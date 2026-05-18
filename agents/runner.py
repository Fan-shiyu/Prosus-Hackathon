"""Reusable agent runner — plays a full game via the RestBench HTTP API.

Enhanced version with better error handling for the Ultimate Agent.

Usage:
    from agents.runner import run_game
    from agents.team.agent import strategy

    result = run_game(strategy, base_url="http://localhost:8001", team_name="ultimate", seed=42)
    print(result)

A strategy is a callable: (observation: dict, day: int) -> list[dict]
Each dict in the list is a tool call: {"tool": "place_order", "args": {...}}
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

Strategy = Callable[[dict, int], list[dict]]

DEFAULT_URL = os.getenv("RESTBENCH_URL", "http://52.48.183.209:8001")


def run_game(
    strategy: Strategy,
    *,
    base_url: str = DEFAULT_URL,
    team_name: str = "agent",
    scenario: str = "baseline",
    seed: int = 42,
    verbose: bool = True,
    timeout: float = 60.0,
) -> dict:
    """
    Run a full game with the given strategy.
    
    Args:
        strategy: Callable that takes (observation, day) and returns list of actions
        base_url: REST API base URL
        team_name: Team name for the game
        scenario: Scenario to run (baseline, supply_crisis, tourist_season, renovation)
        seed: Random seed for reproducibility
        verbose: Whether to print progress
        timeout: HTTP timeout in seconds
        
    Returns:
        Dict with score data and game results
    """
    transport = httpx.HTTPTransport(retries=5)
    
    try:
        with httpx.Client(base_url=base_url, timeout=timeout, transport=transport) as client:
            # Create game
            r = client.post("/games", json={
                "team_name": team_name,
                "scenario": scenario,
                "seed": seed,
            })
            r.raise_for_status()
            data = r.json()
            game_id = data["game_id"]
            observation = data["observation"]
            day = data["day"]

            if verbose:
                print(f"Game {game_id} created — Day {day}, Cash: €{observation['cash']:.0f}")
            
            logger.info(f"Game {game_id} started: scenario={scenario}, seed={seed}")

            # Main game loop (30 days)
            for turn in range(30):
                try:
                    # Get actions from strategy
                    tool_calls = strategy(observation, day)
                except Exception as e:
                    logger.error(f"Strategy error on Day {day}: {e}")
                    import traceback
                    traceback.print_exc()
                    tool_calls = []

                # Submit actions
                accepted = 0
                rejected = 0
                for tc in tool_calls:
                    try:
                        r = client.post(f"/games/{game_id}/action", json=tc)
                        r.raise_for_status()
                        result = r.json()
                        if result["status"] == "accepted":
                            accepted += 1
                        else:
                            rejected += 1
                            if verbose:
                                print(f"  Day {day}: REJECTED {tc.get('tool', 'unknown')}: {result.get('reason', 'No reason')}")
                            logger.warning(f"Day {day}: REJECTED {tc.get('tool', 'unknown')}: {result.get('reason', 'No reason')}")
                    except httpx.HTTPError as e:
                        logger.error(f"HTTP error submitting action on Day {day}: {e}")
                        rejected += 1
                    except Exception as e:
                        logger.error(f"Error submitting action on Day {day}: {e}")
                        rejected += 1

                # End turn
                try:
                    r = client.post(f"/games/{game_id}/end-turn")
                    r.raise_for_status()
                    turn_data = r.json()
                except httpx.HTTPError as e:
                    logger.error(f"HTTP error ending turn on Day {day}: {e}")
                    break
                except Exception as e:
                    logger.error(f"Error ending turn on Day {day}: {e}")
                    break

                observation = turn_data["observation"]
                day = turn_data["day"]
                status = turn_data["status"]
                dr = turn_data.get("day_result", {})

                if verbose:
                    print(
                        f"  Day {day-1}: covers={dr.get('total_covers', 0)}, "
                        f"revenue=€{dr.get('total_revenue', 0):.0f}, "
                        f"cash=€{observation.get('cash', 0):.0f}, "
                        f"actions={accepted}ok/{rejected}rej"
                    )
                
                logger.debug(f"Day {day-1} result: covers={dr.get('total_covers', 0)}, "
                            f"revenue=€{dr.get('total_revenue', 0):.0f}, "
                            f"cash=€{observation.get('cash', 0):.0f}")

                # Check if game ended
                if status != "in_progress":
                    if verbose:
                        print(f"Game ended: {status}")
                    logger.info(f"Game ended on Day {day-1}: {status}")
                    break

            # Get final score
            try:
                r = client.get(f"/games/{game_id}/score")
                r.raise_for_status()
                score_data = r.json()
            except Exception as e:
                logger.error(f"Error getting score: {e}")
                return {
                    "error": str(e),
                    "game_id": game_id,
                    "days_survived": day - 1,
                }

            if verbose:
                s = score_data.get('score', {})
                print(f"\nFinal score: {s.get('total_score', 0)}")
                print(f"  Net profit: €{s.get('net_profit', 0):.0f}")
                print(f"  Satisfaction penalty: €{s.get('satisfaction_penalty', 0):.0f}")
                print(f"  Reputation penalty: €{s.get('reputation_penalty', 0):.0f}")
                print(f"  Walkout penalty: €{s.get('walkout_penalty', 0):.0f}")
                print(f"  Waste penalty: €{s.get('waste_penalty', 0):.0f}")
                print(f"  Days survived: {score_data.get('days_survived', 0)}")
                print(f"  Final cash: €{score_data.get('final_cash', 0):.0f}")

            logger.info(f"Game {game_id} completed with score: {s.get('total_score', 0)}")

            return score_data

    except httpx.HTTPError as e:
        logger.error(f"HTTP error in run_game: {e}")
        return {"error": f"HTTP error: {e}"}
    except Exception as e:
        logger.error(f"Error in run_game: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


if __name__ == "__main__":
    print("Use: python -m agents.team.agent")
