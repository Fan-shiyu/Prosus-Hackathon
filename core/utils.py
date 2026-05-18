"""Shared utility functions for RestBench agent."""

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Multipliers for demand forecasting
dow_multipliers = {
    "Monday": 0.80,
    "Tuesday": 0.85,
    "Wednesday": 0.90,
    "Thursday": 1.00,
    "Friday": 1.20,
    "Saturday": 1.40,
    "Sunday": 1.30,
}

weather_multipliers = {
    "sunny": 1.00,
    "cloudy": 0.95,
    "rainy": 0.80,
    "stormy": 0.70,
}

trend_multipliers = {
    "Declining": 0.90,
    "Stable": 1.00,
    "Growing": 1.10,
}

rep_multipliers = {
    "Poor": 0.70,
    "Fair": 0.85,
    "Good": 1.00,
    "Very Good": 1.10,
    "Excellent": 1.20,
}


def get_demand_multiplier(dow: str, weather: str, trend: str, reputation: str) -> float:
    """Calculate combined demand multiplier."""
    d_mult = dow_multipliers.get(dow, 1.0)
    w_mult = weather_multipliers.get(weather, 1.0)
    t_mult = trend_multipliers.get(trend, 1.0)
    r_mult = rep_multipliers.get(reputation, 1.0)
    return d_mult * w_mult * t_mult * r_mult


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max."""
    return max(min_val, min(max_val, value))


def round_price(price: float) -> float:
    """Round price to 2 decimal places."""
    return round(price, 2)


def validate_price(dish_name: str, price: float, menu_book: Dict[str, Any]) -> bool:
    """Check if price is within valid range (0.8x to 1.2x base price)."""
    if dish_name not in menu_book:
        return False
    base_price = menu_book[dish_name]["base_price"]
    min_price = base_price * 0.8
    max_price = base_price * 1.2
    return min_price <= price <= max_price


def get_cash_reserve(cash: float, min_reserve: float = 2000.0) -> float:
    """Calculate available budget after maintaining cash reserve."""
    return max(0, cash - min_reserve)


def format_notes(**kwargs) -> str:
    """Format notes from keyword arguments."""
    parts = []
    for key, value in kwargs.items():
        parts.append(f"{key}={value}")
    return " | ".join(parts)


def parse_notes(notes: str) -> Dict[str, Any]:
    """Parse notes string into a dictionary."""
    result = {}
    if not notes:
        return result
    parts = notes.split("|")
    for part in parts:
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            key = key.strip()
            value = value.strip()
            # Try to convert to numeric
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass
            result[key] = value
    return result


def get_dishes_unavailable_at(observation: Dict[str, Any]) -> Dict[str, int]:
    """Get dishes that were unavailable and when."""
    service_summary = observation.get("service_summary", {})
    return service_summary.get("dishes_unavailable_at", {})


def get_alerts(observation: Dict[str, Any]) -> List[str]:
    """Get alerts from observation."""
    return observation.get("alerts", [])


def is_weekend(dow: str) -> bool:
    """Check if day of week is weekend."""
    return dow in ["Saturday", "Sunday"]


def is_slow_day(dow: str) -> bool:
    """Check if day of week is typically slow."""
    return dow in ["Monday", "Tuesday", "Wednesday"]


def get_supplier_top_reliable(supplier_reliability: Dict[str, Dict[str, int]], n: int = 2) -> List[str]:
    """Get top N most reliable suppliers by success rate."""
    if not supplier_reliability:
        return []
    
    # Sort by success rate (success/total), then by name for tie-breaking
    sorted_suppliers = sorted(
        supplier_reliability.items(),
        key=lambda x: (x[1]["success"] / max(1, x[1]["total"]), x[0]),
        reverse=True,
    )
    return [s[0] for s in sorted_suppliers[:n]]


def can_afford(cash: float, cost: float, reserve_ratio: float = 0.7) -> bool:
    """Check if we can afford an expense while maintaining reserve."""
    return cost <= cash * reserve_ratio
