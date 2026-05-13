"""Anti-gaming guardrails.

Pure-function module — no RNG, no state mutation. Each function takes
game-state values and tuning parameters, returns a modifier or penalty.
"""

from __future__ import annotations

import math


def supplier_concentration_penalty(
    order_volume_by_supplier: dict[str, float],
    threshold: float,
    penalty: float,
) -> dict[str, float]:
    """Reliability reduction when a single supplier gets too much volume.

    If any supplier receives more than `threshold` (e.g. 70%) of total order
    volume, their delivery reliability is reduced by `penalty` (e.g. 0.15)
    for that day's deliveries.
    """
    total = sum(order_volume_by_supplier.values())
    if total <= 0:
        return {}

    penalties: dict[str, float] = {}
    for supplier in sorted(order_volume_by_supplier.keys()):
        if order_volume_by_supplier[supplier] / total > threshold:
            penalties[supplier] = penalty
    return penalties


def endgame_weighted_reputation(
    reputation_snapshots: dict[int, float],
    total_days: int,
    w25: float,
    w27: float,
    w30: float,
) -> float:
    """Weighted final reputation from snapshots at days 25, 27, and 30.

    Prevents agents from tanking quality in the last few days.
    """
    if total_days <= 0:
        return 0.0

    day25 = reputation_snapshots.get(total_days - 5, 3.9)
    day27 = reputation_snapshots.get(total_days - 3, 3.9)
    day30 = reputation_snapshots.get(total_days, 3.9)

    return w25 * day25 + w27 * day27 + w30 * day30


def happy_hour_withdrawal_penalty(
    days_since_stopped: int,
    previous_streak: int,
    threshold_days: int,
    penalty: float,
) -> float:
    """Satisfaction penalty when customers expect a discount that disappeared.

    If the restaurant ran happy hour for `threshold_days`+ consecutive days
    and then stopped, a satisfaction penalty applies for the first 3 days.
    """
    if previous_streak >= threshold_days and days_since_stopped < 3:
        return penalty
    return 0.0


def overstock_penalty(
    inventory: dict[str, list],
    expected_daily_consumption: dict[str, float],
    threshold: float,
) -> int:
    """Reduce shelf life on oldest batch when overstocked.

    For each ingredient, if total quantity exceeds threshold * expected daily
    consumption, the oldest batch loses 1 day of shelf life (clamped to 0).

    Returns the number of batches penalized.
    """
    penalized = 0
    for ingredient in sorted(inventory.keys()):
        batches = inventory[ingredient]
        if not batches:
            continue

        expected = expected_daily_consumption.get(ingredient, 0.0)
        if expected <= 0:
            continue

        total_qty = sum(b.quantity_kg for b in batches)
        if total_qty > threshold * expected:
            batches[0].expires_in_days = max(0, batches[0].expires_in_days - 1)
            penalized += 1

    return penalized
