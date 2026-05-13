"""Initialize WorldState from restaurant YAML data + tuning config."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from restbench.core.types import (
    InventoryBatch,
    Recipe,
    RecipeIngredient,
    StaffMember,
    SupplierConfig,
    SupplierIngredient,
    SupplierState,
    WorldState,
)
from restbench.engine.tuning import TuningConfig
import restbench

_DATA_DIR = restbench.data_dir()


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def create_world(
    seed: int = 42,
    scenario: str = "baseline",
    tuning: TuningConfig | None = None,
    data_file: str = "restaurant_minimal.yaml",
) -> WorldState:
    tuning = tuning or TuningConfig()
    data = _load_yaml(_DATA_DIR / data_file)

    ingredient_shelf_life: dict[str, int] = {}
    ingredient_par: dict[str, float] = {}
    for ing in data["ingredients"]:
        ingredient_shelf_life[ing["name"]] = ing["shelf_life_days"]
        ingredient_par[ing["name"]] = ing["par_level_kg"]

    recipes = [
        Recipe(
            name=r["name"],
            category=r["category"],
            base_price=r["base_price"],
            cook_time_minutes=r.get("cook_time_minutes", 15),
            ingredients=[
                RecipeIngredient(ingredient=i["ingredient"], quantity_kg=i["quantity_kg"])
                for i in r["ingredients"]
            ],
        )
        for r in data["recipes"]
    ]

    suppliers = [
        SupplierConfig(
            name=s["name"],
            lead_time_days=s["lead_time_days"],
            delivery_days=s["delivery_days"],
            min_order_kg=s["min_order_kg"],
            reliability=s.get("reliability", 0.90),
            ingredients=[
                SupplierIngredient(ingredient=i["ingredient"], price_per_kg=i["price_per_kg"])
                for i in s["ingredients"]
            ],
        )
        for s in data["suppliers"]
    ]

    # Starting inventory: ~3 days worth of each ingredient
    inventory: dict[str, list[InventoryBatch]] = {}
    for ing_name, par in ingredient_par.items():
        shelf = ingredient_shelf_life[ing_name]
        qty = par * 3.0
        avg_cost = _avg_ingredient_cost(ing_name, data["suppliers"])
        inventory[ing_name] = [
            InventoryBatch(
                ingredient=ing_name,
                quantity_kg=qty,
                expires_in_days=shelf,
                cost_per_kg=avg_cost,
            )
        ]

    active_menu = [r.name for r in recipes]
    prices = {r.name: r.base_price for r in recipes}
    menu_dish_added_day = {r.name: -10 for r in recipes}

    staff = []
    num_staff = tuning.economics.starting_staff
    skill_levels = [0.9, 0.9, 0.7, 0.7, 0.7, 0.5, 0.5, 0.5]
    for i in range(num_staff):
        skill = skill_levels[i] if i < len(skill_levels) else 0.5
        staff.append(StaffMember(skill_level=skill))

    weather_rng = np.random.default_rng(np.random.SeedSequence(seed + 7777))
    weather_types = list(tuning.weather.distribution.keys())
    weather_weights = list(tuning.weather.distribution.values())
    initial_weather = _draw_weather_from(weather_rng, weather_types, weather_weights)
    upcoming = [
        _draw_weather_from(weather_rng, weather_types, weather_weights)
        for _ in range(3)
    ]

    forecast_rng = np.random.default_rng(np.random.SeedSequence(seed + 8888))
    initial_forecast = []
    for i, accuracy in enumerate(tuning.weather.forecast_accuracy):
        actual = upcoming[i]
        if float(forecast_rng.random()) < accuracy:
            initial_forecast.append(actual)
        else:
            others = [w for w in weather_types if w != actual]
            initial_forecast.append(others[int(forecast_rng.integers(0, len(others)))])

    return WorldState(
        day=1,
        day_of_week="Monday",
        cash=tuning.economics.starting_cash,
        inventory=inventory,
        ingredient_shelf_life=ingredient_shelf_life,
        recipes=recipes,
        active_menu=active_menu,
        prices=prices,
        menu_dish_added_day=menu_dish_added_day,
        staff=staff,
        staff_level=num_staff,
        suppliers=suppliers,
        supplier_states=[SupplierState(name=s.name) for s in suppliers],
        reputation_ewma=tuning.reputation.starting_ewma,
        previous_cohort_total=(
            tuning.cohorts.starting_regulars
            + tuning.cohorts.starting_occasionals
            + tuning.cohorts.starting_prospects
        ),
        weather_today=initial_weather,
        weather_forecast=initial_forecast,
        weather_actual_upcoming=upcoming,
        seed=seed,
        scenario_name=scenario,
    )


def _draw_weather_from(rng, weather_types: list[str], weights: list[float]) -> str:
    cumulative = []
    total = 0.0
    for w in weights:
        total += w
        cumulative.append(total)
    r = float(rng.random())
    for i, c in enumerate(cumulative):
        if r < c:
            return weather_types[i]
    return weather_types[-1]


def _avg_ingredient_cost(ingredient: str, suppliers_data: list[dict]) -> float:
    prices = []
    for s in suppliers_data:
        for i in s["ingredients"]:
            if i["ingredient"] == ingredient:
                prices.append(i["price_per_kg"])
    return sum(prices) / len(prices) if prices else 5.0
