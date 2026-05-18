"""JFAM L3 — sparing LLM judgment layer over the deterministic core.

Design principles:
- FIRES RARELY: day 1, a regime change, a new alert, weekly cadence, or a
  reputation drop. Not every day. ~5-8 calls/game, not 30.
- BOUNDED OUTPUT: the LLM may only nudge a few clamped strategic knobs. It
  never emits raw tool calls, so it cannot produce an illegal/ruinous action.
  The deterministic core remains the supervisor.
- METERED: every call's token usage is appended to traces/llm_usage.jsonl
  and accumulated in state, because the proxy budget is opaque.
- FAIL-SAFE: any error returns the core's actions unchanged.

Wired to the hackathon LiteLLM proxy via the OpenAI SDK (base_url + key from
env). Enable with JFAM_LLM_OFF=0.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

USAGE_LOG = Path(__file__).resolve().parent / "traces" / "llm_usage.jsonl"

SYSTEM = """You are the strategic advisor for an autonomous agent running an \
Italian restaurant. A deterministic rule engine already handles ordering, \
staffing, pricing and promotions safely. Your job: given the situation, \
propose small bounded ADJUSTMENTS only. Respond with ONLY a JSON object, no \
prose:
{"price_mult": <0.85-1.20>, "marketing": <0-500>, "staff_bias": <-3..3>, \
"happy_hour": <true|false>, "reason": "<=12 words"}
Guidance: raise price_mult only with strong reputation & demand; cut it to \
recover reputation or in soft demand; marketing helps in soft/declining \
demand, wasted during surges you can't serve; staff_bias up for surges/long \
waits, down to save cash when quiet; happy_hour to fill slow days, not during \
a surge. Be conservative; the rules are already good."""


def _client():
    from openai import OpenAI
    # Defaults to OpenAI directly; set LITELLM_BASE_URL for the hackathon proxy.
    base = os.getenv("LITELLM_BASE_URL") or "https://api.openai.com/v1"
    key = os.getenv("LITELLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("LITELLM_API_KEY / OPENAI_API_KEY not set")
    return OpenAI(api_key=key, base_url=base, timeout=20.0)


def _resolve_model() -> str:
    """Proxy wants `openai/<model>`; api.openai.com wants the bare name."""
    model = os.getenv("AGENT_MODEL", "openai/gpt-4.1-mini")
    base = os.getenv("LITELLM_BASE_URL") or "https://api.openai.com/v1"
    if "api.openai.com" in base and model.startswith("openai/"):
        model = model.split("/", 1)[1]
    return model


def _should_fire(obs: dict, day: int, state: dict) -> bool:
    if day == 1:
        return True
    if day % 7 == 1:                              # weekly re-plan
        return True
    if state.get("regime") != state.get("llm_regime"):
        return True
    seen = set(state.get("llm_alerts", []))
    if set(obs.get("alerts", []) or []) - seen:   # a new alert appeared
        return True
    if obs.get("reputation_band") in ("Poor", "Fair") \
            and not state.get("llm_rep_seen"):
        return True
    return False


def _summary(obs: dict, day: int, state: dict) -> str:
    inv = []
    cons = state.get("cons", {})
    for i in obs.get("inventory", []):
        ing = i["ingredient"]
        rate = cons.get(ing, 0) or 0.0
        days = round(i.get("total_kg", 0) / rate, 1) if rate > 0.01 else 99
        inv.append(f"{ing}:{days}d")
    ss = obs.get("service_summary") or {}
    return json.dumps({
        "day": day, "dow": obs.get("day_of_week"),
        "cash": round(obs.get("cash", 0)),
        "rev_y": round(obs.get("yesterday_revenue", 0)),
        "cost_y": round(obs.get("yesterday_total_costs", 0)),
        "reputation": obs.get("reputation_band"),
        "trend": obs.get("customer_trend"),
        "walkouts": ss.get("walkout_band"),
        "covers_y": ss.get("total_covers"),
        "regime": state.get("regime"),
        "alerts": obs.get("alerts", []),
        "stockouts_y": ss.get("dishes_unavailable_at", {}),
        "stock_days": inv[:12],
        "price_mult_now": state.get("price_mult_used"),
        "staff_now": state.get("staff"),
    }, separators=(",", ":"))


def _meter(model: str, usage, state: dict) -> None:
    pin = getattr(usage, "prompt_tokens", 0) or 0
    pout = getattr(usage, "completion_tokens", 0) or 0
    acc = state.setdefault("llm_tok", [0, 0, 0])
    acc[0] += pin
    acc[1] += pout
    acc[2] += 1
    try:
        USAGE_LOG.parent.mkdir(exist_ok=True)
        with USAGE_LOG.open("a") as fh:
            fh.write(json.dumps({"t": time.time(), "model": model,
                                 "in": pin, "out": pout}) + "\n")
    except Exception:
        pass


def _apply(obs: dict, actions: list[dict], adj: dict) -> list[dict]:
    """Fold validated knob deltas into the core's action list."""
    out = [a for a in actions
           if a["tool"] not in ("set_marketing_spend", "run_happy_hour")]

    pm = adj.get("price_mult")
    if isinstance(pm, (int, float)):
        pm = max(0.8, min(1.2, float(pm)))
        priced = {a["args"]["dish"] for a in out if a["tool"] == "set_price"}
        for d in obs.get("menu_book", []):
            if d.get("is_active") and d["name"] not in priced:
                base = float(d["base_price"])
                price = round(min(base * 1.2 - 0.01,
                                  max(base * 0.8 + 0.01, base * pm)), 2)
                out.append({"tool": "set_price",
                            "args": {"dish": d["name"], "price": price}})
        for a in out:
            if a["tool"] == "set_price":
                d = next((x for x in obs.get("menu_book", [])
                          if x["name"] == a["args"]["dish"]), None)
                if d:
                    base = float(d["base_price"])
                    a["args"]["price"] = round(
                        min(base * 1.2 - 0.01,
                            max(base * 0.8 + 0.01, base * pm)), 2)

    mk = adj.get("marketing")
    if isinstance(mk, (int, float)):
        out.append({"tool": "set_marketing_spend",
                    "args": {"amount": max(0.0, min(500.0, float(mk)))}})

    sb = adj.get("staff_bias")
    if isinstance(sb, (int, float)) and int(sb) != 0:
        for a in out:
            if a["tool"] == "set_staff_level":
                a["args"]["level"] = int(max(3, min(
                    15, a["args"]["level"] + int(sb))))

    if adj.get("happy_hour") is True:
        out.append({"tool": "run_happy_hour", "args": {}})

    return out


def refine(obs: dict, day: int, actions: list[dict],
           state: dict) -> tuple[list[dict], dict]:
    if not _should_fire(obs, day, state):
        return actions, state

    model = _resolve_model()
    try:
        client = _client()
        for attempt in range(2):
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user",
                           "content": _summary(obs, day, state)}],
                temperature=0.2,
                max_tokens=120,
            )
            _meter(model, resp.usage, state)
            txt = (resp.choices[0].message.content or "").strip()
            if txt.startswith("```"):
                txt = txt.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            try:
                adj = json.loads(txt)
                break
            except Exception:
                adj = None
        if not isinstance(adj, dict):
            return actions, state

        actions = _apply(obs, actions, adj)
        state["llm_regime"] = state.get("regime")
        state["llm_alerts"] = list(obs.get("alerts", []) or [])
        state["llm_last_day"] = day
        if obs.get("reputation_band") in ("Poor", "Fair"):
            state["llm_rep_seen"] = True
        return actions, state
    except Exception as e:
        print(f"  L3 error (day {day}): {e}")
        return actions, state
