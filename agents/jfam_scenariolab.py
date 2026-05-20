"""All-10-scenario synthetic lab — what does the LOCKED agent actually DO?

Server-free. The 6 hidden eval scenarios cannot be hit until ~16:00, so this
takes the REAL 30-day observation sequence from traces/baseline_7.jsonl as a
structural template (real supplier catalog / menu / inventory / weather /
delivery cadence) and applies a per-scenario mutator that emulates ONLY the
archetype-defining dynamics (demand, reputation, alerts, supplier prices).

Two things jfam_oodlab.py does NOT model — and they are exactly where the
critical review flagged risk — so they are modelled here:

  * CLOSED-LOOP CASH. The agent's own price/staff/marketing choices feed an
    open cash model (cash_t = cash_{t-1} + revenue - costs). In baseline cash
    grows monotonically so the cash-bleed safe-mode / panic logic NEVER fires;
    only a real declining-cash trajectory exercises the anti-bankruptcy nets.

  * PRICE ELASTICITY. The agent's whole profit edge is "demand is inelastic ->
    slam the 1.20 ceiling", measured on the 4 knowns. Each scenario carries an
    elasticity; covers respond to the agent's chosen price. This directly
    quantifies "what if a hidden scenario's demand is NOT inelastic" — the
    single biggest scenario-overfit exposure.

The cash/elasticity model is an APPROXIMATE open model (the trace's covers
cannot truly close back on the agent). It is directionally indicative — read
it for "does a net fire / does it bleed / does it go bankrupt", not as a
server score. Regime + action trajectory ARE exact (real core_strategy).

  python -m agents.jfam_scenariolab            # all scenarios, summary
  python -m agents.jfam_scenariolab silent_drift --full   # one, day-by-day
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from agents.jfam_core import DEFAULT_PARAMS, core_strategy, dump_state, load_state

TRACE = Path(__file__).resolve().parent / "traces" / "baseline_7.jsonl"

# Calibrated to the REAL baseline_7 trace (15,000 -> ~60,380 cash over 30d):
# realized check ~ €20.7/cover AT the agent's 1.20 price => €17.25/cover at
# base price; variable cost ~ €7/cover absorbs ingredients + lumpy order
# prepay + waste (reconciles the trace's ~+€1,560/day net). 22 tables cap
# realized covers ~ 360 (parties x turns).
BASE_CHECK = 17.25      # per cover at price_mult = 1.00
VAR_COST = 7.0
CAP_COVERS = 360
FIXED = 300.0
STAFF_COST = 120.0


def _base_rows() -> list[dict]:
    return [json.loads(l) for l in TRACE.open()]


def _set_covers(o: dict, mult: float) -> float:
    """Scale the scenario's *latent* demand signal in the observation the
    agent reads (service_summary is yesterday's actuals). Returns the latent
    cover multiplier so the cash model can apply elasticity on top."""
    ss = o.get("service_summary")
    if isinstance(ss, dict) and ss.get("total_covers") is not None:
        ss["total_covers"] = max(0, int(ss["total_covers"] * mult))
        if ss.get("table_utilization_peak") is not None:
            ss["table_utilization_peak"] = min(
                1.0, (ss["table_utilization_peak"] or 0) * mult)
        c = ss["total_covers"]
        ss["walkout_band"] = ("Many" if ss.get("table_utilization_peak", 0)
                              >= 1.0 and c > 300 else
                              "Some" if c > 320 else "None")
    if o.get("yesterday_revenue"):
        o["yesterday_revenue"] = round(o["yesterday_revenue"] * mult, 2)
    return mult


def _inflate_suppliers(o: dict, factor: float) -> None:
    for s in o.get("supplier_catalog", []):
        ings = s.get("ingredients", {})
        for k in list(ings):
            ings[k] = round(float(ings[k]) * factor, 3)


def _degrade_deliveries(o: dict, ratio: float) -> None:
    for h in o.get("delivery_history", []):
        if h.get("ordered_kg"):
            h["delivered_kg"] = round(h["ordered_kg"] * ratio, 1)
            h["on_time"] = False


# --------------------------------------------------------------------------- #
# Per-scenario mutators. Each: (obs, day_idx 0-based) -> latent demand mult.
# `elasticity` is how strongly covers fall as the agent raises price above 1.0
# (covers *= price_mult ** -elasticity). 0.15 ~ the inelastic world the team
# measured on knowns; ~1.2 = the adversarial "elastic hidden" hypothesis.
# --------------------------------------------------------------------------- #

SCENARIOS: dict[str, dict] = {}


def scen(name, elasticity):
    def deco(fn):
        SCENARIOS[name] = {"mut": fn, "elasticity": elasticity}
        return fn
    return deco


@scen("baseline", 0.15)
def baseline(o, i):
    return 1.0                                   # control — unmutated


@scen("renovation", 0.15)
def renovation(o, i):                            # capacity cut, ~2 weeks
    if i == 3:
        o["alerts"] = ["Dining room renovation begins: half the tables "
                       "are unavailable for the next two weeks."]
    if 3 <= i < 17:
        m = _set_covers(o, 0.55)
        ss = o.get("service_summary")
        if isinstance(ss, dict):
            ss["table_utilization_peak"] = 1.0
            ss["walkout_band"] = "Many"
        return m
    return 1.0


@scen("supply_crisis", 0.15)
def supply_crisis(o, i):                         # supplier outage ~10 days
    if i == 5:
        o["alerts"] = ["Major supplier outage: ingredient deliveries are "
                       "disrupted across the region."]
    if 5 <= i < 16:
        _degrade_deliveries(o, 0.30)
    return 1.0


@scen("tourist_season", 0.15)
def tourist_season(o, i):                        # surge then EXPECTED lull
    if i == 4:
        o["alerts"] = ["Tourist season has begun — a festival is drawing "
                       "large crowds to the area."]
    if 4 <= i < 18:
        o["customer_trend"] = "Growing"
        return _set_covers(o, 1.7)
    if 18 <= i < 25:                             # post-festival lull (normal!)
        return _set_covers(o, 0.60)
    return 1.0


@scen("inflation", 0.15)
def inflation(o, i):                             # supplier cost ramp
    if i == 5:
        o["alerts"] = ["Supplier prices are rising sharply due to "
                       "inflation across the supply chain."]
    if i >= 4:
        _inflate_suppliers(o, 1.0 + 0.05 * (i - 3))   # +5%/day, ~+135% by d30
    return 1.0


@scen("health_scare", 1.0)
def health_scare(o, i):                          # contamination -> rep crash
    if i == 7:
        o["alerts"] = ["Local health authority is investigating a "
                       "contamination scare linked to area restaurants."]
    if 7 <= i < 9:
        o["reputation_band"] = "Poor"
        o["customer_trend"] = "Declining"
        return _set_covers(o, 0.30)
    if 9 <= i < 14:
        o["reputation_band"] = "Fair"
        o["customer_trend"] = "Declining"
        return _set_covers(o, 0.45)
    if 14 <= i < 20:
        o["reputation_band"] = "Fair"
        return _set_covers(o, 0.70)
    return 1.0


@scen("black_swan", 1.0)
def black_swan(o, i):                            # sudden UNANNOUNCED crash
    if i >= 11:
        o["customer_trend"] = "Declining"
        o["reputation_band"] = "Fair"            # not Poor -> dodges low_rep
        o["alerts"] = []                          # unkeyworded on purpose
        return _set_covers(o, 0.35)
    return 1.0


@scen("feast_or_famine", 1.0)
def feast_or_famine(o, i):                       # alternating boom / bust
    phase = (i // 7) % 2                          # 0 feast, 1 famine
    if phase == 0:
        o["customer_trend"] = "Growing"
        m = _set_covers(o, 1.6)
        ss = o.get("service_summary")
        if isinstance(ss, dict):
            ss["table_utilization_peak"] = 1.0
            ss["walkout_band"] = "Many"
        return m
    o["customer_trend"] = "Declining"             # famine: rep stays Good
    o["alerts"] = []                              # exogenous, unannounced
    return _set_covers(o, 0.32)


@scen("premium_pivot", 1.2)
def premium_pivot(o, i):                          # gentrification, base churns
    if i == 4:
        o["alerts"] = ["The neighbourhood is gentrifying — the local "
                       "clientele is shifting toward an upscale market."]
    if 5 <= i < 14:                               # transition dip, rep fine
        o["customer_trend"] = "Declining"
        o["reputation_band"] = "Very Good"
        return _set_covers(o, 0.62)
    if i >= 14:                                   # new premium base, smaller
        return _set_covers(o, 0.85)
    return 1.0


@scen("silent_drift", 1.2)
def silent_drift(o, i):                           # slow erosion, NO signal
    o["customer_trend"] = "Stable"                # banded signal hides it
    o["alerts"] = []
    o["reputation_band"] = "Good"
    return _set_covers(o, max(0.30, 0.975 ** i))  # ~ -2.5%/day compounding


# --------------------------------------------------------------------------- #
# Runner — closed-loop cash on top of the real core_strategy.
# --------------------------------------------------------------------------- #


def _read_action(actions, tool, key=None, default=None):
    for a in actions:
        if a.get("tool") == tool:
            return a["args"].get(key) if key else a["args"]
    return default


def _price_mult(actions, base_by_dish):
    for a in actions:
        if a.get("tool") == "set_price":
            d = a["args"]["dish"]
            if base_by_dish.get(d):
                return round(a["args"]["price"] / base_by_dish[d], 3)
    return None


def run(name: str, full: bool = False) -> dict:
    spec = SCENARIOS[name]
    mut, elas = spec["mut"], spec["elasticity"]
    rows = _base_rows()
    obs_seq = [copy.deepcopy(r["obs"]) for r in rows]
    base_by_dish = {d["name"]: float(d["base_price"])
                    for d in obs_seq[0].get("menu_book", [])}

    notes = ""
    cash = 15000.0
    pm_used = 1.20
    days = []
    bankrupt_day = None

    for i, o in enumerate(obs_seq):
        day = o.get("day", i + 1)
        latent = mut(o, i) or 1.0
        # Inject the closed-loop cash the agent must read THIS turn.
        o["cash"] = round(cash, 2)
        o["notes"] = notes

        st = load_state(o)
        actions, st = core_strategy(o, day, st, dict(DEFAULT_PARAMS))
        notes = dump_state(st)

        pm = _price_mult(actions, base_by_dish)
        if pm is not None:
            pm_used = pm
        staff = _read_action(actions, "set_staff_level", "level")
        if staff is None:
            staff = st.get("staff") or DEFAULT_PARAMS["staff_base"]
        mkt = _read_action(actions, "set_marketing_spend", "amount", 0.0) or 0.0
        hh = any(a.get("tool") == "run_happy_hour" for a in actions)
        regime = st.get("regime", "?")
        adverse = bool(st.get("cash_hist") and len(st["cash_hist"]) >
                       DEFAULT_PARAMS["ood_cash_decline_days"]
                       and all(st["cash_hist"][k] > st["cash_hist"][k + 1]
                               for k in range(len(st["cash_hist"]) - 1))
                       and cash < DEFAULT_PARAMS["reserve_days"]
                       * (FIXED + (staff or 5) * STAFF_COST))

        # --- closed-loop cash: covers respond to the agent's price ---------- #
        base_covers = (o.get("service_summary") or {}).get("total_covers")
        if not base_covers:
            base_covers = 170 * latent
        eff_covers = min(CAP_COVERS,
                         base_covers * (max(pm_used, 0.8) ** -elas))
        cost_infl = 1.0
        if name == "inflation" and i >= 4:
            cost_infl = 1.0 + 0.05 * (i - 3)
        revenue = eff_covers * BASE_CHECK * pm_used
        costs = (FIXED + (staff or 5) * STAFF_COST + mkt
                 + eff_covers * VAR_COST * cost_infl)
        cash = cash + revenue - costs
        if cash < 0 and bankrupt_day is None:
            bankrupt_day = day

        days.append({
            "d": day, "regime": regime, "pm": pm_used, "staff": staff,
            "mkt": mkt, "hh": hh, "cash": round(cash),
            "covers": round(eff_covers), "adverse": adverse,
        })

    cashes = [r["cash"] for r in days]
    regimes = sorted({r["regime"] for r in days})
    pms = sorted({r["pm"] for r in days})
    return {
        "name": name, "elasticity": elas, "days": days,
        "bankrupt_day": bankrupt_day, "min_cash": min(cashes),
        "end_cash": cashes[-1], "regimes": regimes, "price_mults": pms,
        "adverse_fired": [r["d"] for r in days if r["adverse"]],
        "full": full,
    }


def _verdict(r: dict, ref: float) -> str:
    """Calibration-robust: each scenario vs the agent's OWN baseline under
    the identical cash model. Answers 'does the locked policy generalise'."""
    e = r["end_cash"]
    pct = 100 * e / ref if ref else 0
    if r["bankrupt_day"]:
        return f"❌ BANKRUPT day {r['bankrupt_day']} (min €{r['min_cash']:,})"
    if e < 15000:
        return (f"❌ ENDS BELOW STARTING CASH €{e:,} ({pct:.0f}% of baseline) "
                "— net-negative month, no net ever fired")
    if pct < 45:
        return (f"⚠️  SEVERE BLEED: end €{e:,} = {pct:.0f}% of baseline "
                "— policy mis-serves this regime")
    if pct < 75:
        return (f"⚠️  underperforms: end €{e:,} = {pct:.0f}% of baseline")
    return f"✅ robust: end €{e:,} = {pct:.0f}% of baseline"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    full = "--full" in sys.argv
    names = args if args else list(SCENARIOS)

    print("=" * 78)
    print("JFAM locked agent (08dfba3 / DEFAULT_PARAMS) vs synthetic scenarios")
    print("closed-loop cash + price elasticity | covers *= price_mult**-elas")
    print("=" * 78)

    # Baseline is the reference the locked agent was tuned on.
    ref = run("baseline").get("end_cash") or 1.0

    summary = []
    for nm in names:
        r = run(nm, full)
        summary.append(r)
        print(f"\n### {nm}   (demand elasticity = {r['elasticity']})")
        print(f"  regimes seen : {', '.join(r['regimes'])}")
        print(f"  price mults  : {r['price_mults']}")
        print(f"  cash-bleed safe-mode fired on days: "
              f"{r['adverse_fired'] or 'never'}")
        print(f"  min cash €{r['min_cash']:,}  |  end cash "
              f"€{r['end_cash']:,}")
        print(f"  VERDICT: {_verdict(r, ref)}")
        if full or len(names) == 1:
            print("  day reg               pm    staff mkt   hh cash    covers adv")
            for d in r["days"]:
                print(f"  {d['d']:>3} {d['regime']:<16} {d['pm']:<5} "
                      f"{str(d['staff']):>3}  {str(int(d['mkt'])):>4} "
                      f"{'Y' if d['hh'] else '.'}  {d['cash']:>7} "
                      f"{d['covers']:>5}  {'A' if d['adverse'] else '.'}")

    print("\n" + "=" * 78)
    print(f"SUMMARY  (baseline reference end cash = €{ref:,.0f})")
    print("=" * 78)
    for r in summary:
        print(f"  {r['name']:<16} {_verdict(r, ref)}")


if __name__ == "__main__":
    main()
