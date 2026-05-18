"""JFAM agent — submission entrypoint for team `JFAM_agents`.

Composes the deterministic L1+L2 core with an optional, sparing L3 LLM
judgment layer, then persists state via save_notes.

  python -m agents.jfam_agent                       # baseline, seed 42
  python -m agents.jfam_agent supply_crisis 88      # scenario, seed
  python -m agents.evaluate agents.jfam_agent ...    # matrix harness

L3 is OFF unless JFAM_LLM_OFF=0 and a key is configured. Pure-rules mode is
fully functional and costs zero tokens.
"""

from __future__ import annotations

import os
import sys

from agents.jfam_core import core_strategy, dump_state, load_dotenv, load_state

load_dotenv()  # must run before runner import (it captures RESTBENCH_URL)

from agents.runner import run_game  # noqa: E402

LLM_OFF = os.getenv("JFAM_LLM_OFF", "1") != "0"

VALID_TOOLS = {
    "place_order", "set_menu", "set_price", "set_staff_level",
    "set_marketing_spend", "run_happy_hour", "offer_daily_special",
    "save_notes",
}


def _sane(actions: list[dict]) -> list[dict]:
    """Drop anything malformed before it wastes a turn on a rejection."""
    out = []
    for a in actions:
        if (isinstance(a, dict) and a.get("tool") in VALID_TOOLS
                and isinstance(a.get("args", {}), dict)):
            out.append(a)
    return out


def make_strategy(params: dict | None = None, use_llm: bool | None = None):
    """Build a strategy(obs, day) closure.

    params:  override jfam_core PARAMS (used by the tuner).
    use_llm: force L3 on/off; None => env (JFAM_LLM_OFF).
    """
    llm_on = (not LLM_OFF) if use_llm is None else use_llm

    def strategy(observation: dict, day: int) -> list[dict]:
        try:
            state = load_state(observation)
            actions, state = core_strategy(observation, day, state, params)

            if llm_on:
                try:
                    from agents.jfam_llm import refine
                    actions, state = refine(observation, day, actions, state)
                except Exception as e:  # never let L3 break a live game
                    print(f"  L3 skipped (day {day}): {e}")

            actions = _sane(actions)
            actions.append({"tool": "save_notes",
                            "args": {"text": dump_state(state)}})
            return actions
        except Exception as e:
            print(f"  core error (day {day}): {e}")
            return []  # safer to skip a turn than crash the game loop

    return strategy


# Module-level strategy for `agents.evaluate` / `python -m agents.jfam_agent`.
strategy = make_strategy()


if __name__ == "__main__":
    scenario = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 42
    team = os.getenv("TEAM_NAME", "JFAM_agents")
    run_game(strategy, team_name=team, scenario=scenario, seed=seed)
