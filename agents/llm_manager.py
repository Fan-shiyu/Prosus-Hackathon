"""LLM manager — revenue-side judgment layer over the deterministic spine.

Handles ONLY: menu selection, price multipliers, happy hour, daily special,
marketing spend. Never touches inventory ordering, supplier selection, or staff.

Call daily_decisions() once per turn (when warranted). On any failure it
returns use_deterministic_fallback=True so the spine keeps working.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import openai

MODEL = os.getenv("AGENT_MODEL", "gpt-4.1-mini")

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


@dataclass
class ValidatedDecisions:
    use_deterministic_fallback: bool
    active_menu: list[str] | None = None
    price_changes: dict[str, float] | None = None
    run_happy_hour: bool | None = None
    daily_special: str | None = None
    marketing_spend: int | None = None
    reasoning: str | None = None


_FALLBACK = ValidatedDecisions(use_deterministic_fallback=True)


def _validate(raw: dict, deterministic_context: dict) -> ValidatedDecisions:
    menu_book = {d["dish"]: d for d in deterministic_context.get("menu_book_summary", [])}
    all_dishes = list(menu_book.keys())
    # Dishes the deterministic planner confirmed are stocked for today
    available_dishes = [d for d in all_dishes if menu_book[d].get("available_to_serve", True)]
    unavailable_dishes = [d for d in all_dishes if not menu_book[d].get("available_to_serve", True)]

    # active_menu: keep only valid dish names (must exist in menu_book)
    raw_menu = raw.get("active_menu") or []
    active_menu = [d for d in raw_menu if d in menu_book]

    # Pad to 5 — prefer available (stocked) dishes first
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

    # price_changes: clamp [0.8, 1.2], drop dishes not in active_menu
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

    # daily_special: must be in active_menu
    daily_special = raw.get("daily_special")
    if daily_special not in active_set:
        daily_special = None

    # marketing_spend: clamp [0, 500]
    try:
        mkt = int(raw.get("marketing_spend") or 0)
    except (TypeError, ValueError):
        mkt = 0
    mkt = max(0, min(500, mkt))

    # run_happy_hour
    hh = bool(raw.get("run_happy_hour", False))

    # reasoning
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


def daily_decisions(observation: dict, deterministic_context: dict) -> ValidatedDecisions:
    """Call LLM once for revenue-side decisions. Returns fallback on any failure."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    api_base = os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL")

    client = openai.OpenAI(
        api_key=api_key,
        base_url=(api_base.rstrip("/") if api_base else "https://api.openai.com/v1"),
        timeout=5.0,
    )

    user_msg = json.dumps(deterministic_context, separators=(",", ":"))

    last_content: str | None = None
    for attempt in range(2):  # one retry on parse failure only
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
                max_tokens=400,
            )
            content = resp.choices[0].message.content.strip()
            last_content = content

            # Strip markdown fences if present
            if content.startswith("```"):
                lines = content.splitlines()
                inner = []
                for line in lines[1:]:
                    if line.strip() == "```":
                        break
                    inner.append(line)
                content = "\n".join(inner).strip()

            raw = json.loads(content)
            return _validate(raw, deterministic_context)

        except json.JSONDecodeError:
            if attempt == 0:
                continue  # single retry
            return _FALLBACK

        except Exception:
            # timeout, API error — no retry
            return _FALLBACK

    return _FALLBACK
