"""Weekend-safe deterministic agent.

Fixes the dominant failure from naive_rule diagnostic:
the cheapest-supplier trap causes complete inventory stockout every
weekend because Italian Imports Co. (cheapest) delivers Wednesday-only
with 3-day lead time — effective 7-day wait when ordered on Wednesday.

Strategy:
- Rank suppliers by effective lead time, not price
- Buffer stock specifically for Saturday + Sunday (zero or limited deliveries)
- Track per-supplier reliability from delivery_history
- Drop dishes from menu when stock will be unavailable in next 2 days
- Restore dishes when stock replenishes
- Persist state (reliability, demand history) in notes field
"""

from __future__ import annotations

import json
import os
from agents.runner import run_game
from agents.regime_detector import detect_regime
from agents.llm_manager import daily_decisions, ValidatedDecisions

# ── constants ────────────────────────────────────────────────────────────────

DOW_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DEFAULT_RELIABILITY = 0.85
CRITICAL_BUFFER = 1.5   # extra multiplier for ingredients used by 3+ dishes
CASH_RESERVE = 1500     # never spend below this


# ── helpers ──────────────────────────────────────────────────────────────────

def _dow_idx(dow: str) -> int:
    return DOW_ORDER.index(dow)


def _eff_lead(today_dow: str, lead_time_days: int, delivery_days: list[str]) -> int:
    """Days from today until earliest possible delivery, respecting delivery schedule."""
    earliest = _dow_idx(today_dow) + lead_time_days
    delivery_idxs = {_dow_idx(d) for d in delivery_days}
    for extra in range(8):
        if (earliest + extra) % 7 in delivery_idxs:
            return lead_time_days + extra
    return lead_time_days + 7  # shouldn't happen


# ── notes_manager ─────────────────────────────────────────────────────────────

def _load_state(obs: dict) -> dict:
    raw = obs.get("notes", "") or ""
    try:
        if raw.strip().startswith("{"):
            return json.loads(raw)
    except Exception:
        pass
    return {}


def _save_notes_action(state: dict) -> dict:
    serialized = json.dumps(state, separators=(",", ":"))
    # Trim history entries until payload fits under 3800 chars
    while len(serialized) > 3800:
        trimmed = False
        if state.get("cov"):
            state["cov"] = state["cov"][1:]
            trimmed = True
        for ing in list(state.get("ing", {}).keys()):
            if state["ing"][ing]:
                state["ing"][ing] = state["ing"][ing][1:]
                trimmed = True
        if not trimmed:
            break
        serialized = json.dumps(state, separators=(",", ":"))
    return {"tool": "save_notes", "args": {"text": serialized[:4000]}}


def _update_state(state: dict, obs: dict) -> dict:
    """Pull new information from today's observation into persisted state."""
    state.setdefault("rel", {})   # supplier -> ingredient -> reliability ratio
    state.setdefault("cov", [])   # daily cover counts, last 14 days
    state.setdefault("ing", {})   # ingredient -> last 7 days usage kg
    state.setdefault("sl", [])    # stockout log [{d, i}]

    # Update supplier reliability via EMA (alpha=0.3) from delivery_history
    for dh in obs.get("delivery_history", []):
        if dh["ordered_kg"] <= 0:
            continue
        sup = dh["supplier"]
        ing = dh["ingredient"]
        ratio = round(dh["delivered_kg"] / dh["ordered_kg"], 3)
        state["rel"].setdefault(sup, {})
        prev = state["rel"][sup].get(ing, DEFAULT_RELIABILITY)
        state["rel"][sup][ing] = round(0.3 * ratio + 0.7 * prev, 2)  # 2dp saves space

    # Record yesterday's covers (keep last 10 only)
    ss = obs.get("service_summary") or {}
    if ss and ss.get("total_covers") is not None:
        state["cov"].append(ss["total_covers"])
        state["cov"] = state["cov"][-10:]

    # Annotate last LLM decision entry with actual outcomes (feedback loop)
    if state.get("ll") and ss and "out" not in state["ll"][-1]:
        state["ll"][-1]["out"] = {
            "cov": ss.get("total_covers", 0),
            "rev": int(ss.get("total_revenue") or 0),
            "wk": ss.get("walkout_band", "None"),
        }

    # Record yesterday's per-ingredient usage (keep last 7 per ingredient)
    if ss and ss.get("dishes_sold"):
        menu_map = {d["name"]: d for d in obs.get("menu_book", [])}
        for dish_name, count in ss["dishes_sold"].items():
            dish = menu_map.get(dish_name)
            if not dish:
                continue
            for req in dish.get("ingredients", []):
                ing = req["ingredient"]
                usage = round(req["quantity_kg"] * count, 2)
                state["ing"].setdefault(ing, [])
                state["ing"][ing].append(usage)
                state["ing"][ing] = state["ing"][ing][-7:]

    # Log stockouts: ingredient at zero with no pending order (keep last 10)
    today_day = obs.get("day", 1)
    inv = {i["ingredient"]: i["total_kg"] for i in obs.get("inventory", [])}
    pending_ings = {po["ingredient"] for po in obs.get("pending_orders", [])}
    for ing, qty in inv.items():
        if qty == 0 and ing not in pending_ings:
            entry = {"d": today_day, "i": ing}
            if entry not in state["sl"]:
                state["sl"].append(entry)
    state["sl"] = state["sl"][-10:]

    return state


# ── demand_estimator ──────────────────────────────────────────────────────────

_BOOTSTRAP_DEFAULTS: dict[str, float] = {
    "Fresh Pasta": 12.0,
    "Tomato Sauce": 10.0,
    "Flour": 8.0,
    "Mozzarella": 6.0,
    "Mushrooms": 5.0,
}
_BOOTSTRAP_DEFAULT_OTHER = 4.0


def _ing_max_usage(state: dict, ingredient: str) -> float:
    """Max single-day usage for ingredient over recorded history.

    Returns ingredient-specific bootstrap estimate before history accumulates.
    """
    hist = state.get("ing", {}).get(ingredient, [])
    if not hist:
        return _BOOTSTRAP_DEFAULTS.get(ingredient, _BOOTSTRAP_DEFAULT_OTHER)
    return max(hist)


# ── supplier_evaluator ────────────────────────────────────────────────────────

def _build_supplier_ranking(obs: dict, state: dict,
                             skip_supplier: str | None = None) -> dict[str, list]:
    """
    Returns {ingredient: [(eff_lead, neg_rel, eff_cost_per_kg, name, price, min_order), ...]}
    sorted by (eff_lead ASC, neg_rel ASC [= rel DESC], eff_cost ASC).
    skip_supplier: ignore this supplier name entirely (used in supplier_crisis mode).
    """
    today_dow = obs.get("day_of_week", "Monday")
    ranked: dict[str, list] = {}

    for sup in obs.get("supplier_catalog", []):
        name = sup["name"]
        if skip_supplier and name == skip_supplier:
            continue
        lead = sup["lead_time_days"]
        delivery_days = sup["delivery_days"]
        eff_lead = _eff_lead(today_dow, lead, delivery_days)

        for ingredient, price in sup.get("ingredients", {}).items():
            rel = state.get("rel", {}).get(name, {}).get(ingredient, DEFAULT_RELIABILITY)
            eff_cost = price / rel
            entry = (eff_lead, -rel, eff_cost, name, price, sup["min_order_kg"])
            ranked.setdefault(ingredient, []).append(entry)

    for ing in ranked:
        ranked[ing].sort()

    return ranked


def _least_reliable_supplier(state: dict) -> str | None:
    """Return name of supplier with lowest average reliability across all ingredients."""
    rel_dict = state.get("rel", {})
    if not rel_dict:
        return None
    worst_sup = min(
        rel_dict.items(),
        key=lambda kv: sum(kv[1].values()) / len(kv[1]) if kv[1] else 1.0,
    )
    return worst_sup[0]


# ── critical_ingredient_filter ────────────────────────────────────────────────

def _critical_ingredients(obs: dict) -> set[str]:
    """Ingredients used by 3 or more active dishes."""
    active = set(obs.get("active_menu", []))
    count: dict[str, int] = {}
    for dish in obs.get("menu_book", []):
        if dish["name"] not in active:
            continue
        for req in dish.get("ingredients", []):
            ing = req["ingredient"]
            count[ing] = count.get(ing, 0) + 1
    return {ing for ing, n in count.items() if n >= 3}


# ── pending helper ────────────────────────────────────────────────────────────

def _pending_arriving_by(obs: dict, ingredient: str, by_day: int, rel: float) -> float:
    """Expected kg of ingredient arriving on or before by_day, discounted by reliability."""
    return sum(
        po["quantity_kg"] * rel
        for po in obs.get("pending_orders", [])
        if po["ingredient"] == ingredient and po["delivery_day"] <= by_day
    )


# ── day-1 front-load ──────────────────────────────────────────────────────────

def _day1_frontload(obs: dict) -> tuple[list[dict], dict[str, float]]:
    """
    On day 1, place a 70 kg Fresh Pasta order from North Sea Millers.
    Returns (actions, ordered_this_turn_map).
    Target: 5 days × 12 kg/day = 60 kg at 0.85 reliability → 70.6 kg ordered.
    North Sea Millers delivers Mon/Wed/Fri with 2-day lead → arrives day 3 (Wed).
    """
    target_qty = 71.0
    for sup in obs.get("supplier_catalog", []):
        if sup["name"] == "North Sea Millers" and "Fresh Pasta" in sup.get("ingredients", {}):
            qty = max(target_qty, sup["min_order_kg"])
            qty = round(qty, 1)
            action = {"tool": "place_order", "args": {
                "supplier": sup["name"],
                "ingredient": "Fresh Pasta",
                "quantity_kg": qty,
            }}
            return [action], {"Fresh Pasta": qty}
    return [], {}


# ── weekend_buffer_planner + inventory_planner ────────────────────────────────

def _plan_orders(obs: dict, state: dict, ranked: dict[str, list],
                 already_ordered: dict[str, float] | None = None,
                 regime: str = "normal") -> list[dict]:
    today_dow = obs.get("day_of_week", "Monday")
    today_day = obs.get("day", 1)
    critical = _critical_ingredients(obs)

    # Regime-based order multiplier applied to days_needed
    # tourist_surge intentionally uses 1.0 — staff-cap-7 is the only lever,
    # order volume stays normal to avoid post-surge waste
    if regime == "renovation_or_capacity":
        regime_mult = 0.6
    elif regime == "supplier_crisis":
        regime_mult = 1.5   # larger safety buffer
    else:
        regime_mult = 1.0

    cash = obs.get("cash", 0.0)
    budget = cash - CASH_RESERVE
    if budget <= 0:
        return []

    # Inventory map
    inv: dict[str, float] = {
        item["ingredient"]: item["total_kg"]
        for item in obs.get("inventory", [])
    }

    # All ingredients used by active dishes
    active = set(obs.get("active_menu", []))
    needed_ings: set[str] = set()
    for dish in obs.get("menu_book", []):
        if dish["name"] not in active:
            continue
        for req in dish.get("ingredients", []):
            needed_ings.add(req["ingredient"])

    orders: list[tuple] = []   # (eff_lead, deficit_cost, ingredient, supplier, qty)

    for ingredient in needed_ings:
        if ingredient not in ranked:
            continue

        eff_lead, neg_rel, eff_cost, sup_name, price, min_order = ranked[ingredient][0]
        rel = -neg_rel

        max_usage = _ing_max_usage(state, ingredient)

        # Required stock depends on day of week
        if today_dow == "Thursday":
            days_needed = 3.0   # Fri + Sat + Sun
        elif today_dow == "Friday":
            days_needed = 2.5   # Sat + Sun (with buffer)
        else:
            days_needed = float(eff_lead + 2)

        # Regime multiplier (applied before critical buffer)
        days_needed *= regime_mult

        # Critical ingredients need extra buffer
        if ingredient in critical:
            days_needed *= CRITICAL_BUFFER

        required = max_usage * days_needed
        deadline_day = today_day + int(days_needed)

        current = inv.get(ingredient, 0.0)
        pending = _pending_arriving_by(obs, ingredient, deadline_day, rel)
        # Also count orders placed earlier this same turn
        this_turn = (already_ordered or {}).get(ingredient, 0.0) * rel
        effective = current + pending + this_turn

        if effective >= required:
            continue

        deficit = required - effective
        order_qty = deficit / rel   # over-order to compensate for expected shortfall
        order_qty = max(order_qty, min_order)
        order_qty = round(order_qty, 1)
        total_cost = order_qty * price

        orders.append((eff_lead, total_cost, ingredient, sup_name, price, min_order, order_qty))

    # Sort: urgency (lowest lead time) first, then cost
    orders.sort(key=lambda x: (x[0], x[1]))

    actions: list[dict] = []
    spent = 0.0

    for eff_lead, total_cost, ingredient, sup_name, price, min_order, qty in orders:
        if spent + total_cost > budget:
            continue
        actions.append({
            "tool": "place_order",
            "args": {
                "supplier": sup_name,
                "ingredient": ingredient,
                "quantity_kg": qty,
            }
        })
        spent += total_cost

    return actions


# ── menu_planner ──────────────────────────────────────────────────────────────

def _top_margin_dishes(obs: dict, n: int) -> list[str]:
    """Return up to n active dishes sorted by base_price descending (proxy for margin)."""
    dishes = sorted(
        obs.get("menu_book", []),
        key=lambda d: d.get("base_price", 0),
        reverse=True,
    )
    return [d["name"] for d in dishes[:n]]


def _plan_menu(obs: dict, regime: str = "normal") -> list[dict]:
    """
    Drop dishes whose ingredients won't cover even 1 serving over next 2 days.
    Re-add dishes that are stocked again.
    Always keep >= 5 dishes.
    """
    today_day = obs.get("day", 1)
    inv: dict[str, float] = {
        item["ingredient"]: item["total_kg"]
        for item in obs.get("inventory", [])
    }

    # Count pending arrivals in next 2 days
    arriving: dict[str, float] = {}
    for po in obs.get("pending_orders", []):
        if po["delivery_day"] <= today_day + 2:
            ing = po["ingredient"]
            arriving[ing] = arriving.get(ing, 0) + po["quantity_kg"] * DEFAULT_RELIABILITY

    usable: dict[str, float] = {
        ing: inv.get(ing, 0) + arriving.get(ing, 0)
        for ing in set(list(inv.keys()) + list(arriving.keys()))
    }

    menu_map = {d["name"]: d for d in obs.get("menu_book", [])}
    current_active = set(obs.get("active_menu", []))
    all_dishes = [d["name"] for d in obs.get("menu_book", [])]

    def _can_serve(dish_name: str) -> bool:
        dish = menu_map.get(dish_name)
        if not dish:
            return False
        for req in dish.get("ingredients", []):
            if usable.get(req["ingredient"], 0) < req["quantity_kg"]:
                return False
        return True

    # Start from currently active menu, drop what's unstockable, add back what's now stocked
    new_active: list[str] = []
    for dish_name in all_dishes:
        if _can_serve(dish_name):
            new_active.append(dish_name)

    # Enforce minimum 5 dishes — add back the best-stocked unavailable dishes
    if len(new_active) < 5:
        for dish_name in all_dishes:
            if dish_name not in new_active and len(new_active) < 5:
                new_active.append(dish_name)

    # In renovation mode, trim to 6 highest-margin dishes to cut waste
    if regime == "renovation_or_capacity":
        top6 = _top_margin_dishes(obs, 6)
        # Keep only servable dishes from top6, fall back to full new_active if needed
        trimmed = [d for d in top6 if d in set(new_active)]
        if len(trimmed) >= 5:
            new_active = trimmed

    new_active_set = set(new_active)
    if new_active_set == current_active:
        return []

    return [{"tool": "set_menu", "args": {"dishes": new_active}}]


# ── staffing_planner ──────────────────────────────────────────────────────────

def _plan_staff(obs: dict, regime: str = "normal") -> list[dict]:
    today_dow = obs.get("day_of_week", "Monday")
    rep = obs.get("reputation_band", "Good")
    cash = obs.get("cash", 0.0)
    current = obs.get("staff_level", 5)

    target = 6 if today_dow in ("Friday", "Saturday") else 5

    # Regime-based staff cap
    regime_caps = {
        "renovation_or_capacity": 4,
        "demand_collapse": 5,
        "tourist_surge": 7,
        "normal": 7,
        "supplier_crisis": 7,
    }
    staff_cap = regime_caps.get(regime, 7)

    # Spend a bit more on service quality when we can afford it and rep needs help
    if cash > 12000 and rep in ("Fair", "Poor"):
        target += 1

    target = max(5, min(staff_cap, target))

    if target != current:
        return [{"tool": "set_staff_level", "args": {"level": target}}]
    return []


# ── marketing_planner (Step 4) ────────────────────────────────────────────────

def _plan_marketing(obs: dict, state: dict, regime: str) -> list[dict]:
    rep = obs.get("reputation_band", "Good")
    dow = obs.get("day_of_week", "")

    consec = state.get("mkt_consec", 0)   # consecutive non-zero marketing days
    forced_zero = state.get("mkt_pause", 0)  # forced-zero days remaining

    if forced_zero > 0:
        state["mkt_pause"] = forced_zero - 1
        state["mkt_consec"] = 0
        return []

    if consec >= 5:
        state["mkt_consec"] = 0
        state["mkt_pause"] = 2
        return []

    # Demand-collapse regime overrides: spend to recover reputation fast
    if regime == "demand_collapse":
        amount = 200
    elif rep == "Poor":
        amount = 250
    elif rep == "Fair":
        amount = 150
    elif rep == "Good" and dow in ("Thursday", "Friday"):
        amount = 100
    else:
        amount = 0

    if amount > 0:
        state["mkt_consec"] = consec + 1
        state["mkt_pause"] = 0
        return [{"tool": "set_marketing_spend", "args": {"amount": amount}}]

    state["mkt_consec"] = 0
    return []


# ── panic mode ───────────────────────────────────────────────────────────────

def _panic_actions(obs: dict) -> list[dict]:
    """If cash < 2000: minimal staff, no big orders, 5 highest-margin dishes."""
    actions: list[dict] = []
    if obs.get("staff_level", 5) != 3:
        actions.append({"tool": "set_staff_level", "args": {"level": 3}})
    top5 = _top_margin_dishes(obs, 5)
    if set(top5) != set(obs.get("active_menu", [])):
        actions.append({"tool": "set_menu", "args": {"dishes": top5}})
    return actions


# ── deterministic context builder (for LLM) ──────────────────────────────────

def _format_ll_history(ll: list[dict]) -> list[str]:
    """Convert raw ll log entries into readable decision→outcome strings for LLM context."""
    lines = []
    for e in ll:
        parts = [f"Day {e['d']}: {e.get('r', '')}"]
        # Decisions made
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
        # Actual outcome (next day)
        if "out" in e:
            out = e["out"]
            parts.append(f"→ result: {out['cov']} covers, €{out['rev']} revenue, walkouts={out.get('wk','?')}")
        else:
            parts.append("→ outcome pending")
        lines.append(" ".join(parts))
    return lines


def _usable_stock(obs: dict) -> dict[str, float]:
    """Current inventory + pending orders arriving within 2 days, reliability-discounted."""
    today_day = obs.get("day", 1)
    inv: dict[str, float] = {
        item["ingredient"]: item["total_kg"] for item in obs.get("inventory", [])
    }
    arriving: dict[str, float] = {}
    for po in obs.get("pending_orders", []):
        if po["delivery_day"] <= today_day + 2:
            ing = po["ingredient"]
            arriving[ing] = arriving.get(ing, 0) + po["quantity_kg"] * DEFAULT_RELIABILITY
    usable = dict(inv)
    for ing, qty in arriving.items():
        usable[ing] = usable.get(ing, 0) + qty
    return usable


def _dish_is_servable(dish: dict, usable: dict[str, float]) -> bool:
    for req in dish.get("ingredients", []):
        if usable.get(req["ingredient"], 0) < req["quantity_kg"]:
            return False
    return True


def _build_deterministic_context(obs: dict, state: dict, regime: str,
                                  regime_just_changed: bool, day: int) -> dict:
    ss = obs.get("service_summary") or {}
    cov = state.get("cov", [])

    # Customer trend: recent-3 vs prior-3
    if len(cov) >= 6:
        recent_avg = sum(cov[-3:]) / 3
        prior_avg = sum(cov[-6:-3]) / 3
        ratio = (recent_avg - prior_avg) / prior_avg if prior_avg > 0 else 0
    else:
        ratio = 0
    customer_trend = "rising" if ratio > 0.1 else "falling" if ratio < -0.1 else "stable"

    # Top 3 dishes sold yesterday
    dishes_sold = ss.get("dishes_sold") or {}
    top_3 = [d for d, _ in sorted(dishes_sold.items(), key=lambda x: x[1], reverse=True)[:3]]

    # Stockout frequency
    stockout_counts: dict[str, int] = {}
    for e in state.get("sl", []):
        ing = e.get("i", "?")
        stockout_counts[ing] = stockout_counts.get(ing, 0) + 1

    # Open questions generated deterministically
    questions: list[str] = []
    for ing, cnt in stockout_counts.items():
        if cnt >= 2:
            questions.append(f"{ing} stocked out {cnt}x recently — reduce menu dependency?")
    if obs.get("reputation_band") != state.get("lrp", obs.get("reputation_band")):
        questions.append(f"Reputation changed to {obs.get('reputation_band')} — adjust strategy?")
    if ss.get("walkout_band", "None") in ("Some", "Many"):
        questions.append(f"Yesterday {ss.get('walkout_band')} walkouts — boost capacity or price down?")

    # Menu book summary — available_to_serve reflects ACTUAL stock (not yesterday's menu)
    usable = _usable_stock(obs)
    menu_book_summary = [
        {
            "dish": d["name"],
            "base_price": d.get("base_price", 0),
            "available_to_serve": _dish_is_servable(d, usable),
        }
        for d in obs.get("menu_book", [])
    ]

    return {
        "regime": regime,
        "regime_just_changed": regime_just_changed,
        "reputation_band": obs.get("reputation_band", "Good"),
        "customer_trend": customer_trend,
        "days_remaining": 31 - day,
        "yesterday_summary": {
            "covers": ss.get("total_covers", 0),
            "revenue": round(float(ss.get("total_revenue") or 0), 0),
            "walkouts_band": ss.get("walkout_band", "None"),
            "top_3_dishes": top_3,
            "stockouts_count": len(stockout_counts),
            "zero_cover": bool(cov and cov[-1] == 0),
            "marketing_spend": obs.get("marketing_spend", 0),
        },
        "active_alerts": list(obs.get("alerts") or []),
        "cash": round(float(obs.get("cash", 0)), 0),
        "expected_overhead_today": (obs.get("staff_level") or 5) * 200,
        "menu_book_summary": menu_book_summary,
        "notes_summary": _format_ll_history(state.get("ll", [])[-3:]),
        "open_questions": questions[:3],
    }


# ── main strategy ─────────────────────────────────────────────────────────────

def strategy(observation: dict, day: int) -> list[dict]:
    actions: list[dict] = []

    # Load and update persisted state
    state = _load_state(observation)
    state = _update_state(state, observation)

    # Panic mode: cash < 2000, skip LLM, pure deterministic
    if observation.get("cash", 0) < 2000:
        actions.extend(_panic_actions(observation))
        actions.append(_save_notes_action(state))
        return actions

    # Yesterday's tracking fields for change detection
    last_regime = state.get("lr", "normal")
    last_rep = state.get("lrp", observation.get("reputation_band", "Good"))
    last_alerts: set[str] = set(state.get("la", []))

    # Detect operating regime
    regime = detect_regime(observation, state)

    # ── Renovation recovery override ──────────────────────────────────────────
    forced_normal = state.get("forced_normal", 0)
    if forced_normal > 0:
        state["forced_normal"] = forced_normal - 1
        regime = "normal"
    elif (state.get("renov_days", 0) >= 3
          and regime == "renovation_or_capacity"
          and len(state.get("cov", [])) >= 2
          and sum(state["cov"][-2:]) / 2 > 50):
        regime = "normal"
        state["forced_normal"] = 5
        state["renov_days"] = 0

    if regime == "renovation_or_capacity":
        state["renov_days"] = state.get("renov_days", 0) + 1
    elif forced_normal == 0:
        state["renov_days"] = 0

    # Change detection for LLM trigger logic
    regime_just_changed = regime != last_regime
    rep_just_changed = observation.get("reputation_band") != last_rep
    current_alerts: set[str] = set(str(a) for a in (observation.get("alerts") or []))
    new_alert = bool(current_alerts - last_alerts)

    # Update yesterday-tracking state (for next turn)
    state["lr"] = regime
    state["lrp"] = observation.get("reputation_band", "Good")
    state["la"] = list(current_alerts)

    # ── Deterministic: ordering + staffing (NEVER overridden by LLM) ─────────

    already_ordered: dict[str, float] = {}
    if day == 1:
        fl_actions, already_ordered = _day1_frontload(observation)
        actions.extend(fl_actions)

    skip_sup = _least_reliable_supplier(state) if regime == "supplier_crisis" else None
    ranked = _build_supplier_ranking(observation, state, skip_supplier=skip_sup)
    actions.extend(_plan_orders(observation, state, ranked, already_ordered, regime))
    actions.extend(_plan_staff(observation, regime))

    # ── LLM call decision ─────────────────────────────────────────────────────

    ss = observation.get("service_summary") or {}
    cov = state.get("cov", [])
    yesterday_zero = bool(cov and cov[-1] == 0)
    walkouts_many = ss.get("walkout_band", "None") == "Many"
    llm_calls = state.get("lc", 0)
    days_since_llm = day - state.get("ld", 0)
    dow = observation.get("day_of_week", "")

    should_call_llm = (
        regime_just_changed
        or rep_just_changed
        or yesterday_zero
        or walkouts_many
        or dow in ("Thursday", "Friday")
        or new_alert
        or days_since_llm >= 3
    )

    # Skip if steady-state renovation (deterministic narrow menu is correct)
    if regime == "renovation_or_capacity" and not regime_just_changed:
        should_call_llm = False

    # Fix B: rate-limit cap raised 20 → 28 (games were hitting the cap at day 20-23,
    # losing LLM judgment in the final days when reputation lock-in matters most).
    if llm_calls > 28:
        should_call_llm = should_call_llm and (regime_just_changed or new_alert)

    # ── Revenue-side actions: LLM or deterministic fallback ───────────────────

    llm_dec: ValidatedDecisions | None = None
    if should_call_llm:
        ctx = _build_deterministic_context(observation, state, regime, regime_just_changed, day)
        llm_dec = daily_decisions(observation, ctx)
        if not llm_dec.use_deterministic_fallback:
            state["lc"] = llm_calls + 1
            state["ld"] = day
            # Build decision+reasoning entry for feedback loop
            entry: dict = {"d": day, "r": (llm_dec.reasoning or "")[:100]}
            if llm_dec.marketing_spend:
                entry["mkt"] = llm_dec.marketing_spend
            if llm_dec.run_happy_hour:
                entry["hh"] = 1
            if llm_dec.daily_special:
                entry["spc"] = llm_dec.daily_special[:20]
            if llm_dec.price_changes:
                avg_m = sum(llm_dec.price_changes.values()) / len(llm_dec.price_changes)
                entry["px"] = "up" if avg_m > 1.04 else ("dn" if avg_m < 0.96 else "~")
            state.setdefault("ll", [])
            state["ll"].append(entry)
            state["ll"] = state["ll"][-5:]

    if llm_dec and not llm_dec.use_deterministic_fallback:
        # LLM menu
        llm_menu = llm_dec.active_menu or []
        if set(llm_menu) != set(observation.get("active_menu", [])):
            actions.append({"tool": "set_menu", "args": {"dishes": llm_menu}})

        # LLM prices (absolute = base × multiplier)
        base_prices = {d["name"]: d.get("base_price", 0) for d in observation.get("menu_book", [])}
        for dish, mult in (llm_dec.price_changes or {}).items():
            base = base_prices.get(dish, 0)
            if base > 0 and abs(mult - 1.0) > 0.01:
                actions.append({"tool": "set_price", "args": {
                    "dish": dish, "price": round(base * mult, 2),
                }})

        # LLM marketing (always emit so 0 overrides any prior spend)
        actions.append({"tool": "set_marketing_spend", "args": {
            "amount": llm_dec.marketing_spend or 0,
        }})

        # Happy hour: LLM decision OR always on slow weekdays (safety net)
        if llm_dec.run_happy_hour or dow in ("Monday", "Tuesday", "Wednesday"):
            actions.append({"tool": "run_happy_hour", "args": {}})

        # LLM daily special
        if llm_dec.daily_special:
            actions.append({"tool": "offer_daily_special", "args": {
                "dish": llm_dec.daily_special,
            }})
    else:
        # Deterministic fallback: menu + marketing + happy hour
        actions.extend(_plan_menu(observation, regime))
        actions.extend(_plan_marketing(observation, state, regime))
        if dow in ("Monday", "Tuesday", "Wednesday"):
            actions.append({"tool": "run_happy_hour", "args": {}})

    # Persist state
    actions.append(_save_notes_action(state))

    return actions


# ── robust game runner (Step 3) ───────────────────────────────────────────────

import time
import httpx as _httpx


def _post_with_retry(client: "_httpx.Client", url: str, json_body: dict,
                     max_retries: int = 3) -> "_httpx.Response":
    """POST with 429-aware retry (sleep 5 s between attempts)."""
    for attempt in range(max_retries):
        try:
            r = client.post(url, json=json_body)
            if r.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(5)
                    continue
            return r
        except Exception as exc:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            raise exc
    raise RuntimeError(f"Failed after {max_retries} attempts: {url}")


def run_game_robust(strat, *, base_url: str, team_name: str,
                    scenario: str, seed: int, verbose: bool = True) -> dict:
    """
    Full game loop with:
    - 429 retry (sleep 5 s, max 3 attempts)
    - try/except around every action submission
    - guarantee end-turn is called even if actions error out
    - panic mode enforced in strategy (cash < 2000 → minimal spend)
    """
    transport = _httpx.HTTPTransport(retries=3)
    with _httpx.Client(base_url=base_url, timeout=60.0, transport=transport) as client:
        r = _post_with_retry(client, "/games", {
            "team_name": team_name,
            "scenario": scenario,
            "seed": seed,
        })
        r.raise_for_status()
        data = r.json()
        game_id = data["game_id"]
        observation = data["observation"]
        day = data["day"]

        if verbose:
            print(f"Game {game_id} — Day {day}, Cash: {observation['cash']}")

        for _turn in range(30):
            # Panic-mode: override order budget when cash < 2000
            try:
                tool_calls = strat(observation, day)
            except Exception as exc:
                print(f"  Day {day}: strategy error: {exc}")
                tool_calls = []

            accepted = rejected = 0
            for tc in tool_calls:
                # Panic budget guard: skip orders > 200 EUR when cash < 2000
                if observation.get("cash", 9999) < 2000 and tc.get("tool") == "place_order":
                    args = tc.get("args", {})
                    # Find price
                    ing = args.get("ingredient", "")
                    qty = args.get("quantity_kg", 0)
                    sup_name = args.get("supplier", "")
                    price = next(
                        (s["ingredients"].get(ing, 0)
                         for s in observation.get("supplier_catalog", [])
                         if s["name"] == sup_name),
                        0,
                    )
                    if price * qty > 200:
                        continue
                try:
                    resp = _post_with_retry(client, f"/games/{game_id}/action", tc)
                    resp.raise_for_status()
                    result = resp.json()
                    if result.get("status") == "accepted":
                        accepted += 1
                    else:
                        rejected += 1
                        if verbose:
                            print(f"  Day {day}: REJECTED {tc['tool']}: {result.get('reason')}")
                except Exception as exc:
                    print(f"  Day {day}: action error ({tc.get('tool')}): {exc}")
                    rejected += 1

            # Always end turn — even if actions errored
            try:
                r2 = _post_with_retry(client, f"/games/{game_id}/end-turn", {})
                r2.raise_for_status()
                turn_data = r2.json()
            except Exception as exc:
                print(f"  Day {day}: end-turn error: {exc}")
                break

            observation = turn_data["observation"]
            day = turn_data["day"]
            status = turn_data["status"]
            dr = turn_data["day_result"]

            if verbose:
                print(
                    f"  Day {day-1}: covers={dr['total_covers']}, "
                    f"rev={dr['total_revenue']:.0f}, "
                    f"cash={observation['cash']:.0f}, "
                    f"ok={accepted} rej={rejected}"
                )

            if status != "in_progress":
                if verbose:
                    print(f"Game ended: {status}")
                break

        r3 = client.get(f"/games/{game_id}/score")
        r3.raise_for_status()
        score_data = r3.json()

        if verbose:
            s = score_data["score"]
            print(f"\nFinal score: {s['total_score']:.0f}")
            print(f"  Net profit: {s['net_profit']:.0f}")
            print(f"  Rep penalty: {s['reputation_penalty']:.0f}")
            print(f"  Walk penalty: {s['walkout_penalty']:.0f}")
            print(f"  Waste penalty: {s['waste_penalty']:.0f}")

        return score_data


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    BASE_URL = os.getenv("RESTBENCH_URL", "http://52.48.183.209:8001")
    SCENARIOS = ["baseline", "supply_crisis", "tourist_season", "renovation"]
    SEEDS = [42, 88, 123]

    results = []
    for scenario in SCENARIOS:
        for seed in SEEDS:
            print(f"\n{'='*55}")
            print(f"scenario={scenario}  seed={seed}")
            print("=" * 55)
            r = run_game_robust(
                strategy,
                base_url=BASE_URL,
                team_name="weekend_safe",
                scenario=scenario,
                seed=seed,
            )
            results.append({
                "scenario": scenario,
                "seed": seed,
                "score": r["score"]["total_score"],
                "days": r["days_survived"],
                "status": r["status"],
            })

    print("\n\n" + "=" * 70)
    print(f"{'Scenario':<18} {'Seed':>5} {'Score':>10} {'Days':>5} {'Status':<12}")
    print("-" * 70)
    for r in results:
        print(
            f"{r['scenario']:<18} {r['seed']:>5} {r['score']:>10.0f} "
            f"{r['days']:>5} {r['status']:<12}"
        )
    avg = sum(r["score"] for r in results) / len(results)
    print("-" * 70)
    print(f"{'Average':>25} {avg:>10.0f}")
