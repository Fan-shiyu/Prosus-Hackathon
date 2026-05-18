"""Offline hidden-regime A/B: HYBRID (rules+gate+LLM) vs LOCKED rules.

Reuses jfam_scenariolab's synthetic streams + closed-loop cash + price-
elasticity model EXACTLY (same baseline reference), but drives the real
agents.jfam_hybrid.decide() — so the LLM actually fires on the gate-open
hidden archetypes. Zero server quota (the live rate budget stays free for
the real JFAM_agents run). Directionally indicative, NOT a server score —
read it for "does the LLM path help or hurt vs the locked rules, and is it
still safe", which is exactly the open question.

  python -m agents.jfam_hybridlab                  # 6 hidden, hybrid vs rules
  python -m agents.jfam_hybridlab health_scare --full
"""
from __future__ import annotations

import copy
import sys

from agents.jfam_core import DEFAULT_PARAMS, core_strategy, dump_state, load_state
from agents.jfam_hybrid import decide
from agents.jfam_scenariolab import (
    BASE_CHECK,
    CAP_COVERS,
    FIXED,
    STAFF_COST,
    VAR_COST,
    SCENARIOS,
    _base_rows,
    _price_mult,
    _read_action,
    _verdict,
)

HIDDEN = ["inflation", "health_scare", "black_swan", "feast_or_famine",
          "premium_pivot", "silent_drift"]


def _run(name: str, use_hybrid: bool, full: bool = False) -> dict:
    spec = SCENARIOS[name]
    mut, elas = spec["mut"], spec["elasticity"]
    obs_seq = [copy.deepcopy(r["obs"]) for r in _base_rows()]
    base_by_dish = {d["name"]: float(d["base_price"])
                    for d in obs_seq[0].get("menu_book", [])}
    notes = ""
    cash = 15000.0
    pm_used = 1.20
    days = []
    bankrupt_day = None
    llm_calls = 0

    for i, o in enumerate(obs_seq):
        day = o.get("day", i + 1)
        mut(o, i)
        o["cash"] = round(cash, 2)
        o["notes"] = notes
        st = load_state(o)
        if use_hybrid:
            actions, st, meta = decide(o, day, st)
            if meta.get("llm_used"):
                llm_calls += 1
        else:
            actions, st = core_strategy(o, day, st, dict(DEFAULT_PARAMS))
        notes = dump_state(st)

        pm = _price_mult(actions, base_by_dish)
        if pm is not None:
            pm_used = pm
        staff = _read_action(actions, "set_staff_level", "level") \
            or st.get("staff") or DEFAULT_PARAMS["staff_base"]
        mkt = _read_action(actions, "set_marketing_spend", "amount", 0.0) or 0.0
        hh = any(a.get("tool") == "run_happy_hour" for a in actions)

        base_covers = (o.get("service_summary") or {}).get("total_covers")
        if not base_covers:
            base_covers = 170
        eff = min(CAP_COVERS, base_covers * (max(pm_used, 0.8) ** -elas))
        cost_infl = 1.0 + 0.05 * (i - 3) if name == "inflation" and i >= 4 \
            else 1.0
        cash += eff * BASE_CHECK * pm_used - (
            FIXED + (staff or 5) * STAFF_COST + mkt + eff * VAR_COST * cost_infl)
        if cash < 0 and bankrupt_day is None:
            bankrupt_day = day
        days.append({"d": day, "pm": pm_used, "staff": staff, "mkt": mkt,
                     "hh": hh, "cash": round(cash), "covers": round(eff)})

    cashes = [r["cash"] for r in days]
    return {"name": name, "elasticity": elas, "bankrupt_day": bankrupt_day,
            "min_cash": min(cashes), "end_cash": cashes[-1],
            "llm_calls": llm_calls, "days": days}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    full = "--full" in sys.argv
    names = args if args else HIDDEN

    print("=" * 84)
    print("HYBRID (rules + confidence-gate + gpt-5.5 override) vs LOCKED rules")
    print("synthetic hidden streams | closed-loop cash + price elasticity")
    print("=" * 84)
    ref = _run("baseline", use_hybrid=False)["end_cash"] or 1.0
    print(f"baseline reference end-cash (rules) = EUR {ref:,.0f}\n")

    print(f"{'scenario':<16} {'elas':>5} {'RULES end':>12} {'HYBRID end':>12} "
          f"{'delta':>10} {'LLM':>4}  verdict(hybrid vs OWN baseline)")
    print("-" * 84)
    for nm in names:
        r = _run(nm, use_hybrid=False)
        h = _run(nm, use_hybrid=True, full=full)
        d = h["end_cash"] - r["end_cash"]
        flag = "BANKRUPT" if h["bankrupt_day"] else ""
        print(f"{nm:<16} {h['elasticity']:>5} {r['end_cash']:>12,.0f} "
              f"{h['end_cash']:>12,.0f} {d:>+10,.0f} {h['llm_calls']:>4}  "
              f"{_verdict(h, ref)} {flag}")
        if full or len(names) == 1:
            print("   day   pm  staff  mkt  hh     cash  covers")
            for x in h["days"]:
                print(f"   {x['d']:>3} {x['pm']:<5} {str(x['staff']):>3} "
                      f"{str(int(x['mkt'])):>5} {'Y' if x['hh'] else '.'} "
                      f"{x['cash']:>8} {x['covers']:>6}")
    print("\nNOTE: indicative (open cash model + adversarial elasticity "
          "hypothesis). delta>0 = LLM path helped vs locked rules on that "
          "synthetic regime; BANKRUPT = the LLM path broke safety (must not "
          "ship).")


if __name__ == "__main__":
    main()
