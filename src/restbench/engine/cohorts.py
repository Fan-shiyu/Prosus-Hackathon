"""Cohort dynamics: 5-pool migration model driven by satisfaction.

Pools: Regulars -> Occasionals -> Prospects -> Tried_Once -> Lost
Migration is driven by daily mean satisfaction and walkout counts.
Cohort sizes modulate demand via visit rates.
"""

from __future__ import annotations

from restbench.core.types import WorldState
from restbench.engine.tuning import CohortConfig


def compute_cohort_modifier(world: WorldState, config: CohortConfig) -> float:
    c = world.cohorts
    demand = (
        c.regulars * config.visit_rates["regulars"]
        + c.occasionals * config.visit_rates["occasionals"]
        + c.prospects * config.visit_rates["prospects"]
        + c.tried_once * config.visit_rates["tried_once"]
        + c.lost * config.visit_rates["lost"]
    )
    if config.base_demand <= 0:
        return 1.0
    return demand / config.base_demand


def update_cohorts(
    world: WorldState,
    config: CohortConfig,
    mean_satisfaction: float,
    walkouts: int,
) -> None:
    c = world.cohorts

    if mean_satisfaction >= config.promotion_threshold:
        promote_occ = c.occasionals * config.promotion_rate
        c.regulars += promote_occ
        c.occasionals -= promote_occ

        promote_tried = c.tried_once * config.promotion_rate
        c.occasionals += promote_tried
        c.tried_once -= promote_tried

        promote_prospects = c.prospects * config.promotion_rate * 0.5
        c.tried_once += promote_prospects
        c.prospects -= promote_prospects

    if mean_satisfaction <= config.demotion_threshold:
        demote_reg = c.regulars * config.demotion_rate
        c.regulars -= demote_reg
        c.occasionals += demote_reg

        demote_occ = c.occasionals * config.demotion_rate
        c.occasionals -= demote_occ
        c.tried_once += demote_occ

    if walkouts > 0:
        walkout_demote = min(walkouts * config.walkout_demotion_rate, c.regulars * 0.1)
        c.regulars -= walkout_demote
        c.occasionals += walkout_demote

    churn = c.tried_once * 0.02
    c.tried_once -= churn
    c.lost += churn

    recovery = c.lost * config.lost_recovery_rate
    c.lost -= recovery
    c.prospects += recovery

    c.regulars = max(0.0, c.regulars)
    c.occasionals = max(0.0, c.occasionals)
    c.prospects = max(0.0, c.prospects)
    c.tried_once = max(0.0, c.tried_once)
    c.lost = max(0.0, c.lost)


def compute_customer_trend(world: WorldState) -> str:
    c = world.cohorts
    current = c.regulars + c.occasionals + c.prospects
    prev = world.previous_cohort_total
    if prev <= 0:
        return "Stable"
    change = (current - prev) / prev
    if change > 0.02:
        return "Growing"
    elif change < -0.02:
        return "Declining"
    return "Stable"
