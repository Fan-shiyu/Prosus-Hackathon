"""JFAM HYBRID — rules-floor + confidence-gated LLM override + hard veto.

This is the ONLY feasible form of "use both rules and LLM, keep the best":
the selection happens INSIDE one agent, per-turn, BEFORE submitting — not on
the server (scoring is LATEST-per-cell, and hidden scenarios get one try, so
"run both and keep the better submission" is impossible).

Design (research-backed — see CLAUDE.md session note):

  * RULES ARE THE FLOOR. jfam_core.core_strategy runs every turn, unchanged.
    It is the proven, locked policy (08dfba3). Its action set is the default.

  * CONFIDENCE-GATED DEFERRAL (ReDAct pattern, inverted). The LLM is consulted
    ONLY when the rule core is in a situation it was NOT validated on — i.e.
    a regime that PROVABLY never occurs on the 4 known scenarios:
        customer_trend != "Stable"  OR  reputation in {Poor,Fair}
        OR an alert is present that detect_regime's keywords do NOT map.
    On all 4 knowns trend is ALWAYS "Stable", reputation healthy, and alerts
    map — so the gate NEVER opens there ⇒ this agent is BYTE-IDENTICAL to the
    locked rules agent on every known cell (proven by the dormancy gate).
    The LLM gets its shot exactly and only on genuinely novel hidden regimes.

  * RULE-TRAJECTORY ANCHOR + COGNITIVE REFLECTION (AIM-Bench mitigations).
    When consulted, the LLM is shown the rule core's proposed actions and
    regime call as a strong in-context anchor and must reason before it may
    propose a BOUNDED deviation.

  * DETERMINISTIC HARD VETO. The LLM may only nudge price_mult / staff /
    marketing / happy_hour / daily_special within legal, bankruptcy-safe
    bounds. Ordering & menu stay 100% on the proven core math. So the
    worst case is a small bounded knob change in a regime the rules don't
    specifically optimise anyway. Any LLM error ⇒ pure rules (never crash).

NEVER run under team JFAM_agents (LATEST-per-cell would overwrite banked
cells). __main__ hard-defaults to a throwaway team.

  python -m agents.jfam_hybrid health_scare 7          # single game
  JFAM_HYBRID_SC=1 python -m agents.jfam_hybrid baseline 42   # +self-consist
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from agents.jfam_core import (
    DEFAULT_PARAMS,
    core_strategy,
    dump_state,
    load_dotenv,
    load_state,
)

load_dotenv()

from agents.runner import run_game  # noqa: E402

USAGE_LOG = Path(__file__).resolve().parent / "traces" / "llm_usage.jsonl"
SELF_CONSIST = os.getenv("JFAM_HYBRID_SC", "0") == "1"
# Min LLM self-reported confidence to accept a deviation over the rule
# default. Higher -> the LLM acts less often -> closer to pure rules (safer).
MIN_CONF = float(os.getenv("JFAM_HYBRID_MINCONF", "0.55"))

VALID_TOOLS = {
    "place_order", "set_menu", "set_price", "set_staff_level",
    "set_marketing_spend", "run_happy_hour", "offer_daily_special",
    "save_notes",
}

# Alert keyword groups detect_regime maps. An alert containing NONE of these
# is "unmapped" -> the rule core has no specific response -> defer to the LLM.
_MAPPED = (
    "renovation", "tables unavailable", "dining room", "capacity", "renov",
    "supplier", "outage", "halted", "shortage", "supply",
    "festival", "tourist", "surge", "boom", "demand spike", "influx",
    "inflation", "price", "cost increase", "rising costs",
    "health", "scare", "illness", "contamination", "outbreak",
    "premium", "upscale", "gentrif", "high-end",
)

SYSTEM = """You are the senior operations strategist for an autonomous agent \
running a 22-table Italian restaurant. A PROVEN deterministic rule engine \
already runs the restaurant safely and is near-optimal on every situation it \
was designed for. You are consulted ONLY because the situation is OUTSIDE \
that validated envelope (an unusual demand/reputation trend or an unmapped \
alert) — the rules may be sub-optimal here, OR they may still be right.

Economics you must respect: ~90% gross margin per cover; costs are \
fixed-dominated; on the known regimes demand was measured to be fairly \
price-INELASTIC and a SHRINKING menu sharply cut demand. But you are now \
off-distribution, so reason from first principles about THIS situation, do \
not blindly assume inelasticity.

Method (do this, it mitigates known LLM inventory biases):
1. REFLECT: in <=40 words, what is actually happening and why might the \
rule default be wrong or right here?
2. Then decide a BOUNDED adjustment to the rule default. Only deviate with a \
concrete mechanism. If unsure, match the rule default (return its values).

You may ONLY set these knobs (everything else stays on the safe rule core):
{"reflection":"<=40 words",
 "price_mult": <0.8-1.2>, "staff_level": <3-15 int>,
 "marketing": <0-500>, "happy_hour": <true|false>,
 "daily_special": "<exact active dish name or null>",
 "confidence": <0.0-1.0 that your deviation beats the rule default>}
Respond with ONLY that JSON object, no prose, no markdown fence."""


def _client():
    from openai import OpenAI
    base = os.getenv("LITELLM_BASE_URL") or "https://api.openai.com/v1"
    key = os.getenv("LITELLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("LITELLM_API_KEY / OPENAI_API_KEY not set")
    return OpenAI(api_key=key, base_url=base, timeout=40.0)


def _model() -> str:
    m = os.getenv("AGENT_MODEL", "openai/gpt-4.1-mini")
    base = os.getenv("LITELLM_BASE_URL") or "https://api.openai.com/v1"
    if "api.openai.com" in base and m.startswith("openai/"):
        m = m.split("/", 1)[1]
    return m


def _meter(model: str, usage, state: dict) -> None:
    pin = getattr(usage, "prompt_tokens", 0) or 0
    pout = getattr(usage, "completion_tokens", 0) or 0
    acc = state.setdefault("hyb_tok", [0, 0, 0])
    acc[0] += pin
    acc[1] += pout
    acc[2] += 1
    try:
        USAGE_LOG.parent.mkdir(exist_ok=True)
        with USAGE_LOG.open("a") as fh:
            fh.write(json.dumps({"t": time.time(), "model": model, "in": pin,
                                 "out": pout, "agent": "hybrid"}) + "\n")
    except Exception:
        pass


def _gate_open(obs: dict, state: dict) -> str | None:
    """Return a reason string if the rule core is OUTSIDE its validated
    envelope (defer to LLM), else None. Provably never true on the 4 knowns
    (trend always Stable, reputation healthy, alerts all mapped)."""
    trend = obs.get("customer_trend", "Stable")
    rep = obs.get("reputation_band", "Very Good")
    if trend != "Stable":
        return f"customer_trend={trend}"
    if rep in ("Poor", "Fair"):
        return f"reputation={rep}"
    alerts = obs.get("alerts", []) or []
    if alerts:
        txt = " ".join(alerts).lower()
        if not any(k in txt for k in _MAPPED):
            return "unmapped_alert"
    return None


def _rule_price_mult(core_acts: list[dict], obs: dict) -> float | None:
    mb = {d["name"]: float(d["base_price"]) for d in obs.get("menu_book", [])}
    for a in core_acts:
        if a["tool"] == "set_price":
            b = mb.get(a["args"]["dish"])
            if b:
                return round(a["args"]["price"] / b, 3)
    return None


def _rule_knob(core_acts: list[dict], tool: str, key: str, default):
    for a in core_acts:
        if a["tool"] == tool:
            return a["args"].get(key, default)
    return default


def _digest(obs: dict, day: int, state: dict, anchor: dict) -> str:
    ss = obs.get("service_summary") or {}
    return json.dumps({
        "day": day, "dow": obs.get("day_of_week"),
        "days_remaining": obs.get("days_remaining"),
        "cash": round(obs.get("cash", 0)),
        "reputation": obs.get("reputation_band"),
        "trend": obs.get("customer_trend"),
        "alerts": obs.get("alerts", []),
        "regime_detected": state.get("regime"),
        "weather": obs.get("weather_today"),
        "forecast": obs.get("weather_forecast"),
        "yesterday": {"covers": ss.get("total_covers"),
                      "revenue": obs.get("yesterday_revenue"),
                      "walkouts": ss.get("walkout_band"),
                      "util_peak": ss.get("table_utilization_peak"),
                      "stockouts": ss.get("dishes_unavailable_at", {})},
        "cash_trend": state.get("cash_hist", [])[-6:],
        "RULE_DEFAULT_anchor": anchor,   # the proven safe action to anchor on
        "active_menu": obs.get("active_menu", []),
    }, separators=(",", ":"), default=str)


def _ask(obs: dict, day: int, state: dict, anchor: dict) -> dict | None:
    model = _model()
    samples = []
    n = 2 if SELF_CONSIST else 1
    # gpt-5.x / reasoning models reject temperature!=1 and want
    # max_completion_tokens; older models want max_tokens. Be model-agnostic:
    # omit temperature (default 1 still gives sampling diversity for SC) and
    # try the new token param first, fall back to the old one.
    msgs = [{"role": "system", "content": SYSTEM}]
    try:
        client = _client()
        for _ in range(n):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=msgs + [{"role": "user",
                                      "content": _digest(obs, day, state,
                                                         anchor)}],
                    max_completion_tokens=2000,
                )
            except Exception:
                resp = client.chat.completions.create(
                    model=model,
                    messages=msgs + [{"role": "user",
                                      "content": _digest(obs, day, state,
                                                         anchor)}],
                    max_tokens=2000,
                )
            _meter(model, resp.usage, state)
            txt = (resp.choices[0].message.content or "").strip()
            if txt.startswith("```"):
                txt = txt.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                samples.append(json.loads(txt))
            except Exception:
                continue
    except Exception as e:
        print(f"  hybrid LLM error (day {day}): {e}")
        return None
    if not samples:
        return None
    if len(samples) == 1:
        return samples[0]
    # Self-consistency: only accept a deviation both samples roughly agree on;
    # otherwise fall back to the rule anchor (conservative).
    a, b = samples[0], samples[1]
    if abs(float(a.get("price_mult", anchor["price_mult"]))
           - float(b.get("price_mult", anchor["price_mult"]))) > 0.05:
        return {**anchor, "reflection": "samples disagreed -> rule default",
                "confidence": 0.0}
    return min(samples, key=lambda s: float(s.get("confidence", 0)))


def _apply(obs: dict, core_acts: list[dict], adj: dict,
           anchor: dict) -> list[dict]:
    """Replace ONLY the demand-side knobs in the core action set with the
    (clamped) LLM values. Ordering / menu / safety stay on the core."""
    conf = float(adj.get("confidence", 0) or 0)
    if conf < MIN_CONF:                    # not confident enough -> keep rules
        return core_acts

    out = [a for a in core_acts if a["tool"] not in (
        "set_price", "set_staff_level", "set_marketing_spend",
        "run_happy_hour", "offer_daily_special")]

    pm = adj.get("price_mult", anchor["price_mult"])
    try:
        pm = max(0.8, min(1.2, float(pm)))
    except Exception:
        pm = anchor["price_mult"]
    for d in obs.get("menu_book", []):
        if d.get("is_active"):
            base = float(d["base_price"])
            price = round(min(base * 1.2 - 0.01,
                              max(base * 0.8 + 0.01, base * pm)), 2)
            out.append({"tool": "set_price",
                        "args": {"dish": d["name"], "price": price}})

    sl = adj.get("staff_level", anchor["staff"])
    try:
        sl = int(max(3, min(15, round(float(sl)))))
    except Exception:
        sl = anchor["staff"]
    out.append({"tool": "set_staff_level", "args": {"level": sl}})

    mk = adj.get("marketing", anchor["marketing"])
    try:
        mk = max(0.0, min(500.0, float(mk)))
    except Exception:
        mk = anchor["marketing"]
    out.append({"tool": "set_marketing_spend", "args": {"amount": mk}})

    if adj.get("happy_hour") is True:
        out.append({"tool": "run_happy_hour", "args": {}})

    sp = adj.get("daily_special")
    active = set(obs.get("active_menu", []))
    if isinstance(sp, str) and sp in active:
        out.append({"tool": "offer_daily_special", "args": {"dish": sp}})
    elif anchor.get("special") in active:
        out.append({"tool": "offer_daily_special",
                    "args": {"dish": anchor["special"]}})

    # Hard anti-bankruptcy veto: if the LLM's spend would drop cash below a
    # 1.5-day overhead floor, revert to the rule default for this turn.
    cash = float(obs.get("cash", 0))
    overhead = 300.0 + sl * 120.0
    if cash < 1.5 * overhead:
        return core_acts
    return out


def _sane(actions: list[dict]) -> list[dict]:
    return [a for a in actions
            if isinstance(a, dict) and a.get("tool") in VALID_TOOLS
            and isinstance(a.get("args", {}), dict)]


def decide(obs: dict, day: int, state: dict) -> tuple[list[dict], dict, dict]:
    """Pure hybrid decision: rules floor -> confidence gate -> bounded LLM
    override -> hard veto. Returns (core_acts, state, meta). Reused by both
    the live strategy and the offline hidden-regime lab so they are identical.
    """
    meta = {"gate": None, "llm_used": False, "conf": None}
    core_acts, state = core_strategy(obs, day, state, dict(DEFAULT_PARAMS))

    reason = _gate_open(obs, state)
    meta["gate"] = reason
    if reason is not None:
        anchor = {
            "price_mult": _rule_price_mult(core_acts, obs) or 1.20,
            "staff": _rule_knob(core_acts, "set_staff_level",
                                "level", state.get("staff") or 5),
            "marketing": _rule_knob(core_acts, "set_marketing_spend",
                                    "amount", 0.0),
            "special": _rule_knob(core_acts, "offer_daily_special",
                                  "dish", None),
            "happy_hour": any(a["tool"] == "run_happy_hour"
                              for a in core_acts),
            "regime": state.get("regime"),
        }
        adj = _ask(obs, day, state, anchor)
        if adj is not None:
            before = list(core_acts)
            core_acts = _apply(obs, core_acts, adj, anchor)
            meta["llm_used"] = core_acts is not before
            meta["conf"] = adj.get("confidence")
            state.setdefault("hyb_log", []).append(
                {"d": day, "why": reason, "conf": adj.get("confidence"),
                 "refl": (adj.get("reflection") or "")[:120]})
    return core_acts, state, meta


def make_strategy():
    def strategy(obs: dict, day: int) -> list[dict]:
        try:
            state = load_state(obs)
            core_acts, state, _ = decide(obs, day, state)
            acts = _sane(core_acts)
            acts.append({"tool": "save_notes",
                         "args": {"text": dump_state(state)}})
            return acts
        except Exception as e:
            print(f"  hybrid error (day {day}): {e}")
            return []

    return strategy


strategy = make_strategy()


if __name__ == "__main__":
    scenario = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42
    team = os.getenv("JFAM_HYBRID_TEAM", "JFAM_hybtest")
    if team == "JFAM_agents":
        sys.exit("refusing to run the experiment under JFAM_agents")
    print(f"[hybrid] {scenario} seed={seed} team={team} model={_model()} "
          f"self_consist={SELF_CONSIST}")
    run_game(make_strategy(), team_name=team, scenario=scenario, seed=seed)
