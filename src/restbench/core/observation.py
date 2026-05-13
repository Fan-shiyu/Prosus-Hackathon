"""Information boundary: WorldState → AgentObservation.

This is an allowlist filter. Fields are explicitly added to the observation.
Fields that exist in WorldState but not in AgentObservation cannot leak.
"""

from __future__ import annotations

from restbench.core.types import (
    AgentObservation,
    InventoryView,
    RecipeView,
    ReviewView,
    ServiceSummaryView,
    SupplierCatalogView,
    WorldState,
)
from restbench.engine.cohorts import compute_customer_trend
from restbench.engine.tuning import TuningConfig


def make_observation(world: WorldState, tuning: TuningConfig) -> AgentObservation:
    inventory = _make_inventory_view(world)
    supplier_catalog = _make_supplier_catalog(world)
    menu_book = _make_menu_book(world)
    service_summary = _make_service_summary(world)
    reputation_band = _reputation_to_band(world.reputation_ewma, tuning)
    recent_reviews = _get_recent_reviews(world)
    customer_trend = compute_customer_trend(world)

    pending = [
        {"supplier": o.supplier, "ingredient": o.ingredient,
         "quantity_kg": o.quantity_kg, "delivery_day": o.delivery_day}
        for o in world.pending_orders
    ]
    delivery_hist = [
        {"supplier": d.supplier, "ingredient": d.ingredient,
         "ordered_kg": d.ordered_kg, "delivered_kg": d.delivered_kg,
         "on_time": d.on_time, "delivery_day": d.delivery_day}
        for d in world.delivery_history[-14:]  # last 14 days
    ]

    return AgentObservation(
        day=world.day,
        day_of_week=world.day_of_week,
        days_remaining=max(0, tuning.simulation.total_days - world.day),
        cash=round(world.cash, 2),
        yesterday_revenue=round(world.revenue_today, 2),
        yesterday_total_costs=round(sum(world.costs_today.values()), 2),
        cost_breakdown={k: round(v, 2) for k, v in world.costs_today.items()},
        inventory=inventory,
        service_summary=service_summary,
        supplier_catalog=supplier_catalog,
        pending_orders=pending,
        delivery_history=delivery_hist,
        menu_book=menu_book,
        active_menu=list(world.active_menu),
        staff_level=world.staff_level,
        staff_cost_per_person=tuning.economics.staff_cost_per_day,
        reputation_band=reputation_band,
        recent_reviews=recent_reviews,
        customer_trend=customer_trend,
        weather_today=world.weather_today,
        weather_forecast=list(world.weather_forecast),
        alerts=list(world.alerts),
        notes=world.agent_notes,
    )


def _make_inventory_view(world: WorldState) -> list[InventoryView]:
    views = []
    for ingredient, batches in sorted(world.inventory.items()):
        total = sum(b.quantity_kg for b in batches)
        batch_views = [
            {"quantity_kg": round(b.quantity_kg, 3), "expires_in_days": b.expires_in_days}
            for b in batches if b.quantity_kg > 0.001
        ]
        shelf_life = max((b.expires_in_days for b in batches), default=0)
        views.append(InventoryView(
            ingredient=ingredient,
            total_kg=round(total, 3),
            batches=batch_views,
            shelf_life_days=shelf_life,
        ))
    return views


def _make_supplier_catalog(world: WorldState) -> list[SupplierCatalogView]:
    return [
        SupplierCatalogView(
            name=s.name,
            lead_time_days=s.lead_time_days,
            delivery_days=s.delivery_days,
            min_order_kg=s.min_order_kg,
            ingredients={i.ingredient: i.price_per_kg for i in s.ingredients},
        )
        for s in world.suppliers
    ]


def _make_menu_book(world: WorldState) -> list[RecipeView]:
    return [
        RecipeView(
            name=r.name,
            category=r.category,
            base_price=r.base_price,
            current_price=world.prices.get(r.name, r.base_price),
            is_active=r.name in world.active_menu,
            ingredients=[
                {"ingredient": i.ingredient, "quantity_kg": i.quantity_kg}
                for i in r.ingredients
            ],
        )
        for r in world.recipes
    ]


def _make_service_summary(world: WorldState) -> ServiceSummaryView | None:
    result = world.last_service_result
    if result is None:
        return None

    walkouts = result.total_walkouts
    if walkouts == 0:
        band = "None"
    elif walkouts <= 5:
        band = "Few"
    elif walkouts <= 20:
        band = "Some"
    else:
        band = "Many"

    hourly_covers = [0] * len([h for h in range(11, 23)])
    for m in result.hourly_metrics:
        idx = m.hour - 11
        if 0 <= idx < len(hourly_covers):
            hourly_covers[idx] = m.covers

    avg_wait = 0.0
    peak_wait = 0.0
    for m in result.hourly_metrics:
        avg_wait += m.wait_minutes_avg
        peak_wait = max(peak_wait, m.wait_minutes_peak)
    if result.hourly_metrics:
        avg_wait /= len(result.hourly_metrics)

    return ServiceSummaryView(
        total_covers=result.total_covers,
        total_revenue=round(result.total_revenue, 2),
        walkout_band=band,
        hourly_covers=hourly_covers,
        avg_wait_minutes=round(avg_wait, 1),
        peak_wait_minutes=round(peak_wait, 1),
        dishes_sold=dict(result.dishes_sold),
        dishes_unavailable_at=dict(result.dishes_unavailable_at),
        substitution_count=result.substitution_count,
        table_utilization_peak=result.table_utilization_peak,
        kitchen_bottleneck_hours=list(result.kitchen_bottleneck_hours),
    )


def _reputation_to_band(ewma: float, tuning: TuningConfig) -> str:
    bands = tuning.reputation.bands
    if ewma < bands["Poor"]:
        return "Poor"
    elif ewma < bands["Fair"]:
        return "Fair"
    elif ewma < bands["Good"]:
        return "Good"
    elif ewma < bands["Very Good"]:
        return "Very Good"
    else:
        return "Excellent"


def _get_recent_reviews(world: WorldState) -> list[ReviewView]:
    recent = [r for r in world.posted_reviews if r.post_day >= world.day - 14]
    return [
        ReviewView(stars=r.stars, day_of_visit=r.day_of_visit, day_posted=r.post_day)
        for r in recent
    ]
