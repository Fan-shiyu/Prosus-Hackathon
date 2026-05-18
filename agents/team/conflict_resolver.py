"""Conflict Resolver Module

Resolves overlapping actions from different modules based on predefined rules.

Conflict Resolution Rules:
| Tool                  | Resolution Strategy                            |
| --------------------- | ---------------------------------------------- |
| place_order           | SUM quantities for same (supplier, ingredient) |
| set_staff_level       | Take HIGHEST (most conservative)               |
| set_price             | Take LAST (most recent decision)               |
| set_marketing_spend   | Take HIGHEST                                   |
| set_menu              | Take LAST                                      |
| offer_daily_special   | Take LAST                                      |
| run_happy_hour        | Take FIRST (only need once)                    |
| save_notes            | CONCATENATE all notes (max 4000 chars)         |
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def resolve_conflicts(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Resolve conflicts between actions from different modules.
    
    Args:
        actions: List of all actions from all modules
        
    Returns:
        List of resolved actions with conflicts removed
    """
    if not actions:
        return actions
    
    try:
        # Group actions by tool type
        grouped = {}
        for action in actions:
            tool = action.get("tool", "")
            if tool not in grouped:
                grouped[tool] = []
            grouped[tool].append(action)
        
        resolved = []
        
        # Resolve each group
        for tool, tool_actions in grouped.items():
            resolved.extend(_resolve_tool_actions(tool, tool_actions))
        
        return resolved
        
    except Exception as e:
        logger.error(f"Error resolving conflicts: {e}")
        return actions


def _resolve_tool_actions(tool: str, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Resolve conflicts for a specific tool type.
    
    Args:
        tool: The tool name
        actions: List of actions for this tool
        
    Returns:
        List of resolved actions
    """
    if len(actions) <= 1:
        return actions
    
    # place_order: SUM quantities for same (supplier, ingredient)
    if tool == "place_order":
        return _resolve_place_order(actions)
    
    # set_staff_level: Take HIGHEST (most conservative)
    if tool == "set_staff_level":
        return _resolve_set_staff_level(actions)
    
    # set_price: Take LAST (most recent decision)
    if tool == "set_price":
        return _resolve_set_price(actions)
    
    # set_marketing_spend: Take HIGHEST
    if tool == "set_marketing_spend":
        return _resolve_set_marketing_spend(actions)
    
    # set_menu: Take LAST
    if tool == "set_menu":
        return _resolve_set_menu(actions)
    
    # offer_daily_special: Take LAST
    if tool == "offer_daily_special":
        return _resolve_offer_daily_special(actions)
    
    # run_happy_hour: Take FIRST (only need once)
    if tool == "run_happy_hour":
        return [actions[0]]
    
    # save_notes: CONCATENATE all notes (max 4000 chars)
    if tool == "save_notes":
        return _resolve_save_notes(actions)
    
    # Default: Take all (no conflict resolution needed)
    return actions


def _resolve_place_order(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Combine place_order actions for same supplier and ingredient."""
    combined = {}
    
    for action in actions:
        args = action.get("args", {})
        supplier = args.get("supplier", "")
        ingredient = args.get("ingredient", "")
        quantity = args.get("quantity_kg", 0)
        
        key = (supplier, ingredient)
        if key in combined:
            combined[key] = combined[key] + quantity
        else:
            combined[key] = quantity
    
    result = []
    for (supplier, ingredient), quantity in combined.items():
        result.append({
            "tool": "place_order",
            "args": {
                "supplier": supplier,
                "ingredient": ingredient,
                "quantity_kg": round(quantity, 1),
            },
        })
    
    return result


def _resolve_set_staff_level(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Take the HIGHEST staff level (most conservative)."""
    max_level = max(action.get("args", {}).get("level", 5) for action in actions)
    return [{
        "tool": "set_staff_level",
        "args": {"level": max_level},
    }]


def _resolve_set_price(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Take the LAST price for each dish."""
    # Use a dict to keep last price per dish
    last_prices = {}
    
    for action in actions:
        args = action.get("args", {})
        dish = args.get("dish", "")
        price = args.get("price", 0)
        last_prices[dish] = price
    
    result = []
    for dish, price in last_prices.items():
        result.append({
            "tool": "set_price",
            "args": {"dish": dish, "price": round(price, 2)},
        })
    
    return result


def _resolve_set_marketing_spend(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Take the HIGHEST marketing spend."""
    max_amount = max(action.get("args", {}).get("amount", 0) for action in actions)
    return [{
        "tool": "set_marketing_spend",
        "args": {"amount": round(max_amount, 2)},
    }]


def _resolve_set_menu(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Take the LAST menu."""
    last_menu = actions[-1]
    return [last_menu]


def _resolve_offer_daily_special(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Take the LAST daily special."""
    last_special = actions[-1]
    return [last_special]


def _resolve_save_notes(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Concatenate all notes (max 4000 chars)."""
    all_texts = [action.get("args", {}).get("text", "") for action in actions]
    combined = " | ".join(all_texts)
    
    # Truncate to 4000 chars
    if len(combined) > 4000:
        combined = combined[:4000]
    
    return [{
        "tool": "save_notes",
        "args": {"text": combined},
    }]
