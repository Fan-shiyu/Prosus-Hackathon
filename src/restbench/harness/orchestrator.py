"""Day-loop orchestrator: validate → apply → simulate → account → advance.

This is the single entry point for advancing the simulation one day.
It has no knowledge of HTTP or sessions — the API layer calls it.
"""

from __future__ import annotations

import logging

from restbench.core.rng import SimRNG
from restbench.core.types import (
    AgentAction,
    DayServiceResult,
    InventoryBatch,
    PendingOrder,
    DeliveryRecord,
    StaffMember,
    WorldState,
)
from restbench.engine.cohorts import compute_cohort_modifier, compute_customer_trend, update_cohorts
from restbench.engine.guardrails import overstock_penalty, supplier_concentration_penalty
from restbench.engine.reputation import generate_reviews, update_reputation
from restbench.engine.service import age_inventory, process_spoilage, simulate_day
from restbench.engine.suppliers import advance_supplier_states, effective_reliability
from restbench.engine.tuning import TuningConfig
from restbench.harness.validator import ValidatedAction, validate_action

logger = logging.getLogger(__name__)

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _next_delivery_day(
    earliest_ready: int,
    current_dow: str,
    current_day: int,
    delivery_days: list[str],
) -> int:
    """Find the first day >= earliest_ready that matches the supplier's delivery schedule."""
    if not delivery_days:
        return earliest_ready
    dow_idx = DAYS_OF_WEEK.index(current_dow)
    for offset in range(earliest_ready - current_day, earliest_ready - current_day + 8):
        candidate_day = current_day + offset
        candidate_dow = DAYS_OF_WEEK[(dow_idx + offset) % 7]
        if candidate_day >= earliest_ready and candidate_dow in delivery_days:
            return candidate_day
    return earliest_ready


def run_day(
    world: WorldState,
    rng: SimRNG,
    tuning: TuningConfig,
    action: AgentAction,
) -> DayServiceResult:
    """Execute one complete day. Returns the service result for this day."""

    validated = validate_action(action, world, tuning)

    _apply_action(validated, world, tuning)

    advance_supplier_states(world, rng, tuning.supplier_states)

    _process_deliveries(world, rng, tuning)

    waste_kg, waste_cost = process_spoilage(world)
    world.waste_today_kg = waste_kg
    world.waste_today_cost = waste_cost

    expected_daily = _estimate_daily_consumption(world)
    overstock_penalty(world.inventory, expected_daily, tuning.simulation.overstock_threshold)

    result = simulate_day(world, rng, tuning)

    age_inventory(world)
    world.last_service_result = result

    generate_reviews(world, rng, tuning.reputation, result.satisfaction_scores, result.total_walkouts)
    update_reputation(world, tuning.reputation)

    c = world.cohorts
    world.previous_cohort_total = c.regulars + c.occasionals + c.prospects
    update_cohorts(world, tuning.cohorts, result.mean_satisfaction, result.total_walkouts)

    _end_of_day_accounting(world, result, tuning, waste_cost)

    _generate_weather(world, rng, tuning)

    _advance_day(world, tuning)

    return result


def _apply_action(validated: ValidatedAction, world: WorldState, tuning: TuningConfig) -> None:
    ingredient_spend = 0.0
    for order in validated.orders:
        supplier = next(s for s in world.suppliers if s.name == order.supplier)
        price = next(
            i.price_per_kg for i in supplier.ingredients
            if i.ingredient == order.ingredient
        )
        cost = order.quantity_kg * price
        world.cash -= cost
        ingredient_spend += cost

        earliest_ready = world.day + supplier.lead_time_days
        delivery_day = _next_delivery_day(earliest_ready, world.day_of_week, world.day, supplier.delivery_days)

        world.pending_orders.append(PendingOrder(
            supplier=order.supplier,
            ingredient=order.ingredient,
            quantity_kg=order.quantity_kg,
            cost_per_kg=price,
            order_day=world.day,
            delivery_day=delivery_day,
        ))

    world.ingredient_spend_today = ingredient_spend

    if validated.set_active_menu is not None:
        for dish in validated.set_active_menu:
            if dish not in world.active_menu:
                world.menu_dish_added_day[dish] = world.day
        world.active_menu = validated.set_active_menu

    if validated.set_prices is not None:
        world.prices.update(validated.set_prices)

    if validated.set_staff_level is not None:
        _adjust_staff(world, validated.set_staff_level, tuning)

    if validated.marketing_spend is not None:
        world.marketing_spend = validated.marketing_spend

    if validated.offer_daily_special is not None:
        world.promotions.daily_special = validated.offer_daily_special

    world.promotions.happy_hour = validated.run_happy_hour

    world.agent_notes = validated.notes


def _adjust_staff(world: WorldState, target: int, tuning: TuningConfig) -> None:
    current = len(world.staff)
    if target > current:
        for _ in range(target - current):
            world.staff.append(StaffMember(skill_level=0.5))
    elif target < current:
        world.staff = sorted(world.staff, key=lambda s: s.skill_level, reverse=True)[:target]
    world.staff_level = target


def _estimate_daily_consumption(world: WorldState) -> dict[str, float]:
    """Estimate daily ingredient consumption from yesterday's dishes sold."""
    recipe_map = {r.name: r for r in world.recipes}
    consumption: dict[str, float] = {}
    dishes_sold = {}
    if world.last_service_result:
        dishes_sold = world.last_service_result.dishes_sold

    if not dishes_sold:
        for recipe in world.recipes:
            if recipe.name in world.active_menu:
                for ri in recipe.ingredients:
                    consumption[ri.ingredient] = consumption.get(ri.ingredient, 0.0) + ri.quantity_kg * 5.0
        return consumption

    for dish_name, count in dishes_sold.items():
        recipe = recipe_map.get(dish_name)
        if recipe is None:
            continue
        for ri in recipe.ingredients:
            consumption[ri.ingredient] = consumption.get(ri.ingredient, 0.0) + ri.quantity_kg * count

    return consumption


def _process_deliveries(world: WorldState, rng: SimRNG, tuning: TuningConfig) -> None:
    order_volume: dict[str, float] = {}
    for order in world.pending_orders:
        if order.delivery_day <= world.day:
            supplier_catalog = next(
                (s for s in world.suppliers if s.name == order.supplier), None
            )
            if supplier_catalog:
                cost = order.quantity_kg * order.cost_per_kg
                order_volume[order.supplier] = order_volume.get(order.supplier, 0.0) + cost

    conc_penalties = supplier_concentration_penalty(
        order_volume,
        tuning.supplier_states.concentration_threshold,
        tuning.supplier_states.concentration_penalty,
    )

    still_pending = []
    for order in world.pending_orders:
        if order.delivery_day > world.day:
            still_pending.append(order)
            continue

        reliability = effective_reliability(order.supplier, world, tuning.supplier_states)
        reliability = max(0.0, reliability - conc_penalties.get(order.supplier, 0.0))

        roll = rng.supplier.random()
        if roll < reliability:
            delivered_kg = order.quantity_kg
            on_time = True
        else:
            delivered_kg = round(order.quantity_kg * rng.supplier.uniform(0.5, 0.9), 3)
            on_time = False

        if delivered_kg > 0:
            shelf_life = _get_shelf_life(order.ingredient, world)
            batches = world.inventory.get(order.ingredient, [])
            batches.append(InventoryBatch(
                ingredient=order.ingredient,
                quantity_kg=delivered_kg,
                expires_in_days=shelf_life,
                cost_per_kg=order.cost_per_kg,
            ))
            world.inventory[order.ingredient] = batches

        world.delivery_history.append(DeliveryRecord(
            supplier=order.supplier,
            ingredient=order.ingredient,
            ordered_kg=order.quantity_kg,
            delivered_kg=delivered_kg,
            order_day=order.order_day,
            delivery_day=world.day,
            on_time=on_time,
        ))

    world.pending_orders = still_pending


def _get_shelf_life(ingredient: str, world: WorldState) -> int:
    return world.ingredient_shelf_life.get(ingredient, 5)


def _end_of_day_accounting(
    world: WorldState,
    result: DayServiceResult,
    tuning: TuningConfig,
    waste_cost: float,
) -> None:
    revenue = result.total_revenue

    staff_cost = world.staff_level * tuning.economics.staff_cost_per_day
    fixed_cost = tuning.economics.fixed_daily_cost
    marketing_cost = world.marketing_spend

    total_costs = staff_cost + fixed_cost + marketing_cost + waste_cost

    world.revenue_today = revenue
    world.costs_today = {
        "staff": round(staff_cost, 2),
        "fixed": round(fixed_cost, 2),
        "marketing": round(marketing_cost, 2),
        "waste": round(waste_cost, 2),
    }

    world.cash += revenue
    world.cash -= total_costs

    if world.cash < 0:
        world.bankrupt = True

    world.walkouts_today = result.total_walkouts


def _generate_weather(world: WorldState, rng: SimRNG, tuning: TuningConfig) -> None:
    weather_types = list(tuning.weather.distribution.keys())
    weights = list(tuning.weather.distribution.values())

    cumulative = []
    total = 0.0
    for w in weights:
        total += w
        cumulative.append(total)

    def _draw_weather() -> str:
        r = float(rng.weather.random())
        for i, c in enumerate(cumulative):
            if r < c:
                return weather_types[i]
        return weather_types[-1]

    world.weather_today = world.weather_actual_upcoming[0]
    world.weather_actual_upcoming = world.weather_actual_upcoming[1:] + [_draw_weather()]

    forecast = []
    for i, accuracy in enumerate(tuning.weather.forecast_accuracy):
        actual = world.weather_actual_upcoming[i]
        if float(rng.weather.random()) < accuracy:
            forecast.append(actual)
        else:
            others = [w for w in weather_types if w != actual]
            forecast.append(others[int(rng.weather.integers(0, len(others)))])
    world.weather_forecast = forecast


def _advance_day(world: WorldState, tuning: TuningConfig) -> None:
    world.day += 1
    dow_index = DAYS_OF_WEEK.index(world.day_of_week)
    world.day_of_week = DAYS_OF_WEEK[(dow_index + 1) % 7]
    world.alerts = []
    if world.marketing_spend > 0:
        world.promotions.marketing_fatigue += 1.0
    world.marketing_spend = 0.0
    if world.promotions.happy_hour:
        world.promotions.happy_hour_streak += 1
        world.promotions.happy_hour_peak_streak = max(
            world.promotions.happy_hour_peak_streak,
            world.promotions.happy_hour_streak,
        )
        world.promotions.happy_hour_days_since_stopped = 0
    else:
        if world.promotions.happy_hour_streak > 0:
            world.promotions.happy_hour_peak_streak = world.promotions.happy_hour_streak
        world.promotions.happy_hour_streak = 0
        world.promotions.happy_hour_days_since_stopped += 1
    world.promotions.daily_special = None
