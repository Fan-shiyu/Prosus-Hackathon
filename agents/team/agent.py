"""Main Orchestrator - RestBench Ultimate Agent

Combines all modules in a sequential pipeline:
Observation -> Forecast -> Inventory -> Satisfaction -> Helper -> Conflict Resolution -> Actions

This is the main entry point for running the agent.
"""

import argparse
import logging
import sys
from typing import Any, Callable, Dict, List

# Import all modules
from agents.team.forecast import predict_demand
from agents.team.inventory import manage_inventory
from agents.team.satisfaction import track_satisfaction
from agents.team.helper import handle_helper_tasks, save_tracking_notes
from agents.team.conflict_resolver import resolve_conflicts
from agents.runner import run_game

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def strategy(observation: Dict[str, Any], day: int) -> List[Dict[str, Any]]:
    """
    Main strategy function called once per day.
    
    Pipeline:
    1. Forecast demand
    2. Manage inventory (orders)
    3. Track satisfaction (staffing, marketing)
    4. Handle helper tasks (pricing, scenario alerts)
    5. Save tracking notes
    6. Resolve conflicts
    
    Args:
        observation: Current observation from the game
        day: Current day number (1-30)
        
    Returns:
        List of actions to execute
    """
    logger.info(f"=== Day {day} ({observation.get('day_of_week', 'Unknown')}) ===")
    logger.info(f"Cash: €{observation.get('cash', 0):.0f} | Reputation: {observation.get('reputation_band', 'Unknown')}")
    
    try:
        # Step 1: Forecast demand
        forecast = predict_demand(observation, day)
        
        # Step 2: Inventory management (place orders)
        actions = manage_inventory(observation, day, forecast)
        
        # Step 3: Satisfaction tracking (staffing, marketing, specials)
        actions.extend(track_satisfaction(observation, day))
        
        # Step 4: Helper tasks (pricing, scenario handling)
        actions.extend(handle_helper_tasks(observation, day))
        
        # Step 5: Save tracking notes
        actions.extend(save_tracking_notes(observation, day, forecast))
        
        # Step 6: Resolve conflicts between modules
        actions = resolve_conflicts(actions)
        
        logger.info(f"Total actions: {len(actions)}")
        for action in actions:
            logger.debug(f"  Action: {action.get('tool', 'unknown')}")
        
        return actions
        
    except Exception as e:
        logger.error(f"Error in strategy: {e}")
        import traceback
        traceback.print_exc()
        return []


def main():
    """Main entry point when run as a script."""
    parser = argparse.ArgumentParser(
        description="RestBench Ultimate Agent - Run the complete agent"
    )
    parser.add_argument(
        "--scenario",
        default="baseline",
        choices=["baseline", "supply_crisis", "tourist_season", "renovation"],
        help="Scenario to run (default: baseline)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)"
    )
    parser.add_argument(
        "--team-name",
        default="team_ultimate",
        help="Team name for the game (default: team_ultimate)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging (default: INFO)"
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Override RESTBENCH_URL environment variable"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("Verbose mode enabled")
    
    # Set environment variable if provided
    if args.url:
        import os
        os.environ["RESTBENCH_URL"] = args.url
    
    logger.info(f"Starting game: scenario={args.scenario}, seed={args.seed}, team={args.team_name}")
    
    try:
        result = run_game(
            strategy,
            team_name=args.team_name,
            scenario=args.scenario,
            seed=args.seed,
            verbose=True
        )
        
        score = result.get("score", {})
        total_score = score.get("total_score", 0)
        
        logger.info(f"\n{'='*60}")
        logger.info(f"FINAL SCORE: {total_score}")
        logger.info(f"{'='*60}")
        logger.info(f"  Net profit: €{score.get('net_profit', 0):.0f}")
        logger.info(f"  Satisfaction penalty: €{score.get('satisfaction_penalty', 0):.0f}")
        logger.info(f"  Reputation penalty: €{score.get('reputation_penalty', 0):.0f}")
        logger.info(f"  Walkout penalty: €{score.get('walkout_penalty', 0):.0f}")
        logger.info(f"  Waste penalty: €{score.get('waste_penalty', 0):.0f}")
        logger.info(f"  Days survived: {result.get('days_survived', 0)}")
        logger.info(f"  Final cash: €{result.get('final_cash', 0):.0f}")
        logger.info(f"{'='*60}")
        
        return total_score
        
    except Exception as e:
        logger.error(f"Game failed: {e}")
        import traceback
        traceback.print_exc()
        return -100000


if __name__ == "__main__":
    sys.exit(main())
