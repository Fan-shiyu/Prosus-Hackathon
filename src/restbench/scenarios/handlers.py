"""Event handlers: each event type maps to a function that modifies world/tuning."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from restbench.core.rng import SimRNG
from restbench.core.types import PendingReview, WorldState
from restbench.engine.tuning import TuningConfig
from restbench.scenarios.loader import apply_tuning_overrides
from restbench.scenarios.types import EventSchedule, ResolvedEvent


def process_events(
    world: WorldState,
    tuning: TuningConfig,
    schedule: EventSchedule,
    rng: SimRNG,
    default_tuning: TuningConfig | None = None,
) -> TuningConfig:
    if default_tuning is None:
        default_tuning = TuningConfig()
    events_today = schedule.get(world.day, [])
    for event in events_today:
        tuning = _dispatch(event, world, tuning, rng, default_tuning)
        if event.alert:
            world.alerts.append(event.alert)
    return tuning


def _dispatch(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    handler = _HANDLERS.get(event.type)
    if handler is None:
        return tuning
    return handler(event, world, tuning, rng, default_tuning)


def _handle_supplier_outage(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    supplier_name = event.params["supplier"]
    status = event.params.get("status", "Extended_Outage")
    for state in world.supplier_states:
        if state.name == supplier_name:
            state.status = status
            break
    return tuning


def _handle_supplier_recovery(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    supplier_name = event.params["supplier"]
    status = event.params.get("status", "Normal")
    for state in world.supplier_states:
        if state.name == supplier_name:
            state.status = status
            break
    return tuning


def _handle_demand_surge(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    multiplier = event.params["multiplier"]
    new_rates = {h: r * multiplier for h, r in tuning.demand.hourly_rates.items()}
    new_demand = replace(tuning.demand, hourly_rates=new_rates)
    return replace(tuning, demand=new_demand)


def _handle_demand_drop(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    multiplier = event.params["multiplier"]
    new_rates = {h: r * multiplier for h, r in tuning.demand.hourly_rates.items()}
    new_demand = replace(tuning.demand, hourly_rates=new_rates)
    return replace(tuning, demand=new_demand)


def _handle_demand_restore(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    return replace(tuning, demand=replace(tuning.demand, hourly_rates=default_tuning.demand.hourly_rates))


def _handle_price_shock(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    ingredient = event.params["ingredient"]
    multiplier = event.params["multiplier"]
    for i, supplier in enumerate(world.suppliers):
        new_ingredients = []
        changed = False
        for si in supplier.ingredients:
            if si.ingredient == ingredient:
                new_ingredients.append(si.model_copy(update={"price_per_kg": round(si.price_per_kg * multiplier, 2)}))
                changed = True
            else:
                new_ingredients.append(si)
        if changed:
            world.suppliers[i] = supplier.model_copy(update={"ingredients": new_ingredients})
    return tuning


def _handle_cost_increase(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    target = event.params["target"]
    multiplier = event.params["multiplier"]
    econ = tuning.economics
    if target == "fixed":
        econ = replace(econ, fixed_daily_cost=round(econ.fixed_daily_cost * multiplier, 2))
    elif target == "staff":
        econ = replace(econ, staff_cost_per_day=round(econ.staff_cost_per_day * multiplier, 2))
    return replace(tuning, economics=econ)


def _handle_cost_restore(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    return replace(tuning, economics=default_tuning.economics)


def _handle_cash_penalty(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    amount = event.params["amount"]
    world.cash -= amount
    return tuning


def _handle_equipment_failure(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    reductions = event.params.get("reductions", {})
    layout = dict(tuning.tables.layout)
    for size_str, reduction in reductions.items():
        size = int(size_str)
        if size in layout:
            layout[size] = max(0, layout[size] - reduction)
    new_tables = replace(tuning.tables, layout=layout)
    return replace(tuning, tables=new_tables)


def _handle_equipment_restore(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    return replace(tuning, tables=replace(tuning.tables, layout=dict(default_tuning.tables.layout)))


def _handle_weather_lock(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    weather = event.params["weather"]
    world.weather_today = weather
    for i in range(len(world.weather_actual_upcoming)):
        world.weather_actual_upcoming[i] = weather
    return tuning


def _handle_weather_unlock(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    return tuning


def _handle_viral_review(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    stars = event.params["stars"]
    count = event.params["count"]
    for _ in range(count):
        world.review_queue.append(PendingReview(
            stars=stars,
            day_of_visit=world.day,
            post_day=world.day,
            is_walkout=False,
        ))
    return tuning


def _handle_satisfaction_modifier(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    bonus = event.params["bonus"]
    sat = tuning.satisfaction
    new_sat = replace(sat, daily_special_bonus=sat.daily_special_bonus + bonus)
    return replace(tuning, satisfaction=new_sat)


def _handle_satisfaction_restore(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    return replace(tuning, satisfaction=replace(tuning.satisfaction, daily_special_bonus=default_tuning.satisfaction.daily_special_bonus))


def _handle_tuning_override(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    path = event.params["path"]
    value = event.params["value"]
    return apply_tuning_overrides(tuning, {path: value})


def _handle_tuning_restore(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    path = event.params["path"]
    parts = path.split(".", 1)
    if len(parts) == 1:
        default_val = getattr(default_tuning, parts[0])
        return apply_tuning_overrides(tuning, {path: default_val})
    else:
        sub_config = getattr(default_tuning, parts[0])
        sub_path = parts[1]
        if "." in sub_path:
            attr_name, dict_key = sub_path.split(".", 1)
            default_val = getattr(sub_config, attr_name)[dict_key]
        else:
            default_val = getattr(sub_config, sub_path)
        return apply_tuning_overrides(tuning, {path: default_val})


def _handle_alert_only(event: ResolvedEvent, world: WorldState, tuning: TuningConfig, rng: SimRNG, default_tuning: TuningConfig) -> TuningConfig:
    return tuning


_HANDLERS: dict[str, Any] = {
    "supplier_outage": _handle_supplier_outage,
    "supplier_recovery": _handle_supplier_recovery,
    "demand_surge": _handle_demand_surge,
    "demand_drop": _handle_demand_drop,
    "demand_restore": _handle_demand_restore,
    "price_shock": _handle_price_shock,
    "cost_increase": _handle_cost_increase,
    "cost_restore": _handle_cost_restore,
    "cash_penalty": _handle_cash_penalty,
    "equipment_failure": _handle_equipment_failure,
    "equipment_restore": _handle_equipment_restore,
    "weather_lock": _handle_weather_lock,
    "weather_unlock": _handle_weather_unlock,
    "viral_review": _handle_viral_review,
    "satisfaction_modifier": _handle_satisfaction_modifier,
    "satisfaction_restore": _handle_satisfaction_restore,
    "tuning_override": _handle_tuning_override,
    "tuning_restore": _handle_tuning_restore,
    "alert_only": _handle_alert_only,
}
