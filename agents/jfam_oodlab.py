"""Offline adversarial harness for the adverse-drift safe-mode (EXP5b).

Synthetic, server-free. Takes the REAL 30-day observation sequence from
traces/baseline_7.jsonl as a structural template, then mutates ONLY the
archetype-defining fields to emulate the hidden scenarios that evade the
named-regime cascade. Runs core_strategy day-by-day twice — safe-mode
ENABLED (defaults) vs effectively DISABLED (huge streak so it never fires)
— and diffs the price/marketing trajectory.

  * Healthy control (unmutated baseline): enabled trajectory MUST be
    byte-identical to disabled  -> offline dormancy pre-check.
  * Adverse mutants: enabled MUST diverge (hold ceiling vs 0.95 cut,
    marketing 0 vs spend) by some day -> safe-mode efficacy.

This validates generalisation to UNSEEN scenarios offline; the live 24-game
known matrix is the authoritative byte-identical regression gate.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from agents.jfam_core import (DEFAULT_PARAMS, core_strategy, dump_state,
                              load_state)

TRACE = Path(__file__).resolve().parent / "traces" / "baseline_7.jsonl"


def _base_obs() -> list[dict]:
    rows = [json.loads(l) for l in TRACE.open()]
    return [copy.deepcopy(r["obs"]) for r in rows]


def _set_covers(o: dict, mult: float) -> None:
    ss = o.get("service_summary")
    if isinstance(ss, dict) and ss.get("total_covers") is not None:
        ss["total_covers"] = max(0, int(ss["total_covers"] * mult))
    if o.get("yesterday_revenue"):
        o["yesterday_revenue"] = round(o["yesterday_revenue"] * mult, 2)


# --- archetype mutators: (obs, day_index 0-based) -> mutate in place -------- #
def healthy(o, i):                       # control — no change
    pass


def silent_drift(o, i):                  # slow erosion, no alert, band Stable
    _set_covers(o, max(0.30, 1.0 - 0.05 * i))
    o["customer_trend"] = "Stable"
    o["alerts"] = []


def famine(o, i):                        # cliff on day 12, stays low
    if i >= 11:
        _set_covers(o, 0.35)
    o["alerts"] = []                     # exogenous, unannounced


def black_swan_demand(o, i):             # sudden crash + reputation hit
    if i >= 9:
        _set_covers(o, 0.40)
        o["reputation_band"] = "Good"    # not Poor -> not low_rep path
    o["alerts"] = []                     # unkeyworded


def premium_dip(o, i):                   # transition dip days 5..12 then up
    if 5 <= i <= 12:
        _set_covers(o, 0.70)
        o["customer_trend"] = "Declining"
    o["alerts"] = []


MUTATORS = {
    "healthy(control)": healthy,
    "silent_drift": silent_drift,
    "famine": famine,
    "black_swan_demand": black_swan_demand,
    "premium_dip": premium_dip,
}


def _price_mult(actions, base_by_dish):
    for a in actions:
        if a["tool"] == "set_price":
            d = a["args"]["dish"]
            if d in base_by_dish and base_by_dish[d]:
                return round(a["args"]["price"] / base_by_dish[d], 3)
    return None


def _mkt(actions):
    for a in actions:
        if a["tool"] == "set_marketing_spend":
            return a["args"]["amount"]
    return None


def run(mut, params) -> list[tuple]:
    obs_seq = _base_obs()
    base_by_dish = {d["name"]: float(d["base_price"])
                    for d in obs_seq[0].get("menu_book", [])}
    notes = ""
    out = []
    for i, o in enumerate(obs_seq):
        day = o.get("day", i + 1)
        mut(o, i)
        o["notes"] = notes
        st = load_state(o)
        actions, st = core_strategy(o, day, st, params)
        notes = dump_state(st)
        pm = _price_mult(actions, base_by_dish)
        out.append((day, pm if pm is not None else st.get("price_mult_used"),
                    _mkt(actions), st.get("adv_streak", 0)))
    return out


def main():
    disabled = dict(DEFAULT_PARAMS)
    disabled["ood_streak"] = 10**9
    disabled["ood_cash_decline_days"] = 10**9   # safe-mode can never fire
    enabled = dict(DEFAULT_PARAMS)

    for name, mut in MUTATORS.items():
        en = run(mut, enabled)
        di = run(mut, disabled)
        diverged_days = [en[k][0] for k in range(len(en))
                         if (en[k][1], en[k][2]) != (di[k][1], di[k][2])]
        identical = not diverged_days
        tag = ("DORMANT (identical)" if identical
               else f"ACTIVE from day {diverged_days[0]} "
                    f"({len(diverged_days)} days differ)")
        print(f"\n=== {name}: {tag} ===")
        # show the first divergence and the end-state
        show = sorted(set([0] + [next((k for k in range(len(en))
                      if en[k][0] == d), 0) for d in diverged_days[:1]]
                      + [len(en) - 1]))
        print("  day  price(off->on)  mkt(off->on)  streak(on)")
        for k in show:
            d, pm_on, mk_on, sk = en[k]
            _, pm_of, mk_of, _ = di[k]
            print(f"  {d:3}   {pm_of}->{pm_on:<6}   "
                  f"{mk_of}->{mk_on:<5}   {sk}")
        if name == "healthy(control)":
            print("  EXPECT: DORMANT  ->",
                  "PASS" if identical else "*** FAIL (would regress) ***")
        else:
            print("  EXPECT: ACTIVE   ->",
                  "PASS" if not identical else "*** FAIL (no protection) ***")


if __name__ == "__main__":
    main()
