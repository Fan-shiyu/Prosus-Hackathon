"""Observation parsing helpers for RestBench agent."""

from typing import Any, Dict, List, Optional


def get_inventory_map(observation: Dict[str, Any]) -> Dict[str, float]:
    """Get a mapping of ingredient names to total kg available."""
    inventory_map = {}
    for inv in observation.get("inventory", []):
        inventory_map[inv["ingredient"]] = inv["total_kg"]
    return inventory_map


def get_fresh_stock(observation: Dict[str, Any]) -> Dict[str, float]:
    """Get stock that won't expire within 1 day (usable for tomorrow)."""
    fresh_stock = {}
    for inv in observation.get("inventory", []):
        long_life = sum(
            b["quantity_kg"] for b in inv.get("batches", [])
            if b["expires_in_days"] > 1
        )
        fresh_stock[inv["ingredient"]] = long_life
    return fresh_stock


def get_usable_stock(observation: Dict[str, Any], days_ahead: int = 2) -> Dict[str, float]:
    """Get stock that will be usable for the next N days."""
    usable_stock = {}
    for inv in observation.get("inventory", []):
        usable = sum(
            b["quantity_kg"] for b in inv.get("batches", [])
            if b["expires_in_days"] > days_ahead
        )
        usable_stock[inv["ingredient"]] = usable
    return usable_stock


def get_pending_orders_map(observation: Dict[str, Any]) -> Dict[str, float]:
    """Get a mapping of ingredient names to pending order quantities."""
    pending = {}
    for po in observation.get("pending_orders", []):
        pending[po["ingredient"]] = pending.get(po["ingredient"], 0) + po["quantity_kg"]
    return pending


def get_supplier_catalog_map(observation: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Get supplier catalog as a nested dict: {supplier_name: {ingredient: price, ...}, ...}"""
    catalog = {}
    for sup in observation.get("supplier_catalog", []):
        catalog[sup["name"]] = {
            "lead_time_days": sup["lead_time_days"],
            "delivery_days": sup["delivery_days"],
            "min_order_kg": sup["min_order_kg"],
            "ingredients": sup["ingredients"],
        }
    return catalog


def get_cheapest_supplier(observation: Dict[str, Any]) -> Dict[str, tuple]:
    """Get cheapest supplier for each ingredient: {ingredient: (supplier_name, price, min_order_kg)}"""
    cheapest = {}
    for sup in observation.get("supplier_catalog", []):
        for ingredient, price in sup["ingredients"].items():
            if ingredient not in cheapest or price < cheapest[ingredient][1]:
                cheapest[ingredient] = (sup["name"], price, sup["min_order_kg"])
    return cheapest


def get_supplier_reliability(observation: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """Calculate supplier reliability from delivery history."""
    reliability = {}
    for delivery in observation.get("delivery_history", []):
        supplier = delivery["supplier"]
        if supplier not in reliability:
            reliability[supplier] = {"total": 0, "success": 0}
        reliability[supplier]["total"] += 1
        reliability[supplier]["success"] += 1 if delivery.get("on_time", True) else 0
    return reliability


def get_supplier_delivery_days(observation: Dict[str, Any]) -> Dict[str, List[str]]:
    """Get delivery days for each supplier."""
    delivery_days = {}
    for sup in observation.get("supplier_catalog", []):
        delivery_days[sup["name"]] = sup["delivery_days"]
    return delivery_days


def get_menu_book_map(observation: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Get menu book as a dict: {dish_name: dish_info, ...}"""
    menu_book = {}
    for dish in observation.get("menu_book", []):
        menu_book[dish["name"]] = dish
    return menu_book


def get_ingredient_consumption(menu_book: Dict[str, Any], dishes_sold: Dict[str, int]) -> Dict[str, float]:
    """Calculate total kg consumed per ingredient based on yesterday's sales."""
    consumption = {}
    for dish_name, dish_info in menu_book.items():
        count = dishes_sold.get(dish_name, 0)
        if count == 0:
            continue
        for ingred in dish_info.get("ingredients", []):
            ingred_name = ingred["ingredient"]
            kg_per_dish = ingred["quantity_kg"]
            consumption[ingred_name] = consumption.get(ingred_name, 0) + kg_per_dish * count
    return consumption


def get_historical_avg_covers(observation: Dict[str, Any]) -> float:
    """Parse historical covers from notes field."""
    notes = observation.get("notes", "")
    # Look for patterns like "avg_covers=105" or "historical_avg: 100"
    import re
    match = re.search(r'(?:avg_covers|historical_avg|avg_cover)[=:]\s*(\d+(?:\.\d+)?)', notes, re.IGNORECASE)
    if match:
        return float(match.group(1))
    
    # Parse from service_summary if available
    service_summary = observation.get("service_summary", {})
    total_covers = service_summary.get("total_covers", 0)
    if total_covers > 0:
        return float(total_covers)
    
    return 100.0  # Default estimate


def can_deliver_today(supplier_name: str, observation: Dict[str, Any]) -> bool:
    """Check if a supplier can deliver today."""
    dow = observation.get("day_of_week", "")
    delivery_days = get_supplier_delivery_days(observation)
    return dow in delivery_days.get(supplier_name, [])


def get_ingredient_shelf_life(observation: Dict[str, Any]) -> Dict[str, int]:
    """Get shelf life in days for each ingredient."""
    shelf_life = {}
    for inv in observation.get("inventory", []):
        shelf_life[inv["ingredient"]] = inv.get("shelf_life_days", 7)
    return shelf_life
