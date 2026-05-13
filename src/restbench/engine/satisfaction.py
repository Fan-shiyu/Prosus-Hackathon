"""Per-customer satisfaction scoring.

Each served customer gets a satisfaction score in [0, 1]:
  base      ~ Beta(8, 2)          mean ≈ 0.80
  - wait penalty                  if waited > 10 min
  - substitution penalty          if didn't get first choice
  - price penalty                 if dish price > 1.15× base
  + daily special bonus           if ordered the daily special
  clamped to [0, 1]
"""

from __future__ import annotations

from restbench.core.rng import SimRNG
from restbench.engine.tuning import SatisfactionConfig


def compute_satisfaction(
    rng: SimRNG,
    config: SatisfactionConfig,
    wait_minutes: float = 0.0,
    kitchen_minutes: float = 0.0,
    was_substitution: bool = False,
    price_ratio: float = 1.0,
    is_daily_special: bool = False,
    happy_hour_bonus: float = 0.0,
) -> float:
    base = float(rng.satisfaction.beta(config.beta_a, config.beta_b))

    penalty = 0.0

    if wait_minutes > config.wait_penalty_threshold_min:
        excess = wait_minutes - config.wait_penalty_threshold_min
        penalty += min(excess * config.wait_penalty_slope, config.wait_penalty_cap)

    if kitchen_minutes > config.kitchen_penalty_threshold_min:
        excess = kitchen_minutes - config.kitchen_penalty_threshold_min
        penalty += min(excess * config.kitchen_penalty_slope, config.kitchen_penalty_cap)

    if was_substitution:
        penalty += config.substitution_penalty

    if price_ratio > config.price_penalty_threshold:
        excess = price_ratio - config.price_penalty_threshold
        penalty += excess * config.price_penalty_slope

    bonus = config.daily_special_bonus if is_daily_special else 0.0
    bonus += happy_hour_bonus

    return max(0.0, min(1.0, base - penalty + bonus))
