"""Inventory Module - Stock Management (Person 2)

Never run out of ingredients, minimize waste.

Key Logic:
1. Calculate consumption rate per ingredient from menu_book
2. Use forecast to predict demand for next 3-4 days
3. Maintain safety stock (25% buffer)
4. Respect supplier constraints (min_order_kg, delivery schedules)
5. Avoid double-ordering (check pending_orders)
6. Track expiry dates (FIFO consumption)
7. Diversify suppliers (use top 2 most reliable)
8. Monitor dishes_unavailable_at (PRIMARY SIGNAL)
"""

import logging
from typing import Any, Dict, List, Tuple

from core.observation import (
    get_cheapest_supplier,
    get_fresh_stock,
    get_ingredient_consumption,
    get_ingredient_shelf_life,
    get_menu_book_map,
    get_pending_orders_map,
    get_supplier_catalog_map,
    get_supplier_delivery_days,
    get_supplier_reliability,
)
from core.utils import (
    can_afford,
    clamp,
    get_supplier_top_reliable,
    is_weekend,
)

logger = logging.getLogger(__name__)


def calculate_consumption_per_cover(menu_book: Dict[str, Any]) -> Dict[str, float]:
    """Calculate kg per ingredient per cover (average across all dishes)."""
    total_dishes = len(menu_book)
    if total_dishes == 0:
        return {}
    
    consumption = {}
    for dish_name, dish_info in menu_book.items():
        for ingred in dish_info.get("ingredients", []):
            ingred_name = ingred["ingredient"]
            kg_per_dish = ingred["quantity_kg"]
            consumption[ingred_name] = consumption.get(ingred_name, 0) + kg_per_dish
    
    # Average per dish
    for ingred in consumption:
        consumption[ingred] = consumption[ingred] / total_dishes
    
    return consumption


def get_ingredients_needed(forecast: Dict[str, Any], consumption_per_cover: Dict[str, float]) -> Dict[str, float]:
    """Calculate total kg needed per ingredient based on forecast."""
    total_demand = forecast.get("total_predicted_3day", 0)
    needed = {}
    for ingred, kg_per_cover in consumption_per_cover.items():
        needed[ingred] = kg_per_cover * total_demand
    return needed


def get_usable_stock_by_ingredient(observation: Dict[str, Any], forecast_days: int = 3) -> Dict[str, float]:
    """Get stock that will be usable for the forecast period."""
    usable = {}
    for inv in observation.get("inventory", []):
        ingred = inv["ingredient"]
        shelf_life = inv.get("shelf_life_days", 7)
        
        # Calculate usable stock: batches that won't expire within forecast_days
        usable_kg = sum(
            b["quantity_kg"] for b in inv.get("batches", [])
            if b["expires_in_days"] > forecast_days
        )
        usable[ingred] = usable_kg
    
    return usable


def get_supplier_for_ingredient(
    ingredient: str,
    observation: Dict[str, Any],
    preferred_suppliers: List[str] = None,
) -> Tuple[str, float, float]:
    """
    Get best supplier for an ingredient.
    
    Args:
        ingredient: The ingredient name
        observation: Current observation
        preferred_suppliers: List of preferred supplier names (most reliable)
        
    Returns:
        Tuple of (supplier_name, price, min_order_kg) or (None, 0, 0)
    """
    supplier_catalog = get_supplier_catalog_map(observation)
    
    best_supplier = None
    best_price = float('inf')
    best_min_order = 0
    
    for sup_name, sup_data in supplier_catalog.items():
        if ingredient in sup_data["ingredients"]:
            price = sup_data["ingredients"][ingredient]
            min_order = sup_data["min_order_kg"]
            
            # If we have preferred suppliers, prioritize them
            if preferred_suppliers:
                if sup_name in preferred_suppliers:
                    return (sup_name, price, min_order)
            
            if price < best_price:
                best_price = price
                best_supplier = sup_name
                best_min_order = min_order
    
    if best_supplier:
        return (best_supplier, best_price, best_min_order)
    return (None, 0, 0)


def manage_inventory(observation: Dict[str, Any], day: int, forecast: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Manage inventory: place orders to prevent stockouts.
    
    Args:
        observation: Current observation
        day: Current day number
        forecast: Demand forecast from forecast.py
        
    Returns:
        List of place_order actions
    """
    actions: List[Dict[str, Any]] = []
    
    try:
        cash = observation["cash"]
        inventory = observation.get("inventory", [])
        pending_orders = observation.get("pending_orders", [])
        supplier_catalog = observation.get("supplier_catalog", [])
        active_menu = observation.get("active_menu", [])
        menu_book = get_menu_book_map(observation)
        service_summary = observation.get("service_summary", {})
        alerts = observation.get("alerts", [])
        
        # Get dishes that were unavailable
        dishes_unavailable = service_summary.get("dishes_unavailable_at", {})
        
        # Calculate supplier reliability
        supplier_reliability = get_supplier_reliability(observation)
        preferred_suppliers = get_supplier_top_reliable(supplier_reliability, n=2)
        
        # Get pending orders map
        pending_map = get_pending_orders_map(observation)
        
        # Calculate consumption per cover
        consumption_per_cover = calculate_consumption_per_cover(menu_book)
        
        # Calculate total demand from forecast
        total_predicted = forecast.get("total_predicted_3day", forecast.get("today", {}).get("predicted_demand", 100) * 3)
        
        # Get usable stock (won't expire within 3 days)
        usable_stock = get_usable_stock_by_ingredient(observation, forecast_days=3)
        
        # Get fresh stock (won't expire within 1 day)
        fresh_stock = get_fresh_stock(observation)
        
        # Calculate cash reserve
        cash_reserve = 2000
        available_budget = max(0, cash - cash_reserve)
        
        # Identify ingredients that caused stockouts
        stockout_ingredients = set()
        for dish_name in dishes_unavailable:
            if dish_name in menu_book:
                for ingred in menu_book[dish_name].get("ingredients", []):
                    stockout_ingredients.add(ingred["ingredient"])
        
        # Build list of ingredients to order
        needs: List[Tuple[float, str, str, float, float]] = []  # (priority, ingred, supplier, qty, cost)
        
        for inv in inventory:
            ingred = inv["ingredient"]
            usable = usable_stock.get(ingred, 0)
            pending = pending_map.get(ingred, 0)
            effective_stock = usable + pending
            
            # Calculate required stock
            kg_per_cover = consumption_per_cover.get(ingred, 0)
            required_kg = kg_per_cover * total_predicted
            
            # Apply safety buffer
            if ingred in stockout_ingredients:
                reorder_point = required_kg * 1.5  # 50% extra buffer after stockout
            else:
                reorder_point = required_kg * 1.25  # 25% safety buffer
            
            # Check if we need to order
            if effective_stock < reorder_point:
                # Find best supplier
                supplier_name, price, min_order_kg = get_supplier_for_ingredient(
                    ingred, observation, preferred_suppliers
                )
                
                if supplier_name and price > 0:
                    # Calculate order quantity
                    deficit = reorder_point - effective_stock
                    order_qty = max(deficit, min_order_kg)
                    
                    # Limit by budget
                    max_affordable = available_budget / price if price > 0 else 0
                    order_qty = min(order_qty, max_affordable)
                    
                    # Don't spend >70% of cash on one order
                    max_single_order = cash * 0.7 / price if price > 0 else 0
                    order_qty = min(order_qty, max_single_order)
                    
                    if order_qty >= min_order_kg:
                        cost = order_qty * price
                        priority = 0
                        
                        # Higher priority for stockout ingredients
                        if ingred in stockout_ingredients:
                            priority = 100
                        
                        # Higher priority for weekend
                        if forecast.get("is_weekend", False):
                            priority += 10
                        
                        needs.append((-priority, ingred, supplier_name, order_qty, cost))
        
        # Sort by priority (highest first)
        needs.sort()
        
        # Place orders within budget
        spent = 0.0
        for _, ingred, supplier_name, order_qty, cost in needs:
            if spent + cost > available_budget:
                continue
            
            # Round quantity
            order_qty = round(order_qty, 1)
            
            if order_qty >= 0.1:  # Minimum meaningful order
                actions.append({
                    "tool": "place_order",
                    "args": {
                        "supplier": supplier_name,
                        "ingredient": ingred,
                        "quantity_kg": order_qty,
                    },
                })
                spent += cost
                logger.info(f"Order: {order_qty}kg {ingred} from {supplier_name} (€{cost:.2f})")
        
        if spent > 0:
            logger.info(f"Inventory: Placed orders totaling €{spent:.2f}, remaining budget: €{available_budget - spent:.2f}")
        else:
            logger.info("Inventory: No orders needed")
        
        return actions
        
    except Exception as e:
        logger.error(f"Error in inventory management: {e}")
        import traceback
        traceback.print_exc()
        return []
