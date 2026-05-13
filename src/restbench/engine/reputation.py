"""Review generation and reputation EWMA.

Flow:
1. After each day's service, a fraction of diners queue a review.
2. Reviews have a geometric delay before posting (visible to agent).
3. Walkouts may generate "ghost" reviews (0 stars, high weight).
4. Each day, posted reviews update the reputation EWMA.
5. Bad reviews (< 3 stars) are weighted 2x in the EWMA.
"""

from __future__ import annotations

from restbench.core.rng import SimRNG
from restbench.core.types import PendingReview, WorldState
from restbench.engine.tuning import ReputationConfig


def generate_reviews(
    world: WorldState,
    rng: SimRNG,
    config: ReputationConfig,
    satisfaction_scores: list[float],
    walkouts: int,
) -> None:
    for sat in satisfaction_scores:
        if rng.review.random() < config.review_probability:
            stars = _satisfaction_to_stars(sat, rng)
            delay = int(rng.review.geometric(config.review_delay_p))
            world.review_queue.append(PendingReview(
                stars=stars,
                day_of_visit=world.day,
                post_day=world.day + delay,
            ))

    for _ in range(walkouts):
        if rng.review.random() < config.walkout_review_probability:
            delay = int(rng.review.geometric(config.review_delay_p))
            world.review_queue.append(PendingReview(
                stars=0.0,
                day_of_visit=world.day,
                post_day=world.day + delay,
                is_walkout=True,
            ))


def update_reputation(world: WorldState, config: ReputationConfig) -> None:
    newly_posted = [r for r in world.review_queue if r.post_day <= world.day]
    if not newly_posted:
        return

    weighted_sum = 0.0
    total_weight = 0.0
    for review in newly_posted:
        if review.is_walkout:
            w = config.ghost_review_weight
        elif review.stars < 3.0:
            w = config.bad_review_weight
        else:
            w = 1.0
        weighted_sum += review.stars * w
        total_weight += w

    if total_weight > 0:
        weighted_mean = weighted_sum / total_weight
        world.reputation_ewma = (
            config.ewma_retain * world.reputation_ewma
            + (1.0 - config.ewma_retain) * weighted_mean
        )

    world.posted_reviews.extend(newly_posted)
    world.review_queue = [r for r in world.review_queue if r.post_day > world.day]


def _satisfaction_to_stars(satisfaction: float, rng: SimRNG) -> float:
    base_stars = satisfaction * 5.0
    noise = float(rng.review.normal(0, 0.3))
    return max(0.0, min(5.0, round(base_stars + noise, 1)))
