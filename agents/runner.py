"""Reusable agent runner — plays a full game via the RestBench HTTP API.

Usage:
    from agents.runner import run_game
    from agents.naive_rule import strategy

    result = run_game(strategy, base_url="http://localhost:8001", team_name="naive", seed=42)
    print(result)

A strategy is a callable: (observation: dict, day: int) -> list[dict]
Each dict in the list is a tool call: {"tool": "place_order", "args": {...}}
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
import time
from typing import Callable

import httpx

Strategy = Callable[[dict, int], list[dict]]

DEFAULT_URL = os.getenv("RESTBENCH_URL", "http://localhost:8001")


def run_game(
    strategy: Strategy,
    *,
    base_url: str = DEFAULT_URL,
    team_name: str = "agent",
    scenario: str = "baseline",
    seed: int = 42,
    verbose: bool = True,
    log_dir: str | None = "logs",
) -> dict:
    transport = httpx.HTTPTransport(retries=3)
    with httpx.Client(base_url=base_url, timeout=60.0, transport=transport) as client:
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
            print(f"Game {game_id} created — Day {day}, Cash: {observation['cash']}")

        _log = None
        if log_dir:
            pathlib.Path(log_dir).mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"{team_name}_{scenario}_s{seed}_{ts}.jsonl"
            _log = open(pathlib.Path(log_dir) / fname, "w", encoding="utf-8")
            _log.write(json.dumps({
                "type": "meta",
                "game_id": game_id,
                "scenario": scenario,
                "seed": seed,
                "team": team_name,
                "ts": datetime.datetime.now().isoformat(),
            }) + "\n")
            _log.flush()

        try:
            for turn in range(30):
                _cur_day = day
                _dow = observation.get("day_of_week", "?")
                _cash_start = float(observation.get("cash", 0))
                _rep = observation.get("reputation_band", "?")
                _staff = observation.get("staff_level", 5)
                _mkt = observation.get("marketing_spend", 0)

                tool_calls = strategy(observation, day)

                accepted = 0
                rejected = 0
                for tc in tool_calls:
                    r = client.post(f"/games/{game_id}/action", json=tc)
                    r.raise_for_status()
                    result = r.json()
                    if result["status"] == "accepted":
                        accepted += 1
                    else:
                        rejected += 1
                        if verbose:
                            print(f"  Day {day}: REJECTED {tc['tool']}: {result['reason']}")

                r = client.post(f"/games/{game_id}/end-turn")
                r.raise_for_status()
                turn_data = r.json()

                observation = turn_data["observation"]
                day = turn_data["day"]
                status = turn_data["status"]
                dr = turn_data["day_result"]

                if verbose:
                    print(
                        f"  Day {day-1}: covers={dr['total_covers']}, "
                        f"revenue={dr['total_revenue']}, "
                        f"cash={observation['cash']:.0f}, "
                        f"actions={accepted}ok/{rejected}rej"
                    )

                if _log:
                    new_ss = observation.get("service_summary") or {}
                    walkouts = dr.get("walkout_band") or new_ss.get("walkout_band", "None")
                    _log.write(json.dumps({
                        "type": "day",
                        "day": _cur_day,
                        "dow": _dow,
                        "cash_start": round(_cash_start, 2),
                        "cash_end": round(float(observation.get("cash", 0)), 2),
                        "covers": dr.get("total_covers", 0),
                        "revenue": round(float(dr.get("total_revenue") or 0), 2),
                        "walkouts": walkouts or "None",
                        "rep": _rep,
                        "staff": _staff,
                        "mkt": _mkt,
                        "ok": accepted,
                        "rej": rejected,
                    }) + "\n")
                    _log.flush()

                if status != "in_progress":
                    if verbose:
                        print(f"Game ended: {status}")
                    break

            r = client.get(f"/games/{game_id}/score")
            r.raise_for_status()
            score_data = r.json()

            if verbose:
                s = score_data['score']
                print(f"\nFinal score: {s['total_score']}")
                print(f"  Net profit: {s['net_profit']}")
                print(f"  Satisfaction penalty: {s['satisfaction_penalty']}")
                print(f"  Reputation penalty: {s['reputation_penalty']}")
                print(f"  Walkout penalty: {s['walkout_penalty']}")
                print(f"  Waste penalty: {s['waste_penalty']}")
                print(f"  Days survived: {score_data['days_survived']}")
                print(f"  Final cash: {score_data['final_cash']}")

            if _log:
                s = score_data["score"]
                _log.write(json.dumps({
                    "type": "summary",
                    "score": s["total_score"],
                    "net_profit": s["net_profit"],
                    "sat_pen": s["satisfaction_penalty"],
                    "rep_pen": s["reputation_penalty"],
                    "walk_pen": s["walkout_penalty"],
                    "waste_pen": s["waste_penalty"],
                    "days": score_data["days_survived"],
                    "final_cash": round(float(score_data.get("final_cash", 0)), 2),
                    "status": score_data.get("status", "?"),
                }) + "\n")

            return score_data

        finally:
            if _log:
                _log.close()


if __name__ == "__main__":
    print("Use: python -m agents.do_nothing / agents.naive_rule / agents.starter_template")
