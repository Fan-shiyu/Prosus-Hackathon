"""Satisfaction Module - Satisfaction Tracking (Person 3)

Maintains reputation >= "Good", minimizes walkouts.

Key Logic:
- Staffing adjustments based on demand, walkouts, and wait times
- Reputation protection through marketing and specials
- Daily specials and happy hour management
- Track satisfaction metrics in notes
"""

import logging
from typing import Any, Dict, List

from core.utils import is_slow_day, is_weekend

logger = logging.getLogger(__name__)


def calculate_optimal_staff(
    observation: Dict[str, Any],
    day: int,
) -> int:
    """
    Calculate optimal staff level based on multiple factors.
    
    Args:
        observation: Current observation
        day: Current day number
        
    Returns:
        Optimal staff level (5-15)
    """
    # Start with current staff
    current_staff = observation.get("staff_level", 8)
    new_staff = current_staff
    
    dow = observation.get("day_of_week", "Monday")
    service_summary = observation.get("service_summary", {})
    recent_reviews = observation.get("recent_reviews", [])
    
    # Get walkout and wait time info
    walkouts = service_summary.get("walkout_band", "None")
    avg_wait = service_summary.get("avg_wait_minutes", 0)
    peak_wait = service_summary.get("peak_wait_minutes", 0)
    total_covers = service_summary.get("total_covers", 0)
    
    # Weekend boost
    if is_weekend(dow):
        new_staff = max(new_staff, 10)
    
    # Walkout response (URGENT)
    if walkouts in ["Some", "Many"] or avg_wait > 10:
        new_staff = min(new_staff + 2, 15)
        logger.info(f"Walkout/Wait response: Increasing staff to {new_staff} (walkouts: {walkouts}, avg_wait: {avg_wait})")
    
    # High demand response
    if total_covers > 120:
        new_staff = min(new_staff + 1, 15)
    elif total_covers > 150:
        new_staff = min(new_staff + 2, 15)
    
    # Cost optimization - reduce staff on slow days if service is good
    if walkouts == "None" and avg_wait < 5 and not is_weekend(dow):
        new_staff = max(new_staff - 1, 5)  # Never < 5
    
    # Ensure minimum and maximum
    new_staff = max(5, min(new_staff, 15))
    
    return new_staff


def check_reputation_actions(
    observation: Dict[str, Any],
    cash: float,
) -> List[Dict[str, Any]]:
    """
    Generate actions to protect/maintain reputation.
    
    Args:
        observation: Current observation
        cash: Current cash balance
        
    Returns:
        List of actions to improve reputation
    """
    actions: List[Dict[str, Any]] = []
    
    recent_reviews = observation.get("recent_reviews", [])
    reputation = observation.get("reputation_band", "Good")
    active_menu = observation.get("active_menu", [])
    
    if recent_reviews:
        avg_stars = sum(r["stars"] for r in recent_reviews) / len(recent_reviews)
        
        # If reputation is at risk
        if avg_stars < 3.5 and cash > 5000:
            # Boost marketing
            marketing_amount = min(300, cash * 0.05)
            actions.append({
                "tool": "set_marketing_spend",
                "args": {"amount": round(marketing_amount, 2)},
            })
            logger.info(f"Reputation protection: Setting marketing to €{marketing_amount:.2f}")
        
        if avg_stars < 3.0 and active_menu:
            # Offer daily special
            special_dish = active_menu[0] if active_menu else None
            if special_dish:
                actions.append({
                    "tool": "offer_daily_special",
                    "args": {"dish": special_dish},
                })
                logger.info(f"Reputation protection: Offering daily special: {special_dish}")
    
    return actions


def generate_daily_actions(
    observation: Dict[str, Any],
    day: int,
) -> List[Dict[str, Any]]:
    """
    Generate daily satisfaction-related actions.
    
    Args:
        observation: Current observation
        day: Current day number
        
    Returns:
        List of daily actions
    """
    actions: List[Dict[str, Any]] = []
    
    dow = observation.get("day_of_week", "Monday")
    active_menu = observation.get("active_menu", [])
    cash = observation.get("cash", 0)
    service_summary = observation.get("service_summary", {})
    
    # Always offer a daily special (rotate through menu)
    if active_menu and day > 1:
        # Rotate through menu items
        special_idx = (day - 1) % len(active_menu)
        special_dish = active_menu[special_idx]
        actions.append({
            "tool": "offer_daily_special",
            "args": {"dish": special_dish},
        })
        logger.debug(f"Daily special: {special_dish}")
    
    # Use happy hour on slow days (Mon-Wed) if affordable
    if is_slow_day(dow) and cash > 2000:
        # Only run happy hour every other slow day to avoid diminishing returns
        if day % 2 == 0:
            actions.append({
                "tool": "run_happy_hour",
                "args": {},
            })
            logger.info("Running happy hour on slow day")
    
    return actions


def track_satisfaction(observation: Dict[str, Any], day: int) -> List[Dict[str, Any]]:
    """
    Main satisfaction tracking function.
    
    Args:
        observation: Current observation
        day: Current day number
        
    Returns:
        List of satisfaction-related actions
    """
    actions: List[Dict[str, Any]] = []
    
    try:
        cash = observation.get("cash", 0)
        
        # 1. Calculate optimal staff level
        optimal_staff = calculate_optimal_staff(observation, day)
        current_staff = observation.get("staff_level", 8)
        
        if optimal_staff != current_staff:
            actions.append({
                "tool": "set_staff_level",
                "args": {"level": optimal_staff},
            })
            logger.info(f"Setting staff level to {optimal_staff} (was {current_staff})")
        
        # 2. Reputation protection actions
        actions.extend(check_reputation_actions(observation, cash))
        
        # 3. Daily actions (special, happy hour)
        actions.extend(generate_daily_actions(observation, day))
        
        # 4. Set marketing spend
        marketing_actions = set_marketing_spend(observation, cash)
        actions.extend(marketing_actions)
        
        return actions
        
    except Exception as e:
        logger.error(f"Error in satisfaction tracking: {e}")
        return []


def set_marketing_spend(observation: Dict[str, Any], cash: float) -> List[Dict[str, Any]]:
    """
    Calculate and set optimal marketing spend.
    
    Args:
        observation: Current observation
        cash: Current cash balance
        
    Returns:
        List with set_marketing_spend action (if any)
    """
    actions: List[Dict[str, Any]] = []
    
    dow = observation.get("day_of_week", "Monday")
    customer_trend = observation.get("customer_trend", "Stable")
    recent_reviews = observation.get("recent_reviews", [])
    service_summary = observation.get("service_summary", {})
    
    # Calculate base budget
    if is_slow_day(dow):
        base_budget = min(400, cash * 0.05)
    else:
        base_budget = min(200, cash * 0.03)
    
    # Adjust for trend
    if customer_trend == "Declining":
        base_budget *= 1.5
    elif customer_trend == "Growing":
        base_budget *= 0.8  # Reduce spend when growing
    
    # Adjust for reputation
    if recent_reviews:
        avg_stars = sum(r["stars"] for r in recent_reviews) / len(recent_reviews)
        if avg_stars < 3.5:
            base_budget *= 1.5
    
    # Clamp to valid range
    marketing_amount = max(0, min(base_budget, 500))
    
    if marketing_amount > 0:
        actions.append({
            "tool": "set_marketing_spend",
            "args": {"amount": round(marketing_amount, 2)},
        })
        logger.debug(f"Marketing spend: €{marketing_amount:.2f}")
    
    return actions
