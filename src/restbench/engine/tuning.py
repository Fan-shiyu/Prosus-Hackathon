"""All simulation constants in one place. Scenario YAML files override subsets of these."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EconomicsConfig:
    starting_cash: float = 15000.0
    fixed_daily_cost: float = 300.0
    staff_cost_per_day: float = 120.0
    starting_staff: int = 8


@dataclass(frozen=True)
class DemandConfig:
    hourly_rates: dict[int, float] = field(default_factory=lambda: {
        11: 2.0, 12: 7.5, 13: 6.0, 14: 2.5, 15: 1.5, 16: 2.0,
        17: 3.0, 18: 6.0, 19: 10.0, 20: 9.0, 21: 5.0, 22: 2.0,
    })
    dow_factors: dict[str, float] = field(default_factory=lambda: {
        "Monday": 0.70, "Tuesday": 0.75, "Wednesday": 0.85, "Thursday": 0.95,
        "Friday": 1.30, "Saturday": 1.50, "Sunday": 1.20,
    })
    weather_factors: dict[str, float] = field(default_factory=lambda: {
        "sunny": 1.10, "cloudy": 1.00, "rainy": 0.80, "stormy": 0.60,
    })
    party_size_mean: float = 2.3
    party_size_min: int = 1
    party_size_max: int = 6
    variety_threshold: float = 0.6
    variety_penalty_slope: float = 0.4
    variety_min_modifier: float = 0.76


@dataclass(frozen=True)
class TableConfig:
    layout: dict[int, int] = field(default_factory=lambda: {
        2: 4, 4: 8, 6: 6, 8: 4,
    })  # seats -> count
    lunch_duration_mean_min: float = 45.0
    dinner_duration_mean_min: float = 75.0
    patience_mean_min: float = 20.0
    duration_sigma: float = 0.3


@dataclass(frozen=True)
class KitchenConfig:
    understaffing_slope: float = 0.5
    new_dish_cook_time_multiplier: float = 1.25
    new_dish_learning_days: int = 2


@dataclass(frozen=True)
class SatisfactionConfig:
    beta_a: float = 8.0
    beta_b: float = 2.0
    wait_penalty_slope: float = 0.015
    wait_penalty_threshold_min: float = 10.0
    wait_penalty_cap: float = 0.50
    kitchen_penalty_slope: float = 0.008
    kitchen_penalty_threshold_min: float = 20.0
    kitchen_penalty_cap: float = 0.30
    substitution_penalty: float = 0.10
    price_penalty_slope: float = 0.05
    price_penalty_threshold: float = 1.15
    daily_special_bonus: float = 0.05


@dataclass(frozen=True)
class ReputationConfig:
    ewma_retain: float = 0.85
    starting_ewma: float = 3.9
    bad_review_weight: float = 2.0
    ghost_review_weight: float = 2.0
    review_probability: float = 0.20
    walkout_review_probability: float = 0.05
    review_delay_p: float = 0.4
    bands: dict[str, float] = field(default_factory=lambda: {
        "Poor": 2.5, "Fair": 3.2, "Good": 3.8, "Very Good": 4.3,
    })  # upper boundaries; >= 4.3 is "Excellent"


@dataclass(frozen=True)
class CohortConfig:
    starting_regulars: float = 80.0
    starting_occasionals: float = 320.0
    starting_prospects: float = 1500.0
    promotion_rate: float = 0.02
    promotion_threshold: float = 0.80
    demotion_rate: float = 0.04
    demotion_threshold: float = 0.65
    walkout_demotion_rate: float = 0.15
    lost_recovery_rate: float = 0.005
    visit_rates: dict[str, float] = field(default_factory=lambda: {
        "regulars": 0.27, "occasionals": 0.02, "prospects": 0.003,
        "tried_once": 0.0002, "lost": 0.0,
    })
    spend_multipliers: dict[str, float] = field(default_factory=lambda: {
        "regulars": 1.5, "occasionals": 1.2, "prospects": 1.0,
        "tried_once": 0.9, "lost": 0.0,
    })
    base_demand: float = 32.5


@dataclass(frozen=True)
class WeatherConfig:
    distribution: dict[str, float] = field(default_factory=lambda: {
        "sunny": 0.30, "cloudy": 0.35, "rainy": 0.25, "stormy": 0.10,
    })
    forecast_accuracy: list[float] = field(default_factory=lambda: [0.85, 0.70, 0.55])


@dataclass(frozen=True)
class MarketingConfig:
    max_spend: float = 500.0
    log_coefficient: float = 0.1
    max_modifier: float = 1.3
    fatigue_rate: float = 0.023  # ~77% effective after 10 days


@dataclass(frozen=True)
class SupplierStateConfig:
    disruption_prob: float = 0.03
    recovery_from_disrupted_prob: float = 0.25
    escalation_prob: float = 0.15
    recovery_from_outage_prob: float = 0.10
    disrupted_reliability_factor: float = 0.6
    outage_reliability: float = 0.0
    alert_probability: float = 0.5
    concentration_threshold: float = 0.70
    concentration_penalty: float = 0.15


@dataclass(frozen=True)
class PriceElasticityConfig:
    dish_elasticity_k: float = 5.0
    aggregate_sensitivity: float = 0.5
    min_dish_weight: float = 0.15


@dataclass(frozen=True)
class HappyHourConfig:
    hours: list[int] = field(default_factory=lambda: [15, 16, 17, 18])
    demand_multiplier: float = 1.3
    price_discount: float = 0.15
    satisfaction_bonus: float = 0.04
    streak_decay: float = 0.12
    withdrawal_penalty: float = 0.03
    withdrawal_threshold_days: int = 7


@dataclass(frozen=True)
class ScoringConfig:
    satisfaction_penalty_coeff: float = 10_000.0
    satisfaction_threshold: float = 0.70
    reputation_penalty_coeff: float = 2_000.0
    reputation_threshold: float = 3.5
    walkout_penalty_per: float = 1.0
    waste_penalty_coeff: float = 1_000.0
    waste_threshold: float = 0.20
    bankruptcy_score: float = -100_000.0
    endgame_weight_day25: float = 0.30
    endgame_weight_day27: float = 0.30
    endgame_weight_day30: float = 0.40


@dataclass(frozen=True)
class SimulationConfig:
    total_days: int = 30
    service_hours: list[int] = field(default_factory=lambda: list(range(11, 23)))
    min_active_dishes: int = 5
    min_staff: int = 3
    max_staff: int = 15
    price_min_ratio: float = 0.8
    price_max_ratio: float = 1.2
    notes_max_chars: int = 4000
    overstock_threshold: float = 2.0


@dataclass(frozen=True)
class TuningConfig:
    economics: EconomicsConfig = field(default_factory=EconomicsConfig)
    demand: DemandConfig = field(default_factory=DemandConfig)
    tables: TableConfig = field(default_factory=TableConfig)
    kitchen: KitchenConfig = field(default_factory=KitchenConfig)
    satisfaction: SatisfactionConfig = field(default_factory=SatisfactionConfig)
    reputation: ReputationConfig = field(default_factory=ReputationConfig)
    cohorts: CohortConfig = field(default_factory=CohortConfig)
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    marketing: MarketingConfig = field(default_factory=MarketingConfig)
    supplier_states: SupplierStateConfig = field(default_factory=SupplierStateConfig)
    price_elasticity: PriceElasticityConfig = field(default_factory=PriceElasticityConfig)
    happy_hour: HappyHourConfig = field(default_factory=HappyHourConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)


DEFAULT_TUNING = TuningConfig()
