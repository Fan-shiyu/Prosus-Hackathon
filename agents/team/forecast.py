"""Forecast Module - Demand Prediction (Person 1)

Predicts daily demand using all available signals including:
- Day of week patterns
- Weather (today and forecast)
- Customer trend
- Reputation band
- Recent reviews
- Historical covers from notes
- Scenario alerts
"""

import logging
import re
from typing import Any, Dict, List

from core.observation import get_historical_avg_covers
from core.utils import (
    dow_multipliers,
    get_demand_multiplier,
    rep_multipliers,
    trend_multipliers,
    weather_multipliers,
)

logger = logging.getLogger(__name__)


def predict_demand(observation: Dict[str, Any], day: int) -> Dict[str, Any]:
    """
    Predict demand for today and next 3 days.
    
    Args:
        observation: The current observation dict
        day: Current day number
        
    Returns:
        Dict with demand predictions and metadata
    """
    try:
        # Extract key signals
        dow = observation.get("day_of_week", "Monday")
        weather_today = observation.get("weather_today", "sunny")
        weather_forecast = observation.get("weather_forecast", ["sunny", "sunny", "sunny"])
        customer_trend = observation.get("customer_trend", "Stable")
        reputation = observation.get("reputation_band", "Good")
        recent_reviews = observation.get("recent_reviews", [])
        alerts = observation.get("alerts", [])
        notes = observation.get("notes", "")
        service_summary = observation.get("service_summary", {})
        
        # Calculate historical average covers
        historical_avg = get_historical_avg_covers(observation)
        
        # Get yesterday's covers for trend adjustment
        yesterday_covers = service_summary.get("total_covers", historical_avg)
        
        # Calculate base demand from historical data
        # Use exponential moving average: 70% historical + 30% yesterday
        base_demand = 0.7 * historical_avg + 0.3 * yesterday_covers
        
        # Parse alerts for demand signals
        demand_boost = 0.0
        for alert in alerts:
            alert_lower = alert.lower()
            if "tourist" in alert_lower or "surge" in alert_lower or "rush" in alert_lower:
                demand_boost += 0.30  # 30% boost for tourist surge
            elif "slow" in alert_lower or "quiet" in alert_lower:
                demand_boost -= 0.20  # 20% reduction for slow periods
            elif "renovation" in alert_lower or "construction" in alert_lower:
                demand_boost -= 0.40  # 40% reduction during renovation
            elif "event" in alert_lower or "festival" in alert_lower:
                demand_boost += 0.25  # 25% boost for events
        
        # Calculate today's demand
        multiplier = get_demand_multiplier(
            dow=dow,
            weather=weather_today,
            trend=customer_trend,
            reputation=reputation,
        )
        multiplier += demand_boost
        
        today_demand = int(base_demand * multiplier)
        
        # Ensure minimum demand
        today_demand = max(50, today_demand)
        
        # Determine confidence level
        confidence = "high"
        if abs(yesterday_covers - historical_avg) > historical_avg * 0.3:
            confidence = "medium"
        if demand_boost != 0:
            confidence = "medium"
        if len(recent_reviews) == 0 and day > 5:
            confidence = "low"
        
        # Build next 3 days forecast
        next_days = []
        all_dows = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        current_idx = all_dows.index(dow) if dow in all_dows else 0
        
        for i in range(1, 4):
            next_idx = (current_idx + i) % 7
            next_dow = all_dows[next_idx]
            next_weather = weather_forecast[i-1] if i-1 < len(weather_forecast) else "sunny"
            
            # Adjust demand for each day
            day_multiplier = get_demand_multiplier(
                dow=next_dow,
                weather=next_weather,
                trend=customer_trend,
                reputation=reputation,
            )
            
            # Check for weekend boost
            is_weekend = next_dow in ["Saturday", "Sunday"]
            
            # Apply forecast-specific adjustments
            forecast_demand = int(base_demand * day_multiplier)
            forecast_demand = max(50, forecast_demand)
            
            next_days.append({
                "day": day + i,
                "day_of_week": next_dow,
                "predicted_demand": forecast_demand,
                "weather": next_weather,
            })
        
        # Calculate total predicted demand for next 3-4 days
        total_next_3 = today_demand + sum(d["predicted_demand"] for d in next_days)
        
        result = {
            "today": {
                "day": day,
                "day_of_week": dow,
                "predicted_demand": today_demand,
                "confidence": confidence,
                "weather": weather_today,
            },
            "next_3_days": next_days,
            "historical_avg_covers": int(historical_avg),
            "is_weekend": dow in ["Saturday", "Sunday"],
            "demand_multiplier": round(multiplier, 2),
            "base_demand": int(base_demand),
            "yesterday_covers": yesterday_covers,
            "total_predicted_3day": total_next_3,
        }
        
        logger.info(f"Forecast: Day {day} ({dow}) - Predicted: {today_demand}, "
                   f"Historical: {int(historical_avg)}, Confidence: {confidence}")
        
        return result
        
    except Exception as e:
        logger.error(f"Error in forecast: {e}")
        # Return safe defaults
        dow = observation.get("day_of_week", "Monday")
        return {
            "today": {
                "day": day,
                "day_of_week": dow,
                "predicted_demand": 100,
                "confidence": "low",
                "weather": "sunny",
            },
            "next_3_days": [
                {"day": day+1, "day_of_week": "Tuesday", "predicted_demand": 100, "weather": "sunny"},
                {"day": day+2, "day_of_week": "Wednesday", "predicted_demand": 100, "weather": "sunny"},
                {"day": day+3, "day_of_week": "Thursday", "predicted_demand": 100, "weather": "sunny"},
            ],
            "historical_avg_covers": 100,
            "is_weekend": dow in ["Saturday", "Sunday"],
        }
