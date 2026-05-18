"""Demand-aware staffing agent — naive_rule with smarter staff scheduling.

Staffing rule:
  Baseline: 8
  Mon-Thu:  7  |  Fri-Sat-Sun: 9
  +1 if reputation "Fair" or "Poor"
  -1 if reputation "Excellent"
  -1 if weather contains rain/storm
  +1 if yesterday walkout_band was "Some" or "Many"
  Clamp to [6, 10]

All ordering/happy-hour logic identical to naive_rule.
"""

from __future__ import annotations

from agents.runner import run_game

REORDER_POINT = {
    "Flour": 6.0,
    "Tomato Sauce": 3.0,
    "Mozzarella": 3.0,
    "Fresh Pasta": 4.0,
    "Cream": 2.0,
    "Mushrooms": 2.0,
    "Chicken": 3.0,
    "Lettuce": 2.0,
    "Pepperoni": 2.0,
    "Salmon": 2.0,
}

ORDER_QTY = {
    "Flour": 8.0,
    "Tomato Sauce": 5.0,
    "Mozzarella": 5.0,
    "Fresh Pasta": 8.0,
    "Cream": 5.0,
    "Mushrooms": 5.0,
    "Chicken": 5.0,
    "Lettuce": 5.0,
    "Pepperoni": 5.0,
    "Salmon": 5.0,
}

SLOW_DAYS = {"Monday", "Tuesday", "Wednesday"}

HIGH_DEMAND_DAYS = {"Friday", "Saturday", "Sunday"}
BAD_WEATHER = {"rain", "rainy", "storm", "stormy"}


def _target_staff(observation: dict) -> int:
    dow = observation.get("day_of_week", "")
    rep = observation.get("reputation_band", "Very Good")
    weather = observation.get("weather_today", "").lower()
    ss = observation.get("service_summary") or {}
    walkout_band = ss.get("walkout_band", "None")

    staff = 8
    if dow in HIGH_DEMAND_DAYS:
        staff = 9
    elif dow not in HIGH_DEMAND_DAYS:
        staff = 7

    if rep in ("Fair", "Poor"):
        staff += 1
    elif rep == "Excellent":
        staff -= 1

    if any(w in weather for w in BAD_WEATHER):
        staff -= 1

    if walkout_band in ("Some", "Many"):
        staff += 1

    return max(6, min(10, staff))


def strategy(observation: dict, day: int) -> list[dict]:
    actions: list[dict] = []

    target = _target_staff(observation)
    current = observation.get("staff_level", 8)
    if target != current:
        actions.append({"tool": "set_staff_level", "args": {"level": target}})

    dow = observation.get("day_of_week", "")
    if dow in SLOW_DAYS:
        actions.append({"tool": "run_happy_hour", "args": {}})

    inventory_map = {}
    fresh_stock = {}
    for inv in observation.get("inventory", []):
        inventory_map[inv["ingredient"]] = inv["total_kg"]
        long_life = sum(
            b["quantity_kg"] for b in inv.get("batches", [])
            if b["expires_in_days"] > 1
        )
        fresh_stock[inv["ingredient"]] = long_life

    cheapest: dict[str, tuple[str, float, float]] = {}
    for sup in observation.get("supplier_catalog", []):
        for ingredient, price in sup["ingredients"].items():
            if ingredient not in cheapest or price < cheapest[ingredient][1]:
                cheapest[ingredient] = (sup["name"], price, sup["min_order_kg"])

    pending_qty: dict[str, float] = {}
    for po in observation.get("pending_orders", []):
        pending_qty[po["ingredient"]] = pending_qty.get(po["ingredient"], 0) + po["quantity_kg"]

    cash = observation["cash"]
    reserve = 1500
    budget = cash - reserve
    if budget <= 0:
        return actions
    spent = 0.0

    needs = []
    for ingredient, reorder in REORDER_POINT.items():
        usable = fresh_stock.get(ingredient, 0)
        pending = pending_qty.get(ingredient, 0)
        effective = usable + pending

        if effective < reorder and ingredient in cheapest:
            supplier_name, price, min_order = cheapest[ingredient]
            qty = max(ORDER_QTY.get(ingredient, 5.0), min_order)
            cost = qty * price
            needs.append((cost, ingredient, supplier_name, qty))

    needs.sort()

    for cost, ingredient, supplier_name, qty in needs:
        if spent + cost > budget:
            continue
        actions.append({
            "tool": "place_order",
            "args": {
                "supplier": supplier_name,
                "ingredient": ingredient,
                "quantity_kg": round(qty, 1),
            },
        })
        spent += cost

    return actions


if __name__ == "__main__":
    import os
    base_url = os.getenv("RESTBENCH_URL", "http://localhost:8001")
    for seed in (42, 88, 123):
        print(f"\n{'='*50}")
        print(f"seed={seed}")
        print('='*50)
        result = run_game(strategy, team_name="staffing_fix", scenario="baseline", seed=seed)
