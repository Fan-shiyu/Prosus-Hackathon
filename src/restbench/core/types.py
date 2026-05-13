"""Core Pydantic types for RestBench simulation.

WorldState is the full engine truth (mutable, evolved each tick).
AgentObservation is the filtered view the agent receives (separate type to prevent leakage).
AgentAction is what the agent submits (validated by harness/validator.py).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Value types (immutable building blocks)
# ---------------------------------------------------------------------------


class RecipeIngredient(BaseModel):
    model_config = ConfigDict(frozen=True)
    ingredient: str
    quantity_kg: float


class Recipe(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    category: str
    base_price: float
    cook_time_minutes: int = 15
    ingredients: list[RecipeIngredient]


class SupplierIngredient(BaseModel):
    model_config = ConfigDict(frozen=True)
    ingredient: str
    price_per_kg: float


class SupplierConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    lead_time_days: int
    delivery_days: list[str]
    min_order_kg: float
    reliability: float = 0.90
    ingredients: list[SupplierIngredient]


class InventoryBatch(BaseModel):
    ingredient: str
    quantity_kg: float
    expires_in_days: int
    cost_per_kg: float


class PendingOrder(BaseModel):
    supplier: str
    ingredient: str
    quantity_kg: float
    cost_per_kg: float
    order_day: int
    delivery_day: int


class DeliveryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    supplier: str
    ingredient: str
    ordered_kg: float
    delivered_kg: float
    order_day: int
    delivery_day: int
    on_time: bool


class PendingReview(BaseModel):
    stars: float
    day_of_visit: int
    post_day: int
    is_walkout: bool = False


class HourlyMetrics(BaseModel):
    hour: int
    covers: int = 0
    revenue: float = 0.0
    walkouts: int = 0
    wait_minutes_avg: float = 0.0
    wait_minutes_peak: float = 0.0


class CohortState(BaseModel):
    regulars: float = 80.0
    occasionals: float = 320.0
    prospects: float = 1500.0
    tried_once: float = 0.0
    lost: float = 0.0


class PromotionState(BaseModel):
    happy_hour: bool = False
    daily_special: str | None = None
    happy_hour_streak: int = 0
    marketing_fatigue: float = 0.0
    happy_hour_peak_streak: int = 0
    happy_hour_days_since_stopped: int = 0


class SupplierState(BaseModel):
    name: str
    status: str = "Normal"  # Normal | Disrupted | Extended_Outage


class StaffMember(BaseModel):
    skill_level: float = 0.7


# ---------------------------------------------------------------------------
# Day result (returned after service simulation)
# ---------------------------------------------------------------------------


class DayServiceResult(BaseModel):
    total_covers: int = 0
    total_revenue: float = 0.0
    total_walkouts: int = 0
    hourly_metrics: list[HourlyMetrics] = Field(default_factory=list)
    dishes_sold: dict[str, int] = Field(default_factory=dict)
    dishes_unavailable_at: dict[str, str] = Field(default_factory=dict)
    substitution_count: int = 0
    satisfaction_scores: list[float] = Field(default_factory=list)
    mean_satisfaction: float = 0.0
    table_utilization_peak: float = 0.0
    kitchen_bottleneck_hours: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# WorldState (full engine truth — the mutable god object)
# ---------------------------------------------------------------------------


class WorldState(BaseModel):
    # --- Time ---
    day: int = 1
    day_of_week: str = "Monday"

    # --- Economics ---
    cash: float = 15000.0
    revenue_today: float = 0.0
    costs_today: dict[str, float] = Field(default_factory=dict)
    ingredient_spend_today: float = 0.0

    # --- Inventory ---
    inventory: dict[str, list[InventoryBatch]] = Field(default_factory=dict)
    ingredient_shelf_life: dict[str, int] = Field(default_factory=dict)
    waste_today_kg: float = 0.0
    waste_today_cost: float = 0.0

    # --- Menu ---
    recipes: list[Recipe] = Field(default_factory=list)
    active_menu: list[str] = Field(default_factory=list)
    prices: dict[str, float] = Field(default_factory=dict)

    # --- Menu tracking ---
    menu_dish_added_day: dict[str, int] = Field(default_factory=dict)

    # --- Staff ---
    staff: list[StaffMember] = Field(default_factory=list)
    staff_level: int = 8
    queued_staff_change: int | None = None

    # --- Suppliers ---
    suppliers: list[SupplierConfig] = Field(default_factory=list)
    supplier_states: list[SupplierState] = Field(default_factory=list)
    pending_orders: list[PendingOrder] = Field(default_factory=list)
    delivery_history: list[DeliveryRecord] = Field(default_factory=list)

    # --- Customers ---
    cohorts: CohortState = Field(default_factory=CohortState)
    previous_cohort_total: float = 0.0
    walkouts_today: int = 0

    # --- Reputation ---
    reputation_ewma: float = 3.9
    review_queue: list[PendingReview] = Field(default_factory=list)
    posted_reviews: list[PendingReview] = Field(default_factory=list)

    # --- Weather ---
    weather_today: str = "sunny"
    weather_forecast: list[str] = Field(default_factory=list)
    weather_actual_upcoming: list[str] = Field(default_factory=list)

    # --- Scenario ---
    alerts: list[str] = Field(default_factory=list)

    # --- Marketing ---
    marketing_spend: float = 0.0
    promotions: PromotionState = Field(default_factory=PromotionState)

    # --- Agent ---
    agent_notes: str = ""

    # --- Service result (yesterday's) ---
    last_service_result: DayServiceResult | None = None

    # --- Meta ---
    bankrupt: bool = False
    seed: int = 42
    scenario_name: str = "baseline"


# ---------------------------------------------------------------------------
# AgentObservation (filtered view — NO hidden fields)
# ---------------------------------------------------------------------------


class InventoryView(BaseModel):
    model_config = ConfigDict(frozen=True)
    ingredient: str
    total_kg: float
    batches: list[dict]  # [{quantity_kg, expires_in_days}]
    shelf_life_days: int


class SupplierCatalogView(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    lead_time_days: int
    delivery_days: list[str]
    min_order_kg: float
    ingredients: dict[str, float]  # ingredient -> price_per_kg


class RecipeView(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    category: str
    base_price: float
    current_price: float
    is_active: bool
    ingredients: list[dict]  # [{ingredient, quantity_kg}]


class ServiceSummaryView(BaseModel):
    model_config = ConfigDict(frozen=True)
    total_covers: int
    total_revenue: float
    walkout_band: str  # "None"/"Few"/"Some"/"Many"
    hourly_covers: list[int]
    avg_wait_minutes: float
    peak_wait_minutes: float
    dishes_sold: dict[str, int]
    dishes_unavailable_at: dict[str, str]
    substitution_count: int
    table_utilization_peak: float
    kitchen_bottleneck_hours: list[str]


class ReviewView(BaseModel):
    model_config = ConfigDict(frozen=True)
    stars: float
    day_of_visit: int
    day_posted: int


class AgentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Timing
    day: int
    day_of_week: str
    days_remaining: int

    # Financials (exact)
    cash: float
    yesterday_revenue: float = 0.0
    yesterday_total_costs: float = 0.0
    cost_breakdown: dict[str, float] = Field(default_factory=dict)

    # Inventory (exact, with batch detail)
    inventory: list[InventoryView] = Field(default_factory=list)

    # Yesterday's service (hourly granularity, walkouts approximate)
    service_summary: ServiceSummaryView | None = None

    # Suppliers (catalog visible, reliability hidden)
    supplier_catalog: list[SupplierCatalogView] = Field(default_factory=list)
    pending_orders: list[dict] = Field(default_factory=list)
    delivery_history: list[dict] = Field(default_factory=list)

    # Menu
    menu_book: list[RecipeView] = Field(default_factory=list)
    active_menu: list[str] = Field(default_factory=list)

    # Staff
    staff_level: int = 8
    staff_cost_per_person: float = 150.0

    # Reputation (approximate band)
    reputation_band: str = "Good"
    recent_reviews: list[ReviewView] = Field(default_factory=list)

    # Customers (approximate trend)
    customer_trend: str = "Stable"

    # Weather
    weather_today: str = "sunny"
    weather_forecast: list[str] = Field(default_factory=list)

    # Alerts
    alerts: list[str] = Field(default_factory=list)

    # Memory
    notes: str = ""

    # Meta
    tick_budget_ms: int = 30000


# ---------------------------------------------------------------------------
# AgentAction (what the agent submits)
# ---------------------------------------------------------------------------


class PurchaseOrderRequest(BaseModel):
    supplier: str
    ingredient: str
    quantity_kg: float


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    orders: list[PurchaseOrderRequest] = Field(default_factory=list)
    set_active_menu: list[str] | None = None
    set_prices: dict[str, float] | None = None
    set_staff_level: int | None = None
    marketing_spend: float | None = None
    run_happy_hour: bool = False
    offer_daily_special: str | None = None
    notes: str = ""
    reasoning: str = ""


# ---------------------------------------------------------------------------
# API request/response types
# ---------------------------------------------------------------------------


class CreateGameRequest(BaseModel):
    team_name: str
    scenario: str = "baseline"
    seed: int = 42


class ToolCall(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)


class ActionResponse(BaseModel):
    status: str  # "accepted" | "rejected"
    reason: str = ""


class EndTurnResponse(BaseModel):
    observation: AgentObservation
    day: int
    status: str  # "in_progress" | "completed" | "bankrupt"
    day_result: dict = Field(default_factory=dict)


class GameStatusResponse(BaseModel):
    game_id: str
    team_name: str
    scenario: str
    seed: int
    day: int
    status: str
    cash: float


class ScoreBreakdown(BaseModel):
    net_profit: float = 0.0
    satisfaction_penalty: float = 0.0
    reputation_penalty: float = 0.0
    walkout_penalty: float = 0.0
    waste_penalty: float = 0.0
    total_score: float = 0.0


class GameScoreResponse(BaseModel):
    game_id: str = ""
    team_name: str = ""
    scenario: str = "baseline"
    seed: int = 42
    score: ScoreBreakdown
    days_survived: int = 30
    final_cash: float = 0.0
    status: str = "completed"
