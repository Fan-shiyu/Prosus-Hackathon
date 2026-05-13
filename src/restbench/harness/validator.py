"""Validate and sanitize AgentAction against current WorldState.

Design: validate each field independently. Invalid fields are silently
rejected (replaced with no-op defaults) and logged. The simulation never
crashes due to bad agent input.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from restbench.core.types import AgentAction, PurchaseOrderRequest, WorldState
from restbench.engine.tuning import TuningConfig

logger = logging.getLogger(__name__)


@dataclass
class ValidatedAction:
    orders: list[PurchaseOrderRequest] = field(default_factory=list)
    set_active_menu: list[str] | None = None
    set_prices: dict[str, float] | None = None
    set_staff_level: int | None = None
    marketing_spend: float | None = None
    run_happy_hour: bool = False
    offer_daily_special: str | None = None
    notes: str = ""
    reasoning: str = ""
    rejections: list[str] = field(default_factory=list)


def validate_action(
    action: AgentAction,
    world: WorldState,
    tuning: TuningConfig,
) -> ValidatedAction:
    result = ValidatedAction()
    result.reasoning = action.reasoning

    _validate_orders(action, world, tuning, result)
    _validate_menu(action, world, tuning, result)
    _validate_prices(action, world, tuning, result)
    _validate_staff(action, tuning, result)
    _validate_marketing(action, tuning, result)
    _validate_happy_hour(action, result)
    _validate_daily_special(action, world, result)
    _validate_notes(action, tuning, result)

    if result.rejections:
        logger.info("Rejected %d field(s): %s", len(result.rejections), result.rejections)

    return result


def _validate_orders(
    action: AgentAction,
    world: WorldState,
    tuning: TuningConfig,
    result: ValidatedAction,
) -> None:
    supplier_map: dict[str, dict[str, float]] = {}
    for s in world.suppliers:
        supplier_map[s.name] = {i.ingredient: i.price_per_kg for i in s.ingredients}

    budget_remaining = world.cash
    for order in action.orders:
        supplier_catalog = supplier_map.get(order.supplier)
        if supplier_catalog is None:
            result.rejections.append(f"order: unknown supplier '{order.supplier}'")
            continue

        if order.ingredient not in supplier_catalog:
            result.rejections.append(
                f"order: supplier '{order.supplier}' doesn't carry '{order.ingredient}'"
            )
            continue

        supplier_cfg = next(s for s in world.suppliers if s.name == order.supplier)
        if order.quantity_kg < supplier_cfg.min_order_kg:
            result.rejections.append(
                f"order: {order.quantity_kg}kg < min {supplier_cfg.min_order_kg}kg "
                f"for '{order.supplier}'"
            )
            continue

        cost = order.quantity_kg * supplier_catalog[order.ingredient]
        if cost > budget_remaining:
            result.rejections.append(
                f"order: cost {cost:.2f} exceeds remaining budget {budget_remaining:.2f}"
            )
            continue

        budget_remaining -= cost
        result.orders.append(order)


def _validate_menu(
    action: AgentAction,
    world: WorldState,
    tuning: TuningConfig,
    result: ValidatedAction,
) -> None:
    if action.set_active_menu is None:
        return

    recipe_names = {r.name for r in world.recipes}
    seen: set[str] = set()
    valid_dishes: list[str] = []
    for d in action.set_active_menu:
        if d in recipe_names and d not in seen:
            valid_dishes.append(d)
            seen.add(d)
    invalid = [d for d in action.set_active_menu if d not in recipe_names]

    for d in invalid:
        result.rejections.append(f"menu: unknown dish '{d}'")

    if len(valid_dishes) < tuning.simulation.min_active_dishes:
        result.rejections.append(
            f"menu: only {len(valid_dishes)} valid dishes, "
            f"need >= {tuning.simulation.min_active_dishes}; keeping current menu"
        )
        return

    result.set_active_menu = valid_dishes


def _validate_prices(
    action: AgentAction,
    world: WorldState,
    tuning: TuningConfig,
    result: ValidatedAction,
) -> None:
    if action.set_prices is None:
        return

    base_prices = {r.name: r.base_price for r in world.recipes}
    valid_prices: dict[str, float] = {}

    for dish, price in action.set_prices.items():
        if dish not in base_prices:
            result.rejections.append(f"price: unknown dish '{dish}'")
            continue

        base = base_prices[dish]
        lo = base * tuning.simulation.price_min_ratio
        hi = base * tuning.simulation.price_max_ratio

        if not (lo <= price <= hi):
            result.rejections.append(
                f"price: {dish} = {price:.2f} outside [{lo:.2f}, {hi:.2f}]"
            )
            continue

        valid_prices[dish] = round(price, 2)

    result.set_prices = valid_prices if valid_prices else None


def _validate_staff(
    action: AgentAction,
    tuning: TuningConfig,
    result: ValidatedAction,
) -> None:
    if action.set_staff_level is None:
        return

    lo = tuning.simulation.min_staff
    hi = tuning.simulation.max_staff

    if not (lo <= action.set_staff_level <= hi):
        result.rejections.append(
            f"staff: {action.set_staff_level} outside [{lo}, {hi}]"
        )
        return

    result.set_staff_level = action.set_staff_level


def _validate_marketing(
    action: AgentAction,
    tuning: TuningConfig,
    result: ValidatedAction,
) -> None:
    if action.marketing_spend is None:
        return

    if not (0 <= action.marketing_spend <= tuning.marketing.max_spend):
        result.rejections.append(
            f"marketing: {action.marketing_spend:.2f} outside [0, {tuning.marketing.max_spend:.2f}]"
        )
        return

    result.marketing_spend = round(action.marketing_spend, 2)


def _validate_happy_hour(action: AgentAction, result: ValidatedAction) -> None:
    result.run_happy_hour = action.run_happy_hour


def _validate_daily_special(
    action: AgentAction,
    world: WorldState,
    result: ValidatedAction,
) -> None:
    if action.offer_daily_special is None:
        return

    recipe_names = {r.name for r in world.recipes}
    if action.offer_daily_special not in recipe_names:
        result.rejections.append(
            f"daily_special: unknown dish '{action.offer_daily_special}'"
        )
        return

    result.offer_daily_special = action.offer_daily_special


def _validate_notes(
    action: AgentAction,
    tuning: TuningConfig,
    result: ValidatedAction,
) -> None:
    notes = action.notes
    max_len = tuning.simulation.notes_max_chars

    if len(notes) > max_len:
        result.rejections.append(
            f"notes: {len(notes)} chars truncated to {max_len}"
        )
        notes = notes[:max_len]

    result.notes = notes
