"""JFAM core — L1 safety rules + L2 regime detector.

Zero-token, fully deterministic. This is the workhorse: it keeps the restaurant
alive and profitable without any LLM. Every behavioural knob lives in PARAMS so
`jfam_tune.py` can search it by exploiting the sim's determinism for free.

A "strategy" here is `core_strategy(obs, day, state) -> (actions, state)`.
State is a plain dict persisted across turns via the agent's save_notes.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]

# --------------------------------------------------------------------------- #
# .env loader (no dependency) — lets every entrypoint pick up local config.
# --------------------------------------------------------------------------- #


def load_dotenv(path: str | None = None) -> None:
    p = Path(path) if path else Path(__file__).resolve().parents[1] / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# --------------------------------------------------------------------------- #
# Tunable parameters. jfam_params.json (if present) overrides these.
# --------------------------------------------------------------------------- #

DEFAULT_PARAMS: dict = {
    # Pricing: multiplier on base price (clipped to the legal 0.8–1.2 band).
    # Sim demand is relatively price-inelastic and quality penalties have huge
    # headroom (rep/sat ~0), so aggressive pricing dominates when demand is
    # abundant. Verified: 1.20 >> 1.08 on baseline/supply/tourist (+5–7k/cell).
    "price_mult": 1.20,
    # ...and ALSO at the ceiling under a capacity cut (renovation-like). Trace
    # evidence: renovation is table-SUPPLY-bound (util_peak=1.0, walkouts
    # "Many" for ~13 days) — demand vastly exceeds the halved table count, so
    # price is inelastic in the binding regime: every scarce seat should earn
    # the max. The old 1.08 carve-out misdiagnosed scarce SUPPLY as scarce
    # demand and threw away margin on every served renovation cover. (EXP1a)
    "capacity_cut_price_mult": 1.20,
    # Staffing. We were overstaffed for normal demand (idle €120/day/head);
    # base 5 is the validated sweet spot — surge/weekend bonuses still lift it
    # on high-demand days so walkouts stay ~0. (Non-monotonic: 4 understaffs.)
    "staff_base": 5,
    "staff_weekend_bonus": 1,      # Fri/Sat/Sun
    "staff_min": 4,
    "staff_max": 13,
    # Demand bootstrap before real data arrives.
    "base_covers": 95.0,
    "covers_ewma_alpha": 0.5,      # weight on most recent day
    # Inventory: how many days of consumption to keep covered, on top of the
    # delivery gap; and the hard ceiling (in days) to limit spoilage.
    # Waste is a lenient penalty; stockouts are catastrophic -> bias to cover.
    "safety_days": 3.0,
    "coverage_buffer_days": 3.0,   # added to the delivery-cadence gap
    "max_hold_days": 10.0,         # never stock more than this many days' use
    "peak_weight": 0.65,           # size off blend of mean & recent peak demand
    "forecast_safety": 1.20,       # over-provision factor on forecast demand
    # Cash protection.
    "reserve_days": 3.0,           # keep this many days of overhead as reserve
    "panic_reserve_days": 1.5,     # below this -> cut everything discretionary
    # Promotions.
    "slow_days": ["Monday", "Tuesday", "Wednesday"],
    "happy_hour_max_consecutive": 3,
    "marketing_amount": 120.0,
    "marketing_on_weekend": True,
    "marketing_on_declining": True,
    # Spend marketing only when yesterday's table_utilization_peak was at or
    # below this (provable spare capacity to absorb stimulated demand). Below
    # the 0.90 yield threshold with margin -> never markets into a full house.
    "marketing_slack_util": 0.70,
    # Supplier reliability: avoid a supplier whose delivered/ordered ratio
    # drops below this when an alternative exists.
    "reliability_floor": 0.75,
    # Nudge toward suppliers that deliver more often (shorter max gap), so a
    # critical ingredient isn't stranded over a no-delivery weekend.
    "cadence_cost": 0.4,
    # Yield management: when demand exceeds capacity (customers walking due to
    # full tables, not price), raise price toward the legal ceiling — more
    # revenue per cover with ~no extra lost customers. Signal-driven, so it
    # generalises to ANY capacity/surge regime (renovation, tourist, unseen).
    "yield_price_mult": 1.18,
    "yield_util_threshold": 0.90,   # table_utilization_peak
    # Endgame: skip orders arriving later than (last_day - this). 0 = skip only
    # orders that physically cannot arrive before the game ends (zero stockout
    # risk; the order-sizing end-cap handles right-sizing the rest). (EXP2)
    "endgame_order_horizon": 0,
    "price_hysteresis": 0.02,       # don't re-price for tiny mult changes
    # Inflation / cost-shock defence (targets the hidden `inflation` scenario;
    # signal-driven off observed supplier prices so it generalises and stays
    # dormant when costs are stable -> no regression on known scenarios).
    "inflation_cost_trigger": 1.10,  # avg ingredient cost vs early baseline
    "inflation_price_mult": 1.18,    # defend margin within the legal band
    "inflation_reserve_days": 5.0,   # bigger cash buffer vs a cost squeeze
    # Regime adjustments.
    "regime_surge_staff_bonus": 3,
    "regime_quiet_staff_penalty": 2,
    "regime_crisis_extra_safety_days": 3.0,
    "regime_premium_price_mult": 1.16,
    "regime_recovery_price_mult": 0.95,
}


def load_params() -> dict:
    params = dict(DEFAULT_PARAMS)
    pf = Path(__file__).resolve().parent / "jfam_params.json"
    if pf.exists():
        try:
            params.update(json.loads(pf.read_text()))
        except Exception:
            pass
    return params


# --------------------------------------------------------------------------- #
# State (persisted in the game's notes field as JSON).
# --------------------------------------------------------------------------- #


def load_state(obs: dict) -> dict:
    raw = obs.get("notes") or ""
    state: dict = {}
    if raw.strip().startswith("{"):
        try:
            state = json.loads(raw)
        except Exception:
            state = {}
    state.setdefault("cons", {})        # ingredient -> EWMA kg/day consumption
    state.setdefault("dow_covers", {})  # weekday -> EWMA covers
    state.setdefault("rel", {})         # supplier -> [delivered, ordered] totals
    state.setdefault("hh_streak", 0)    # consecutive happy-hour days
    state.setdefault("regime", "normal")
    state.setdefault("priced", False)
    state.setdefault("staff", None)
    return state


def dump_state(state: dict) -> str:
    # Keep well under the 4000-char notes cap.
    txt = json.dumps(state, separators=(",", ":"))
    if len(txt) > 3800:
        state = dict(state)
        state["rel"] = dict(list(state.get("rel", {}).items())[:12])
        txt = json.dumps(state, separators=(",", ":"))
    return txt[:4000]


# --------------------------------------------------------------------------- #
# Parsing helpers.
# --------------------------------------------------------------------------- #


def weekday_of(day: int) -> str:
    return WEEKDAYS[(day - 1) % 7]


def recipes(obs: dict) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    for d in obs.get("menu_book", []):
        out[d["name"]] = [
            (i["ingredient"], float(i["quantity_kg"]))
            for i in d.get("ingredients", [])
        ]
    return out


def usable_stock(obs: dict, min_days_left: int = 1) -> dict[str, float]:
    """kg on hand that won't expire before it can plausibly be used."""
    out: dict[str, float] = {}
    for inv in obs.get("inventory", []):
        good = sum(
            b["quantity_kg"] for b in inv.get("batches", [])
            if b.get("expires_in_days", 0) >= min_days_left
        )
        out[inv["ingredient"]] = good
    return out


def pending_by_arrival(obs: dict) -> list[tuple[int, str, float]]:
    """List of (delivery_day, ingredient, qty)."""
    return [
        (po.get("delivery_day", 10 ** 6), po["ingredient"], po["quantity_kg"])
        for po in obs.get("pending_orders", [])
    ]


def earliest_arrival(day: int, lead: int, delivery_days: list[str]) -> int | None:
    """First absolute day an order placed on `day` could be delivered."""
    if not delivery_days:
        return None
    start = day + max(1, int(lead))
    for d in range(start, start + 14):
        if weekday_of(d) in delivery_days:
            return d
    return None


def delivery_gap_days(day: int, delivery_days: list[str]) -> float:
    """Typical days between consecutive deliveries for this supplier."""
    if not delivery_days:
        return 7.0
    idxs = sorted(WEEKDAYS.index(w) for w in delivery_days if w in WEEKDAYS)
    if len(idxs) <= 1:
        return 7.0
    gaps = [(idxs[(i + 1) % len(idxs)] - idxs[i]) % 7 or 7
            for i in range(len(idxs))]
    return max(gaps)


def supplier_reliability(state: dict, name: str) -> float:
    d, o = state.get("rel", {}).get(name, [0.0, 0.0])
    return d / o if o > 5 else 1.0


def update_reliability(state: dict, obs: dict) -> None:
    rel = state.setdefault("rel", {})
    for h in obs.get("delivery_history", []):
        n = h["supplier"]
        acc = rel.setdefault(n, [0.0, 0.0])
        acc[0] += float(h.get("delivered_kg", 0) or 0)
        acc[1] += float(h.get("ordered_kg", 0) or 0)


def cost_ratio(obs: dict, state: dict) -> float:
    """Current avg cheapest-per-ingredient price vs an early baseline.
    >1 means supplier costs have inflated. Pure observable signal."""
    prices: dict[str, float] = {}
    for s in obs.get("supplier_catalog", []):
        for ing, pr in s.get("ingredients", {}).items():
            pr = float(pr)
            if ing not in prices or pr < prices[ing]:
                prices[ing] = pr
    if not prices:
        return 1.0
    avg = sum(prices.values()) / len(prices)
    base = state.get("cost0")
    if base is None or obs.get("day", 1) <= 2:
        # Lock the baseline in the first couple of (pre-shock) days.
        state["cost0"] = base = avg if base is None else min(base, avg)
    return avg / base if base else 1.0


# --------------------------------------------------------------------------- #
# Consumption estimate.
# --------------------------------------------------------------------------- #


def update_consumption(state: dict, obs: dict, p: dict) -> dict[str, float]:
    """EWMA kg/day per ingredient from yesterday's actual dishes sold."""
    rec = recipes(obs)
    ss = obs.get("service_summary") or {}
    sold = ss.get("dishes_sold") or {}
    a = p["covers_ewma_alpha"]

    if sold:
        today: dict[str, float] = {}
        for dish, qty in sold.items():
            for ing, kg in rec.get(dish, []):
                today[ing] = today.get(ing, 0.0) + qty * kg
        cons = state.setdefault("cons", {})
        for ing, kg in today.items():
            cons[ing] = a * kg + (1 - a) * cons.get(ing, kg)
        covers = float(ss.get("total_covers", 0) or 0)
        if covers > 0:
            dow = obs.get("day_of_week", weekday_of(obs.get("day", 1)))
            dc = state.setdefault("dow_covers", {})
            dc[dow] = a * covers + (1 - a) * dc.get(dow, covers)
            state["cov_ewma"] = a * covers + (1 - a) * state.get(
                "cov_ewma", covers)

    # Track a slowly-decaying per-ingredient peak so we size buffers for
    # demand spikes (e.g. weekend/festival surges), not just the mean.
    if sold:
        pk = state.setdefault("cons_pk", {})
        for ing, kg in today.items():
            pk[ing] = max(kg, pk.get(ing, kg) * 0.88)

    cons = state.get("cons", {})
    if cons:
        pk = state.get("cons_pk", {})
        w = p.get("peak_weight", 0.65)
        return {ing: max(v, w * pk.get(ing, v)) for ing, v in cons.items()}

    # Cold start: spread bootstrap covers across the active menu.
    active = obs.get("active_menu", []) or list(rec.keys())[:5]
    per_dish = p["base_covers"] / max(1, len(active))
    boot: dict[str, float] = {}
    for dish in active:
        for ing, kg in rec.get(dish, []):
            boot[ing] = boot.get(ing, 0.0) + per_dish * kg
    return boot


# --------------------------------------------------------------------------- #
# L2 — regime detection (generalises from observable signals, never sniffs the
# scenario name).
# --------------------------------------------------------------------------- #


_WORDNUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _alert_window_days(text: str) -> int | None:
    """Parse a duration like 'two weeks' / '10 days' from an alert."""
    m = re.search(r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|"
                  r"ten)\s+(week|day)", text)
    if not m:
        return None
    n = int(m.group(1)) if m.group(1).isdigit() else _WORDNUM.get(m.group(1), 1)
    return n * 7 if m.group(2) == "week" else n


def detect_regime(obs: dict, state: dict) -> str:
    alerts = " ".join(obs.get("alerts", []) or []).lower()
    trend = obs.get("customer_trend", "Stable")
    rep = obs.get("reputation_band", "Very Good")
    day = obs.get("day", 1)

    def has(*words):
        return any(w in alerts for w in words)

    # Capacity cuts (renovation-like) are announced ONCE but last for weeks.
    # Persist the regime for the announced window so pricing/staffing stay
    # adjusted on the silent days in between. Generalises to any announced
    # temporary capacity reduction (incl. hidden scenarios).
    if (has("renovation", "tables unavailable", "dining room", "capacity")
            or "renov" in alerts):
        dur = _alert_window_days(alerts) or 14
        state["cap_cut_until"] = max(state.get("cap_cut_until", 0), day + dur)
    if day <= state.get("cap_cut_until", 0):
        state["regime"] = "capacity_cut"
        return "capacity_cut"

    if has("supplier", "outage", "halted", "shortage", "supply"):
        regime = "supply_crisis"
    elif has("festival", "tourist", "surge", "boom", "demand spike", "influx"):
        regime = "demand_surge"
    elif has("inflation", "price", "cost increase", "rising costs"):
        regime = "inflation"
    elif has("health", "scare", "illness", "contamination", "outbreak"):
        regime = "reputation_shock"
    elif has("premium", "upscale", "gentrif", "high-end"):
        regime = "premium"
    elif trend == "Declining" and rep in ("Poor", "Fair"):
        regime = "reputation_shock"
    elif trend == "Declining":
        regime = "soft_demand"
    elif trend == "Growing":
        regime = "demand_surge"
    else:
        regime = "normal"

    state["regime"] = regime
    return regime


# --------------------------------------------------------------------------- #
# Helpers for action building.
# --------------------------------------------------------------------------- #


def _clip_price(base: float, mult: float) -> float:
    lo, hi = base * 0.8, base * 1.2
    price = max(lo, min(hi, base * mult))
    # Nudge strictly inside the band to avoid float-edge rejection.
    price = min(hi - 0.01, max(lo + 0.01, price))
    return round(price, 2)


def daily_overhead(obs: dict) -> float:
    staff = obs.get("staff_level", 8)
    return 300.0 + staff * 120.0


def make_forecaster(state: dict, p: dict):
    """Returns covers_fc(day) using the learned day-of-week demand profile,
    so ordering front-loads ahead of weekend/peak days."""
    dc = state.get("dow_covers", {})
    base = state.get("cov_ewma") or p["base_covers"]
    fs = p.get("forecast_safety", 1.2)

    def covers_fc(d: int) -> float:
        return dc.get(weekday_of(d), base) * fs

    return covers_fc


def required_order(intensity: float, covers_fc, have: float,
                   pend_days: list[tuple[int, float]],
                   day: int, arr: int, end: int) -> float:
    """Smallest qty (arriving on `arr`) that keeps projected stock >= 0 for
    every day in [arr, end], given forecast demand and pending deliveries.

    Adding Q at `arr` lifts every balance from `arr` onward by Q, so
    Q = max(0, -min_{d in [arr,end]} balance_d).
    """
    bal = have
    worst = 0.0
    for d in range(day, end + 1):
        bal += sum(q for dd, q in pend_days if dd == d)
        bal -= intensity * covers_fc(d)
        if d >= arr and bal < worst:
            worst = bal
    return max(0.0, -worst)


# --------------------------------------------------------------------------- #
# L1 — the deterministic policy.
# --------------------------------------------------------------------------- #


def core_strategy(obs: dict, day: int, state: dict,
                   params: dict | None = None) -> tuple[list[dict], dict]:
    p = params or load_params()
    actions: list[dict] = []

    update_reliability(state, obs)
    rate = update_consumption(state, obs, p)
    regime = detect_regime(obs, state)

    cash = float(obs.get("cash", 0))
    rep = obs.get("reputation_band", "Very Good")
    trend = obs.get("customer_trend", "Stable")
    dow = obs.get("day_of_week", weekday_of(day))
    overhead = daily_overhead(obs)

    reserve = p["reserve_days"] * overhead
    panic = cash < p["panic_reserve_days"] * overhead
    low_rep = rep in ("Poor", "Fair")

    # Demand-pressure signal: are we turning customers away on capacity?
    # (walkouts present OR tables near-saturated yesterday). Pure observable
    # signal — no scenario-name sniffing, so it generalises to unseen cases.
    ss = obs.get("service_summary") or {}
    util_peak = ss.get("table_utilization_peak", 0.0) or 0.0
    walk_band = ss.get("walkout_band", "None")
    demand_pressure = (walk_band in ("Some", "Many")
                       or util_peak >= p["yield_util_threshold"])
    recovering = (regime in ("reputation_shock", "soft_demand") or low_rep)

    # Inflation / cost-shock defence (hardens the hidden `inflation` scenario).
    cr = cost_ratio(obs, state)
    inflating = (regime == "inflation"
                 or cr >= p["inflation_cost_trigger"])
    if inflating:
        reserve = max(reserve, p["inflation_reserve_days"] * overhead)

    # ---- Staffing -------------------------------------------------------- #
    staff = p["staff_base"]
    if dow in ("Friday", "Saturday", "Sunday"):
        staff += p["staff_weekend_bonus"]
    if regime == "demand_surge":
        staff += p["regime_surge_staff_bonus"]
    elif regime in ("capacity_cut", "soft_demand"):
        staff -= p["regime_quiet_staff_penalty"]
    elif regime == "reputation_shock":
        staff += 1  # service quality matters most during recovery
    if low_rep:
        staff += 1
    if panic:
        staff = p["staff_min"]
    staff = int(max(p["staff_min"], min(p["staff_max"], round(staff))))
    if state.get("staff") != staff:
        actions.append({"tool": "set_staff_level", "args": {"level": staff}})
        state["staff"] = staff

    # ---- Pricing --------------------------------------------------------- #
    price_mult = p["price_mult"]
    if recovering:
        price_mult = p["regime_recovery_price_mult"]
    elif regime == "capacity_cut":
        # Scarce tables: don't choke demand with a flat-high price; the yield
        # rule below still lifts price on genuinely capacity-bound days.
        price_mult = p["capacity_cut_price_mult"]
    elif regime == "premium":
        price_mult = max(price_mult, p["regime_premium_price_mult"])
    # Yield management overrides upward when capacity-bound with healthy rep:
    # demand already exceeds supply, so a higher price captures margin without
    # losing net customers. Never when recovering reputation.
    if demand_pressure and not recovering:
        price_mult = max(price_mult, p["yield_price_mult"])
    # Pass rising input costs through to menu prices (unless recovering rep).
    if inflating and not recovering:
        price_mult = max(price_mult, p["inflation_price_mult"])
    prev = state.get("price_mult_used")
    changed = prev is None or abs(price_mult - prev) > p["price_hysteresis"]
    if not state.get("priced") or changed:
        for d in obs.get("menu_book", []):
            if d.get("is_active"):
                actions.append({"tool": "set_price", "args": {
                    "dish": d["name"],
                    "price": _clip_price(float(d["base_price"]), price_mult),
                }})
        state["priced"] = True
        state["price_mult_used"] = price_mult

    # ---- Promotions ------------------------------------------------------ #
    active = obs.get("active_menu", [])
    if active and not panic:
        # Daily special: highest-margin active dish (cheap satisfaction).
        mb = {d["name"]: d for d in obs.get("menu_book", [])}
        best = max(active, key=lambda n: mb.get(n, {}).get("current_price", 0))
        actions.append({"tool": "offer_daily_special", "args": {"dish": best}})

    if (not panic and dow in p["slow_days"]
            and state.get("hh_streak", 0) < p["happy_hour_max_consecutive"]
            and regime != "demand_surge" and not demand_pressure):
        actions.append({"tool": "run_happy_hour", "args": {}})
        state["hh_streak"] = state.get("hh_streak", 0) + 1
    else:
        state["hh_streak"] = 0

    # Marketing. At the 1.20 ceiling (~90% margin, fixed-dominated costs) a
    # marketing-driven cover is highly profitable — but ONLY where there is
    # spare table capacity to seat it. Blanket daily spend backfired hard on
    # high-demand seeds (it pushed demand past the 22-table ceiling -> walkouts
    # -> reputation/cohort damage: seed-88 baseline/supply/renov all -5..-11k).
    # So spend is gated to days with PROVABLE slack — low utilisation
    # yesterday, zero walkouts, not already growing, and NOT in a surge /
    # capacity-cut / supply-crisis regime (in a supply crisis low util means
    # "couldn't serve — no ingredients", not spare capacity, so paying to
    # stimulate demand we can't fulfil just burns cash). Pure observable
    # signal, self-limiting, so it generalises: it stays dormant exactly
    # when stimulated demand could not be profitably served. (EXP4b)
    mkt = 0.0
    if (not panic and not low_rep and regime != "reputation_shock"
            and regime not in ("demand_surge", "capacity_cut",
                                "supply_crisis")
            and not demand_pressure):
        slack = (ss and walk_band == "None" and trend != "Growing"
                 and util_peak <= p["marketing_slack_util"])
        if slack or (p["marketing_on_declining"] and trend == "Declining"):
            mkt = p["marketing_amount"]
    actions.append({"tool": "set_marketing_spend", "args": {"amount": mkt}})

    # ---- Inventory ordering --------------------------------------------- #
    active_set = set(active)
    rec = recipes(obs)
    needed_ings = {
        ing for dish in active_set for ing, _ in rec.get(dish, [])
    } or set(rate.keys())

    # Suppliers per ingredient.
    sup_for: dict[str, list[dict]] = {}
    for s in obs.get("supplier_catalog", []):
        for ing, price in s.get("ingredients", {}).items():
            sup_for.setdefault(ing, []).append({
                "name": s["name"], "price": float(price),
                "lead": int(s.get("lead_time_days", 1)),
                "ddays": s.get("delivery_days", []),
                "min": float(s.get("min_order_kg", 0)),
            })

    stock = usable_stock(obs, min_days_left=1)
    pend = pending_by_arrival(obs)

    extra_safety = (p["regime_crisis_extra_safety_days"]
                    if regime == "supply_crisis" else 0.0)

    budget = max(0.0, cash - reserve)
    if panic:
        budget = max(0.0, cash - p["panic_reserve_days"] * overhead)

    covers_fc = make_forecaster(state, p)
    cons_mean = state.get("cons", {})
    cov_base = state.get("cov_ewma") or p["base_covers"]
    cadence_extra = int(round(p["safety_days"] + p["coverage_buffer_days"]
                              + extra_safety))
    # Endgame: the sim ends on `last_day`; inventory arriving after it (or
    # sized for service days that don't exist) is pure sunk cash + waste with
    # zero revenue upside. Trace showed ~280 kg ordered on day 30 alone, all
    # delivered after the game. Self-gating (only ever binds near the end), so
    # no early-game effect and no scenario-name sniffing. (EXP2)
    last_day = day + int(obs.get("days_remaining", max(0, 30 - day)))

    orders: list[tuple[float, dict]] = []
    for ing in sorted(needed_ings):
        # Per-cover intensity (kg per customer) from the mean EWMA; falls
        # back to the cold-start bootstrap rate.
        if cons_mean.get(ing):
            intensity = cons_mean[ing] / max(1.0, cov_base)
        else:
            intensity = rate.get(ing, 0.0) / max(1.0, p["base_covers"])
        if intensity <= 1e-9:
            continue
        cands = sup_for.get(ing)
        if not cands:
            continue

        # Cheapest sufficiently-reliable supplier, nudged toward frequent
        # delivery cadence, that can actually deliver.
        scored = []
        for c in cands:
            arr = earliest_arrival(day, c["lead"], c["ddays"])
            if arr is None:
                continue
            relv = supplier_reliability(state, c["name"])
            penalty = 0.0 if relv >= p["reliability_floor"] else 1e6
            eff = (c["price"] + penalty
                   + p["cadence_cost"] * delivery_gap_days(day, c["ddays"]))
            scored.append((eff, arr, c))
        if not scored:
            continue
        scored.sort(key=lambda x: (x[0], x[1]))
        _, arr, chosen = scored[0]

        # Won't be delivered before the game ends -> 100% waste, skip it.
        if arr > last_day - p["endgame_order_horizon"]:
            continue

        have = stock.get(ing, 0.0)
        pend_days = [(dd, q) for dd, i, q in pend if i == ing]
        # Bridge to the next feasible delivery after this one, plus safety —
        # but never size for service days beyond the game's end.
        end = min(last_day,
                  arr + int(round(delivery_gap_days(day, chosen["ddays"])))
                  + cadence_extra)
        need = required_order(intensity, covers_fc, have, pend_days,
                              day, arr, end)
        if need <= 1e-6:
            continue

        # Spoilage ceiling: don't hold more than max_hold_days of forecast use.
        cap = sum(intensity * covers_fc(d)
                  for d in range(arr, arr + int(p["max_hold_days"]
                                                + extra_safety) + 1))
        qty = min(max(need, chosen["min"]), max(cap, chosen["min"]))
        cost = qty * chosen["price"]
        incoming_soon = sum(q for dd, q in pend_days if dd <= arr)
        # Urgency: forecast days of cover left before this lands.
        rate_now = max(1e-6, intensity * covers_fc(day))
        days_left = (have + incoming_soon) / rate_now
        orders.append((days_left, {
            "ing": ing, "supplier": chosen["name"],
            "qty": round(qty, 1), "cost": cost,
        }))

    orders.sort(key=lambda x: x[0])  # most urgent first
    spent = 0.0
    for _, o in orders:
        if spent + o["cost"] > budget:
            continue
        actions.append({"tool": "place_order", "args": {
            "supplier": o["supplier"],
            "ingredient": o["ing"],
            "quantity_kg": o["qty"],
        }})
        spent += o["cost"]

    return actions, state
