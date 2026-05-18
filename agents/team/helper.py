"""Helper Module - Dynamic Pricing and Marketing (Person 4)

Handles:
- Dynamic pricing (rule-based with LLM fallback)
- Marketing budget management
- Scenario handling (alerts)
- Happy hour and specials coordination
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from agents.prompts.pricing_prompt import PRICING_PROMPT
from core.observation import get_menu_book_map
from core.utils import (
    clamp,
    is_slow_day,
    round_price,
    trend_multipliers,
    validate_price,
    weather_multipliers,
)

logger = logging.getLogger(__name__)


def rule_based_pricing(observation: Dict[str, Any], day: int) -> List[Dict[str, Any]]:
    """
    Rule-based pricing strategy.
    
    Args:
        observation: Current observation
        day: Current day number
        
    Returns:
        List of set_price actions
    """
    actions: List[Dict[str, Any]] = []
    
    try:
        active_menu = observation.get("active_menu", [])
        menu_book = get_menu_book_map(observation)
        service_summary = observation.get("service_summary", {})
        dishes_sold = service_summary.get("dishes_sold", {})
        weather = observation.get("weather_today", "sunny")
        customer_trend = observation.get("customer_trend", "Stable")
        
        for dish in active_menu:
            if dish not in menu_book:
                continue
            
            dish_info = menu_book[dish]
            base_price = dish_info["base_price"]
            current_price = dish_info.get("current_price", base_price)
            count = dishes_sold.get(dish, 0)
            
            # Start with base price
            new_price = base_price
            
            # Popularity adjustments
            if count > 20:
                new_price = base_price * 1.10
            elif count > 15:
                new_price = base_price * 1.05
            elif count > 10:
                new_price = base_price * 1.02
            elif count < 5:
                new_price = base_price * 0.90
            elif count < 3:
                new_price = base_price * 0.85
            
            # Weather adjustments
            if weather in ["rainy", "stormy"]:
                new_price *= 0.95
            
            # Trend adjustments
            if customer_trend == "Declining":
                new_price *= 0.90
            elif customer_trend == "Growing":
                new_price *= 1.05
            
            # Clamp to valid range
            min_price = base_price * 0.8
            max_price = base_price * 1.2
            new_price = clamp(new_price, min_price, max_price)
            
            # Round to 2 decimal places
            new_price = round_price(new_price)
            
            # Only set price if it changes significantly
            if abs(new_price - current_price) > 0.50:
                actions.append({
                    "tool": "set_price",
                    "args": {
                        "dish": dish,
                        "price": new_price,
                    },
                })
                logger.debug(f"Price adjustment: {dish} from €{current_price:.2f} to €{new_price:.2f}")
        
        return actions
        
    except Exception as e:
        logger.error(f"Error in rule-based pricing: {e}")
        return []


def llm_pricing(observation: Dict[str, Any], day: int) -> List[Dict[str, Any]]:
    """
    LLM-based pricing using the PRICING_PROMPT.
    
    Args:
        observation: Current observation
        day: Current day number
        
    Returns:
        List of set_price actions (or empty list on error)
    """
    try:
        import httpx
        import os
        
        OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        MODEL = os.getenv("AGENT_MODEL", "openai/gpt-4.1-mini")
        OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://litellm-production.eba-pvykax23.eu-west-1.elasticbeanstalk.com/")
        
        if not OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY not set, falling back to rule-based pricing")
            return rule_based_pricing(observation, day)
        
        active_menu = observation.get("active_menu", [])
        menu_book = get_menu_book_map(observation)
        service_summary = observation.get("service_summary", {})
        dishes_sold = service_summary.get("dishes_sold", {})
        
        # Format observation for prompt
        obs_str = json.dumps({
            "day_of_week": observation.get("day_of_week", ""),
            "weather_today": observation.get("weather_today", ""),
            "customer_trend": observation.get("customer_trend", ""),
            "reputation_band": observation.get("reputation_band", ""),
            "recent_reviews": observation.get("recent_reviews", []),
        }, indent=2)
        
        menu_str = json.dumps({
            dish: {
                "base_price": info["base_price"],
                "ingredients": info.get("ingredients", []),
            }
            for dish, info in menu_book.items()
        }, indent=2)
        
        prompt = PRICING_PROMPT.format(
            observation=obs_str,
            active_menu=json.dumps(active_menu),
            dishes_sold=json.dumps(dishes_sold),
            menu_book=menu_str,
        )
        
        # Call LiteLLM endpoint
        client = httpx.Client(base_url=OPENAI_BASE_URL, timeout=10.0)
        response = client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 500,
            },
        )
        
        if response.status_code != 200:
            logger.warning(f"LLM pricing failed with status {response.status_code}, falling back to rule-based")
            return rule_based_pricing(observation, day)
        
        result = response.json()
        content = result["choices"][0]["message"]["content"].strip()
        
        # Parse JSON response
        try:
            # Try to extract JSON from response
            if content.startswith("["):
                actions_data = json.loads(content)
                if isinstance(actions_data, list):
                    actions = []
                    for action in actions_data:
                        if isinstance(action, dict) and action.get("tool") == "set_price":
                            actions.append(action)
                    return actions
        except json.JSONDecodeError:
            logger.warning("LLM returned non-JSON response, falling back to rule-based")
        
        return rule_based_pricing(observation, day)
        
    except Exception as e:
        logger.error(f"Error in LLM pricing: {e}")
        return rule_based_pricing(observation, day)


def handle_scenario_alerts(observation: Dict[str, Any], day: int) -> List[Dict[str, Any]]:
    """
    Handle scenario-specific alerts.
    
    Args:
        observation: Current observation
        day: Current day number
        
    Returns:
        List of actions in response to alerts
    """
    actions: List[Dict[str, Any]] = []
    
    alerts = observation.get("alerts", [])
    cash = observation.get("cash", 0)
    dow = observation.get("day_of_week", "Monday")
    
    for alert in alerts:
        alert_lower = alert.lower()
        
        # Supplier outage - handled in inventory.py through diversification
        if "supplier" in alert_lower and "outage" in alert_lower:
            logger.info(f"Alert: Supplier outage detected - {alert}")
            # Save to notes for tracking
            actions.append({
                "tool": "save_notes",
                "args": {"text": f"Day {day}: SUPPLIER ALERT - {alert}"},
            })
        
        # Tourist season or demand surge
        elif "tourist" in alert_lower or "surge" in alert_lower:
            logger.info(f"Alert: Demand surge detected - {alert}")
            actions.append({
                "tool": "set_staff_level",
                "args": {"level": 12},
            })
            actions.append({
                "tool": "save_notes",
                "args": {"text": f"Day {day}: DEMAND SURGE - {alert}"},
            })
        
        # Renovation
        elif "renovation" in alert_lower:
            logger.info(f"Alert: Renovation detected - {alert}")
            actions.append({
                "tool": "set_staff_level",
                "args": {"level": 6},
            })
            actions.append({
                "tool": "save_notes",
                "args": {"text": f"Day {day}: RENOVATION - {alert}"},
            })
        
        # Bad weather warning
        elif "storm" in alert_lower or "extreme" in alert_lower:
            # Reduce prices to attract customers
            active_menu = observation.get("active_menu", [])
            menu_book = get_menu_book_map(observation)
            for dish in active_menu[:3]:  # Apply discount to first 3 dishes
                if dish in menu_book:
                    base_price = menu_book[dish]["base_price"]
                    discounted = base_price * 0.90
                    actions.append({
                        "tool": "set_price",
                        "args": {"dish": dish, "price": round_price(discounted)},
                    })
    
    return actions


def save_tracking_notes(observation: Dict[str, Any], day: int, forecast: Optional[Dict] = None) -> List[Dict[str, Any]]:
    """
    Save tracking information to notes.
    
    Args:
        observation: Current observation
        day: Current day number
        forecast: Optional forecast data
        
    Returns:
        List with save_notes action
    """
    try:
        service_summary = observation.get("service_summary", {})
        cash = observation.get("cash", 0)
        reputation = observation.get("reputation_band", "Unknown")
        
        notes_parts = [
            f"Day {day}",
            f"cash={cash:.0f}",
            f"reputation={reputation}",
        ]
        
        if service_summary:
            covers = service_summary.get("total_covers", 0)
            revenue = service_summary.get("total_revenue", 0)
            walkouts = service_summary.get("walkout_band", "None")
            avg_wait = service_summary.get("avg_wait_minutes", 0)
            notes_parts.extend([
                f"covers={covers}",
                f"revenue={revenue:.0f}",
                f"walkouts={walkouts}",
                f"avg_wait={avg_wait:.1f}",
            ])
        
        if forecast:
            predicted = forecast.get("today", {}).get("predicted_demand", 0)
            confidence = forecast.get("today", {}).get("confidence", "unknown")
            notes_parts.extend([
                f"predicted={predicted}",
                f"confidence={confidence}",
            ])
        
        notes_text = " | ".join(notes_parts)
        
        # Truncate to 4000 chars
        if len(notes_text) > 4000:
            notes_text = notes_text[:4000]
        
        return [{
            "tool": "save_notes",
            "args": {"text": notes_text},
        }]
        
    except Exception as e:
        logger.error(f"Error saving notes: {e}")
        return []


def handle_helper_tasks(observation: Dict[str, Any], day: int) -> List[Dict[str, Any]]:
    """
    Main helper function that coordinates all helper tasks.
    
    Args:
        observation: Current observation
        day: Current day number
        
    Returns:
        List of pricing/marketing actions
    """
    actions: List[Dict[str, Any]] = []
    
    try:
        # 1. Handle scenario alerts
        actions.extend(handle_scenario_alerts(observation, day))
        
        # 2. Pricing
        # Try LLM first, fall back to rule-based
        OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        if OPENAI_API_KEY:
            try:
                pricing_actions = llm_pricing(observation, day)
                actions.extend(pricing_actions)
            except Exception as e:
                logger.warning(f"LLM pricing failed, using rule-based: {e}")
                actions.extend(rule_based_pricing(observation, day))
        else:
            actions.extend(rule_based_pricing(observation, day))
        
        return actions
        
    except Exception as e:
        logger.error(f"Error in helper tasks: {e}")
        return []
