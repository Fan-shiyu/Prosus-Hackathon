"""Service simulation: one day of restaurant operation.

Simulates 12 service hours (11:00-22:00) with:
- Poisson demand per hour with DoW/weather/marketing/variety modifiers
- Minute-granularity table management with smallest-fit assignment
- Patience-based walkouts when no table available
- Random dish selection with substitution cascade
- FIFO ingredient consumption from batches
- Per-customer satisfaction scoring with per-recipe kitchen times
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from restbench.core.rng import SimRNG
from restbench.core.types import DayServiceResult, HourlyMetrics, WorldState
from restbench.engine.cohorts import compute_cohort_modifier
from restbench.engine.guardrails import happy_hour_withdrawal_penalty
from restbench.engine.satisfaction import compute_satisfaction
from restbench.engine.tuning import TuningConfig

SERVICE_START_HOUR = 11


@dataclass
class _Arrival:
    minute: int
    party_size: int
    hour: int


def simulate_day(world: WorldState, rng: SimRNG, tuning: TuningConfig) -> DayServiceResult:
    dow_factor = tuning.demand.dow_factors.get(world.day_of_week, 1.0)
    weather_factor = tuning.demand.weather_factors.get(world.weather_today, 1.0)
    marketing_factor = _marketing_modifier(world, tuning)
    variety_factor = _variety_modifier(world, tuning)
    cohort_factor = compute_cohort_modifier(world, tuning.cohorts)
    price_factor = _price_arrival_modifier(world, tuning)

    active_recipes = [r for r in world.recipes if r.name in world.active_menu]
    if not active_recipes:
        return DayServiceResult()

    daily_special = world.promotions.daily_special
    staff_ratio = world.staff_level / 8.0

    happy_hour_active = world.promotions.happy_hour
    happy_hour_streak = world.promotions.happy_hour_streak

    hh_withdrawal = happy_hour_withdrawal_penalty(
        days_since_stopped=world.promotions.happy_hour_days_since_stopped,
        previous_streak=world.promotions.happy_hour_peak_streak,
        threshold_days=tuning.happy_hour.withdrawal_threshold_days,
        penalty=tuning.happy_hour.withdrawal_penalty,
    )

    arrivals = _generate_arrivals(
        rng, tuning, dow_factor, weather_factor, marketing_factor,
        variety_factor, cohort_factor, price_factor,
        happy_hour_active, happy_hour_streak,
    )

    table_sizes = sorted(tuning.tables.layout.keys())
    free_tables: dict[int, int] = dict(tuning.tables.layout)
    total_tables = sum(tuning.tables.layout.values())
    occupied: list[tuple[float, int]] = []  # (free_at_minute, table_size)

    total_covers = 0
    total_revenue = 0.0
    total_walkouts = 0
    total_substitutions = 0
    total_table_waits = 0
    table_wait_minutes_sum = 0.0
    dishes_sold: dict[str, int] = {}
    dishes_unavailable_at: dict[str, str] = {}
    satisfaction_scores: list[float] = []

    hour_data: dict[int, dict] = {}
    for h in tuning.simulation.service_hours:
        hour_data[h] = {
            "covers": 0, "revenue": 0.0, "walkouts": 0,
            "wait_total": 0.0, "wait_peak": 0.0, "wait_count": 0,
            "occupied_peak": 0,
        }

    for arrival in arrivals:
        minute = arrival.minute
        party_size = arrival.party_size
        hour = arrival.hour
        hd = hour_data[hour]

        _free_tables(occupied, free_tables, minute)

        occupied_count = total_tables - sum(free_tables.values())
        hd["occupied_peak"] = max(hd["occupied_peak"], occupied_count)

        table_size = _find_table(free_tables, table_sizes, party_size)
        wait_minutes = 0.0

        if table_size is None:
            earliest, earliest_size = _earliest_fitting_table(occupied, table_sizes, party_size)
            patience = float(rng.patience.exponential(tuning.tables.patience_mean_min))
            if earliest is not None and (earliest - minute) <= patience:
                wait_minutes = earliest - minute
                _free_tables(occupied, free_tables, earliest)
                table_size = _find_table(free_tables, table_sizes, party_size)
            if table_size is None:
                hd["walkouts"] += party_size
                total_walkouts += party_size
                continue

        if wait_minutes > 0:
            total_table_waits += 1
            table_wait_minutes_sum += wait_minutes
        hd["wait_total"] += wait_minutes
        hd["wait_peak"] = max(hd["wait_peak"], wait_minutes)
        hd["wait_count"] += 1

        is_lunch = hour <= 14
        mean_dur = tuning.tables.lunch_duration_mean_min if is_lunch else tuning.tables.dinner_duration_mean_min
        sigma = tuning.tables.duration_sigma
        mu = math.log(mean_dur) - sigma * sigma / 2.0
        duration = float(rng.party.lognormal(mu, sigma))
        seat_minute = minute + wait_minutes
        free_at = seat_minute + duration
        occupied.append((free_at, table_size))
        free_tables[table_size] -= 1

        party_covers = 0
        party_revenue = 0.0
        for _ in range(party_size):
            dish = _pick_dish(active_recipes, world, rng, hour, tuning)
            if dish is None:
                hd["walkouts"] += 1
                total_walkouts += 1
                continue

            was_substitution = False
            if not _consume_ingredients(world, dish):
                if dish.name not in dishes_unavailable_at:
                    dishes_unavailable_at[dish.name] = f"{hour}:00"
                alt = _pick_alternative(active_recipes, world, rng, exclude=dish.name)
                if alt is None or not _consume_ingredients(world, alt):
                    hd["walkouts"] += 1
                    total_walkouts += 1
                    continue
                dish = alt
                was_substitution = True
                total_substitutions += 1

            price = world.prices.get(dish.name, dish.base_price)
            is_hh_hour = happy_hour_active and hour in tuning.happy_hour.hours
            if is_hh_hour:
                price = round(price * (1.0 - tuning.happy_hour.price_discount), 2)
            price_ratio = price / dish.base_price
            is_special = dish.name == daily_special
            kitchen_minutes = _estimate_kitchen_time(dish, staff_ratio, world, rng, tuning)

            hh_bonus = tuning.happy_hour.satisfaction_bonus if is_hh_hour else 0.0
            sat = compute_satisfaction(
                rng,
                tuning.satisfaction,
                wait_minutes=wait_minutes,
                kitchen_minutes=kitchen_minutes,
                was_substitution=was_substitution,
                price_ratio=price_ratio,
                is_daily_special=is_special,
                happy_hour_bonus=hh_bonus,
            )
            sat = max(0.0, sat - hh_withdrawal)
            satisfaction_scores.append(sat)

            party_revenue += price
            party_covers += 1
            dishes_sold[dish.name] = dishes_sold.get(dish.name, 0) + 1

        hd["covers"] += party_covers
        hd["revenue"] += party_revenue
        total_covers += party_covers
        total_revenue += party_revenue

    hourly_metrics = []
    for h in tuning.simulation.service_hours:
        hd = hour_data[h]
        avg_wait = hd["wait_total"] / hd["wait_count"] if hd["wait_count"] > 0 else 0.0
        hourly_metrics.append(HourlyMetrics(
            hour=h,
            covers=hd["covers"],
            revenue=hd["revenue"],
            walkouts=hd["walkouts"],
            wait_minutes_avg=round(avg_wait, 1),
            wait_minutes_peak=round(hd["wait_peak"], 1),
        ))

    peak_utilization = 0.0
    if total_tables > 0:
        for hd in hour_data.values():
            util = hd["occupied_peak"] / total_tables
            peak_utilization = max(peak_utilization, util)

    mean_sat = sum(satisfaction_scores) / len(satisfaction_scores) if satisfaction_scores else 0.0

    return DayServiceResult(
        total_covers=total_covers,
        total_revenue=total_revenue,
        total_walkouts=total_walkouts,
        hourly_metrics=hourly_metrics,
        dishes_sold=dishes_sold,
        dishes_unavailable_at=dishes_unavailable_at,
        substitution_count=total_substitutions,
        satisfaction_scores=satisfaction_scores,
        mean_satisfaction=round(mean_sat, 4),
        table_utilization_peak=round(peak_utilization, 3),
    )


def _generate_arrivals(
    rng: SimRNG, tuning: TuningConfig,
    dow_factor: float, weather_factor: float, marketing_factor: float,
    variety_factor: float, cohort_factor: float,
    price_factor: float = 1.0,
    happy_hour_active: bool = False,
    happy_hour_streak: int = 0,
) -> list[_Arrival]:
    arrivals: list[_Arrival] = []
    hh = tuning.happy_hour
    for hour in tuning.simulation.service_hours:
        base_rate = tuning.demand.hourly_rates.get(hour, 2.0)
        adjusted_rate = base_rate * dow_factor * weather_factor * marketing_factor * variety_factor * cohort_factor * price_factor
        if happy_hour_active and hour in hh.hours:
            streak_penalty = min(hh.streak_decay * happy_hour_streak, hh.demand_multiplier - 1.0)
            adjusted_rate *= max(1.0, hh.demand_multiplier - streak_penalty)
        adjusted_rate = max(0.1, adjusted_rate)
        num_parties = int(rng.demand.poisson(adjusted_rate))
        for _ in range(num_parties):
            minute_in_hour = int(rng.demand.integers(0, 60))
            minute = (hour - SERVICE_START_HOUR) * 60 + minute_in_hour
            party_size = int(rng.party.poisson(tuning.demand.party_size_mean)) + 1
            party_size = max(tuning.demand.party_size_min, min(tuning.demand.party_size_max, party_size))
            arrivals.append(_Arrival(minute=minute, party_size=party_size, hour=hour))
    arrivals.sort(key=lambda a: a.minute)
    return arrivals


def _free_tables(
    occupied: list[tuple[float, int]],
    free_tables: dict[int, int],
    current_minute: float,
) -> None:
    i = 0
    while i < len(occupied):
        free_at, size = occupied[i]
        if free_at <= current_minute:
            free_tables[size] = free_tables.get(size, 0) + 1
            occupied.pop(i)
        else:
            i += 1


def _find_table(free_tables: dict[int, int], table_sizes: list[int], party_size: int) -> int | None:
    for size in table_sizes:
        if size >= party_size and free_tables.get(size, 0) > 0:
            return size
    return None


def _earliest_fitting_table(
    occupied: list[tuple[float, int]],
    table_sizes: list[int],
    party_size: int,
) -> tuple[float | None, int | None]:
    best_time = None
    best_size = None
    for free_at, size in occupied:
        if size >= party_size:
            if best_time is None or free_at < best_time:
                best_time = free_at
                best_size = size
    return best_time, best_size


def _pick_dish(recipes, world, rng, hour, tuning: TuningConfig | None = None):
    available = [r for r in recipes if _has_ingredients(world, r)]
    if not available:
        return None
    if tuning is None:
        idx = int(rng.demand.integers(0, len(available)))
        return available[idx]
    k = tuning.price_elasticity.dish_elasticity_k
    min_w = tuning.price_elasticity.min_dish_weight
    weights = []
    for r in available:
        price_ratio = world.prices.get(r.name, r.base_price) / r.base_price
        w = max(min_w, 1.0 / (1.0 + math.exp(k * (price_ratio - 1.0))))
        weights.append(w)
    total_w = sum(weights)
    roll = float(rng.demand.random()) * total_w
    cumulative = 0.0
    for i, w in enumerate(weights):
        cumulative += w
        if roll < cumulative:
            return available[i]
    return available[-1]


def _pick_alternative(recipes, world, rng, exclude: str):
    available = [r for r in recipes if r.name != exclude and _has_ingredients(world, r)]
    if not available:
        return None
    idx = int(rng.demand.integers(0, len(available)))
    return available[idx]


def _has_ingredients(world: WorldState, recipe) -> bool:
    for ri in recipe.ingredients:
        batches = world.inventory.get(ri.ingredient, [])
        total = sum(b.quantity_kg for b in batches)
        if total < ri.quantity_kg:
            return False
    return True


def _consume_ingredients(world: WorldState, recipe) -> bool:
    if not _has_ingredients(world, recipe):
        return False
    for ri in recipe.ingredients:
        remaining = ri.quantity_kg
        batches = world.inventory.get(ri.ingredient, [])
        new_batches = []
        for batch in batches:
            if remaining <= 0:
                new_batches.append(batch)
                continue
            if batch.quantity_kg <= remaining:
                remaining -= batch.quantity_kg
            else:
                batch.quantity_kg -= remaining
                remaining = 0
                new_batches.append(batch)
        world.inventory[ri.ingredient] = new_batches
    return True


def _variety_modifier(world: WorldState, tuning: TuningConfig) -> float:
    total_recipes = len(world.recipes)
    if total_recipes == 0:
        return 1.0
    fraction = len(world.active_menu) / total_recipes
    if fraction >= tuning.demand.variety_threshold:
        return 1.0
    penalty = (tuning.demand.variety_threshold - fraction) * tuning.demand.variety_penalty_slope
    return max(tuning.demand.variety_min_modifier, 1.0 - penalty)


def _price_arrival_modifier(world: WorldState, tuning: TuningConfig) -> float:
    active_recipes = [r for r in world.recipes if r.name in world.active_menu]
    if not active_recipes:
        return 1.0
    total_ratio = sum(
        world.prices.get(r.name, r.base_price) / r.base_price
        for r in active_recipes
    )
    mean_ratio = total_ratio / len(active_recipes)
    if mean_ratio <= 1.0:
        return 1.0
    return max(0.7, 1.0 - tuning.price_elasticity.aggregate_sensitivity * (mean_ratio - 1.0))


def _marketing_modifier(world: WorldState, tuning: TuningConfig) -> float:
    spend = world.marketing_spend
    if spend <= 0:
        return 1.0
    mc = tuning.marketing
    fatigue = math.exp(-mc.fatigue_rate * world.promotions.marketing_fatigue)
    raw = 1.0 + mc.log_coefficient * math.log1p(spend) * fatigue
    return min(raw, mc.max_modifier)


def _estimate_kitchen_time(recipe, staff_ratio: float, world: WorldState, rng: SimRNG, tuning: TuningConfig) -> float:
    base = float(recipe.cook_time_minutes)
    added_day = world.menu_dish_added_day.get(recipe.name, 0)
    if world.day - added_day < tuning.kitchen.new_dish_learning_days:
        base *= tuning.kitchen.new_dish_cook_time_multiplier
    if staff_ratio < 1.0:
        base *= 1.0 + (1.0 - staff_ratio) * tuning.kitchen.understaffing_slope
    noise = float(rng.kitchen.exponential(3.0))
    return max(5.0, base + noise)


def process_spoilage(world: WorldState) -> tuple[float, float]:
    waste_kg = 0.0
    waste_cost = 0.0
    for ingredient, batches in world.inventory.items():
        kept = []
        for batch in batches:
            if batch.expires_in_days <= 0:
                waste_kg += batch.quantity_kg
                waste_cost += batch.quantity_kg * batch.cost_per_kg
            else:
                kept.append(batch)
        world.inventory[ingredient] = kept
    return waste_kg, waste_cost


def age_inventory(world: WorldState) -> None:
    for batches in world.inventory.values():
        for batch in batches:
            batch.expires_in_days -= 1
