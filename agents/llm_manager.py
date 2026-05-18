"""LLM manager — revenue-side judgment layer over the deterministic spine.

Handles ONLY: menu selection, price multipliers, happy hour, daily special,
marketing spend. Never touches inventory ordering, supplier selection, or staff.

Call daily_decisions() once per turn (when warranted). On any failure it
returns use_deterministic_fallback=True so the spine keeps working.

Call weekly_self_review() on days 10, 15, 20, 25 after other actions.
On any failure it writes "(review failed)" to state and continues normally.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import openai

MODEL = os.getenv("AGENT_MODEL", "")  # empty = auto-probe at first call
_MODEL_RESOLVED: str | None = None
CANDIDATE_MODELS = ["openai/gpt-5", "openai/gpt-5-mini", "openai/gpt-4o", "gpt-4.1-mini"]


def _resolve_model() -> str:
    global _MODEL_RESOLVED
    if _MODEL_RESOLVED:
        return _MODEL_RESOLVED
    if MODEL:
        _MODEL_RESOLVED = MODEL
        return _MODEL_RESOLVED
    client = _make_client(timeout=4.0)
    for candidate in CANDIDATE_MODELS:
        try:
            client.chat.completions.create(
                model=candidate,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            _MODEL_RESOLVED = candidate
            print(f"[llm_manager] using model: {candidate}")
            return candidate
        except Exception:
            continue
    _MODEL_RESOLVED = "gpt-4.1-mini"
    return _MODEL_RESOLVED


SYSTEM_PROMPT = """\
You are the revenue manager for an Italian restaurant simulation. A separate \
deterministic system handles inventory ordering, supplier selection, and staffing \
— you do NOT control those.

Your job is ONLY to optimize revenue-side decisions for today.

IMPORTANT: The context includes notes_summary — your own past decisions and their \
actual outcomes. Learn from them: if you raised prices and covers dropped, be more \
cautious; if happy hour boosted covers, repeat it in similar conditions. If an \
outcome is "pending", you haven't seen results yet.

You receive a JSON context. Respond with ONLY a valid JSON object — no markdown, \
no explanation:
{
  "active_menu": ["dish1", ...],
  "price_changes": {"dish": 1.10},
  "run_happy_hour": false,
  "daily_special": "dish" or null,
  "marketing_spend": 0,
  "reasoning": "under 200 chars"
}

Rules:
- active_menu: 5-8 dishes. Use exact names from menu_book_summary. Prefer \
available_to_serve=true dishes. Drop dishes whose ingredients stock out often.
- price_changes: only dishes where you deviate from 1.0. Clamp 0.8-1.2. \
tourist_surge → raise high-margin ~1.1. demand_collapse → drop ~0.9. \
renovation_or_capacity → neutral. Learn from notes_summary: if prior price \
raises correlated with cover drops, scale back.
- run_happy_hour: true on Mon-Wed (slow days) or when covers are falling. \
Check notes_summary to see if happy hour worked last time.
- daily_special: best available high-margin dish. null if uncertain.
- marketing_spend: 0 normally. 100-150 for mild reputation dip or Thu/Fri. \
200-300 for demand_collapse or Fair rep. 300-500 for Poor rep + falling covers. \
Avoid spending if notes_summary shows prior marketing had no cover uplift.
- reasoning: one sentence ≤200 chars, reference notes_summary learnings if relevant."""

_REVIEW_PROMPT = """\
You are reviewing decisions you made over the last 5 days for an Italian restaurant. \
Your goal is to identify ONE specific pattern you missed and ONE concrete change to \
make starting tomorrow.

Past 5 days:
{summaries}

Current regime: {regime}
Current reputation: {rep_band}

Be honest. If your past decisions look correct and outcomes are positive, return \
override=null. Only suggest an override if there is a clear pattern of underperformance.

Return JSON only (no markdown):
{{
  "insight": "one sentence under 200 chars",
  "override": {{
    "field": "one of: marketing_floor, force_run_happy_hour, force_no_happy_hour, price_floor_dish, always_include_dish_in_menu",
    "value": "int for marketing_floor (0-300), float for price_floor_dish (0.9-1.1), true for others",
    "dish": "exact dish name if field is price_floor_dish or always_include_dish_in_menu"
  }},
  "override_duration_days": 3
}}

Set override to null if no correction is needed."""

_REGIME_HINTS: dict[str, str] = {
    "supplier_crisis": (
        "Strategy: Switch to most reliable suppliers fast. Protect reputation by starting "
        "with a small menu (5 dishes) and growing it back as supply stabilizes. Don't try "
        "to grow revenue during crisis — defend reputation."
    ),
    "tourist_surge": (
        "Strategy: Short-term exploitation. During the 3-day surge window, raise prices on "
        "high-margin dishes (Grilled Salmon, Mushroom Risotto, Spaghetti Carbonara) up to "
        "1.15-1.2x. Offer the highest-margin dish as daily special. Rebalance to base "
        "prices when surge ends."
    ),
    "renovation_or_capacity": (
        "Strategy: Capacity-constrained. Do NOT spend on marketing — extra customers become "
        "walkouts. Smaller menu is fine. Focus on serving the customers you can serve well."
    ),
    "demand_collapse": (
        "Strategy: Recovery mode. Cut high-margin prices by 0.05-0.10 to encourage return "
        "visits. Run happy hour. Marketing spend 200 EUR. Offer daily special to drive "
        "satisfaction."
    ),
    "normal": (
        "Strategy: Steady operations. Raise prices on high-margin dishes by 0.05-0.10 when "
        "reputation is Good or better. Marketing only on slow weekdays (Mon-Wed)."
    ),
}

ALLOWED_OVERRIDE_FIELDS = frozenset({
    "marketing_floor",
    "force_run_happy_hour",
    "force_no_happy_hour",
    "price_floor_dish",
    "always_include_dish_in_menu",
})


@dataclass
class ValidatedDecisions:
    use_deterministic_fallback: bool
    active_menu: list[str] | None = None
    price_changes: dict[str, float] | None = None
    run_happy_hour: bool | None = None
    daily_special: str | None = None
    marketing_spend: int | None = None
    reasoning: str | None = None


@dataclass
class SelfReviewResult:
    insight: str
    override_field: str | None = None
    override_value: object = None
    override_dish: str | None = None
    override_duration_days: int = 3


_FALLBACK = ValidatedDecisions(use_deterministic_fallback=True)
_FAILED_REVIEW = SelfReviewResult(insight="(review failed)")


def _make_client(timeout: float = 5.0) -> openai.OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "")
    api_base = os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL")
    return openai.OpenAI(
        api_key=api_key,
        base_url=(api_base.rstrip("/") if api_base else "https://api.openai.com/v1"),
        timeout=timeout,
    )


def _strip_fences(content: str) -> str:
    if content.startswith("```"):
        lines = content.splitlines()
        inner = []
        for line in lines[1:]:
            if line.strip() == "```":
                break
            inner.append(line)
        return "\n".join(inner).strip()
    return content


def _validate(raw: dict, deterministic_context: dict) -> ValidatedDecisions:
    menu_book = {d["dish"]: d for d in deterministic_context.get("menu_book_summary", [])}
    all_dishes = list(menu_book.keys())
    available_dishes = [d for d in all_dishes if menu_book[d].get("available_to_serve", True)]
    unavailable_dishes = [d for d in all_dishes if not menu_book[d].get("available_to_serve", True)]

    raw_menu = raw.get("active_menu") or []
    active_menu = [d for d in raw_menu if d in menu_book]

    if len(active_menu) < 5:
        extras_avail = sorted(
            [d for d in available_dishes if d not in active_menu],
            key=lambda d: menu_book[d].get("base_price", 0), reverse=True,
        )
        active_menu.extend(extras_avail[: 5 - len(active_menu)])
    if len(active_menu) < 5:
        extras_unavail = sorted(
            [d for d in unavailable_dishes if d not in active_menu],
            key=lambda d: menu_book[d].get("base_price", 0), reverse=True,
        )
        active_menu.extend(extras_unavail[: 5 - len(active_menu)])
    active_menu = active_menu[:8]
    active_set = set(active_menu)

    raw_pc = raw.get("price_changes") or {}
    price_changes: dict[str, float] = {}
    for dish, mult in raw_pc.items():
        if dish not in active_set:
            continue
        try:
            mult = float(mult)
        except (TypeError, ValueError):
            continue
        price_changes[dish] = round(max(0.8, min(1.2, mult)), 3)

    daily_special = raw.get("daily_special")
    if daily_special not in active_set:
        daily_special = None

    try:
        mkt = int(raw.get("marketing_spend") or 0)
    except (TypeError, ValueError):
        mkt = 0
    mkt = max(0, min(500, mkt))

    hh = bool(raw.get("run_happy_hour", False))
    reasoning = str(raw.get("reasoning") or "")[:200]

    return ValidatedDecisions(
        use_deterministic_fallback=False,
        active_menu=active_menu,
        price_changes=price_changes,
        run_happy_hour=hh,
        daily_special=daily_special,
        marketing_spend=mkt,
        reasoning=reasoning,
    )


def _validate_review(raw: dict, deterministic_context: dict) -> SelfReviewResult:
    insight = str(raw.get("insight") or "")
    if not insight or len(insight) > 300:
        insight = "(invalid)"
    else:
        insight = insight[:200]

    menu_book = {d["dish"]: d for d in deterministic_context.get("menu_book_summary", [])}

    override = raw.get("override")
    override_field = None
    override_value = None
    override_dish = None

    if override and isinstance(override, dict):
        field = override.get("field")
        if field in ALLOWED_OVERRIDE_FIELDS:
            value = override.get("value")
            dish = str(override.get("dish") or "")

            valid = True
            if field == "marketing_floor":
                try:
                    value = int(value)
                    if not (0 <= value <= 300):
                        valid = False
                except (TypeError, ValueError):
                    valid = False
            elif field == "price_floor_dish":
                try:
                    value = float(value)
                    if not (0.9 <= value <= 1.1):
                        valid = False
                except (TypeError, ValueError):
                    valid = False
                if dish not in menu_book:
                    valid = False
            elif field == "always_include_dish_in_menu":
                if dish not in menu_book:
                    valid = False
                value = True
            elif field in ("force_run_happy_hour", "force_no_happy_hour"):
                value = True

            if valid:
                override_field = field
                override_value = value
                override_dish = dish if dish in menu_book else None

    try:
        duration = int(raw.get("override_duration_days", 3))
    except (TypeError, ValueError):
        duration = 3
    duration = max(1, min(5, duration))

    return SelfReviewResult(
        insight=insight,
        override_field=override_field,
        override_value=override_value,
        override_dish=override_dish,
        override_duration_days=duration,
    )


def _apply_overrides(dec: ValidatedDecisions, overrides: list[dict],
                     deterministic_context: dict) -> ValidatedDecisions:
    """Apply active overrides on top of LLM decisions. State keys: f=field, v=value, di=dish."""
    if not overrides:
        return dec
    menu_book = {d["dish"]: d for d in deterministic_context.get("menu_book_summary", [])}

    for ov in overrides:
        field = ov.get("f")
        value = ov.get("v")
        dish = ov.get("di")

        if field == "marketing_floor" and dec.marketing_spend is not None:
            try:
                dec.marketing_spend = max(dec.marketing_spend, int(value))
            except (TypeError, ValueError):
                pass
        elif field == "force_run_happy_hour":
            dec.run_happy_hour = True
        elif field == "force_no_happy_hour":
            dec.run_happy_hour = False
        elif field == "price_floor_dish" and dish:
            if dec.price_changes is not None:
                try:
                    floor_val = float(value)
                    current = dec.price_changes.get(dish, 1.0)
                    dec.price_changes[dish] = max(floor_val, current)
                except (TypeError, ValueError):
                    pass
        elif field == "always_include_dish_in_menu" and dish:
            if dec.active_menu is not None and dish not in dec.active_menu and dish in menu_book:
                # Swap out the lowest-base-price dish
                if dec.active_menu:
                    lowest = min(dec.active_menu,
                                 key=lambda d: menu_book.get(d, {}).get("base_price", 0))
                    dec.active_menu = [d for d in dec.active_menu if d != lowest] + [dish]
                else:
                    dec.active_menu = [dish]

    return dec


def _fmt_ll_for_review(ll: list[dict]) -> str:
    """Format last 5 LLM log entries as readable decision→outcome summary."""
    lines = []
    for e in ll[-5:]:
        parts = [f"Day {e['d']}: {e.get('r', '')}"]
        dec_parts = []
        if e.get("px"):
            dec_parts.append(f"prices={'raised' if e['px']=='up' else ('cut' if e['px']=='dn' else 'neutral')}")
        if e.get("mkt"):
            dec_parts.append(f"marketing=€{e['mkt']}")
        if e.get("hh"):
            dec_parts.append("happy_hour=yes")
        if e.get("spc"):
            dec_parts.append(f"special={e['spc']}")
        if dec_parts:
            parts.append(f"[{', '.join(dec_parts)}]")
        if "out" in e:
            out = e["out"]
            parts.append(f"→ {out['cov']} covers, €{out['rev']}, walkouts={out.get('wk','?')}")
        else:
            parts.append("→ pending")
        lines.append(" ".join(parts))
    return "\n".join(lines) if lines else "No LLM decision history yet."


def daily_decisions(observation: dict, deterministic_context: dict,
                    active_overrides: list[dict] | None = None) -> ValidatedDecisions:
    """Call LLM once for revenue-side decisions. Returns fallback on any failure.

    active_overrides: compact override entries from state["ao"] to apply on top of decisions.
    """
    client = _make_client(timeout=5.0)
    user_msg = json.dumps(deterministic_context, separators=(",", ":"))

    regime = deterministic_context.get("regime", "normal")
    hint = _REGIME_HINTS.get(regime, "")
    system_content = SYSTEM_PROMPT + ("\n\n" + hint if hint else "")

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=_resolve_model(),
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
                max_tokens=400,
            )
            content = _strip_fences(resp.choices[0].message.content.strip())
            raw = json.loads(content)
            result = _validate(raw, deterministic_context)
            if active_overrides:
                result = _apply_overrides(result, active_overrides, deterministic_context)
            return result

        except json.JSONDecodeError:
            if attempt == 0:
                continue
            return _FALLBACK

        except Exception:
            return _FALLBACK

    return _FALLBACK


def weekly_self_review(observation: dict, deterministic_context: dict,
                       state: dict) -> SelfReviewResult:
    """Adversarial self-review: examine last 5 days, propose one corrective override.

    Updates state["srl"] (review log) and state["ao"] (active overrides).
    Safe: never raises, never breaks the game loop.
    Skip call entirely if cash < 4000 (caller's responsibility).
    """
    try:
        client = _make_client(timeout=8.0)

        summaries = _fmt_ll_for_review(state.get("ll", []))
        regime = deterministic_context.get("regime", "normal")
        rep_band = deterministic_context.get("reputation_band", "Good")

        prompt = _REVIEW_PROMPT.format(
            summaries=summaries,
            regime=regime,
            rep_band=rep_band,
        )

        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
        )
        content = _strip_fences(resp.choices[0].message.content.strip())
        raw = json.loads(content)
        result = _validate_review(raw, deterministic_context)

        # Persist review log (last 5)
        state.setdefault("srl", [])
        state["srl"].append({"d": observation.get("day", 0), "ins": result.insight[:100]})
        state["srl"] = state["srl"][-5:]

        # Add override to active_overrides if valid
        if result.override_field:
            state.setdefault("ao", [])
            if len(state["ao"]) >= 3:
                state["ao"] = state["ao"][1:]  # drop oldest
            ov_entry: dict = {
                "f": result.override_field,
                "v": result.override_value,
                "dr": result.override_duration_days,
            }
            if result.override_dish:
                ov_entry["di"] = result.override_dish
            state["ao"].append(ov_entry)

        return result

    except Exception:
        state.setdefault("srl", [])
        state["srl"].append({"d": observation.get("day", 0), "ins": "(review failed)"})
        state["srl"] = state["srl"][-5:]
        return _FAILED_REVIEW
