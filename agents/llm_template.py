"""LLM agent template — copy this and improve the prompt to build your agent.

Uses openai SDK so you can point it at any OpenAI-compatible proxy via
OPENAI_API_BASE env var (e.g. a LiteLLM proxy).

This template sends a trimmed observation to the LLM with a minimal prompt.
It works, but there's a LOT of room to improve:
  - Write a better system prompt with domain strategy
  - Add conversation history so the LLM remembers previous days
  - Filter/summarize the observation to focus on what matters
  - Tune temperature, model choice, etc.
"""

from __future__ import annotations

import json
import os
import sys

import openai

from agents.runner import run_game

MODEL = os.getenv("AGENT_MODEL", "gpt-4.1-mini")

SYSTEM_PROMPT = """\
You manage an Italian restaurant for 30 simulated days. Each day you receive
an observation (JSON) describing your restaurant's state: cash, inventory,
suppliers, menu, reputation, yesterday's service results, and more.

Respond with ONLY a JSON array of tool calls. No explanation, no markdown.

Available tools:
- place_order: {"tool": "place_order", "args": {"supplier": "...", "ingredient": "...", "quantity_kg": N}}
- set_staff_level: {"tool": "set_staff_level", "args": {"level": N}}  (range: 3-15)
- set_price: {"tool": "set_price", "args": {"dish": "...", "price": N}}  (0.8x-1.2x base)
- set_menu: {"tool": "set_menu", "args": {"dishes": [...]}}  (min 5 dishes)
- set_marketing_spend: {"tool": "set_marketing_spend", "args": {"amount": N}}  (0-500 EUR)
- run_happy_hour: {"tool": "run_happy_hour", "args": {}}
- offer_daily_special: {"tool": "offer_daily_special", "args": {"dish": "..."}}
- save_notes: {"tool": "save_notes", "args": {"text": "..."}}  (up to 4000 chars, persists)

Your score = net_profit - penalties (satisfaction, reputation, walkouts, waste).
Going bankrupt (cash < 0) = -100,000 score. Survival is priority #1.

Use the exact supplier, ingredient, and dish names from the observation."""


def _trim_observation(obs: dict) -> dict:
    """Keep only the fields the LLM needs to make decisions."""
    keep = {
        "cash", "day", "staff_level", "marketing_spend",
        "menu", "inventory", "suppliers",
        "reputation", "yesterday",
    }
    return {k: v for k, v in obs.items() if k in keep}


def strategy(observation: dict, day: int) -> list[dict]:
    api_key = os.getenv("OPENAI_API_KEY", "")
    api_base = os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL")
    client = openai.OpenAI(
        api_key=api_key,
        base_url=(api_base.rstrip("/") if api_base else "https://api.openai.com/v1"),
    )

    trimmed = _trim_observation(observation)
    user_msg = f"Day {day}/30. Observation:\n\n{json.dumps(trimmed, indent=2)}"

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=1000,
        )
        content = response.choices[0].message.content.strip()

        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        tool_calls = json.loads(content)
        if not isinstance(tool_calls, list):
            return []
        return tool_calls

    except Exception as e:
        print(f"  LLM error on day {day}: {e}")
        return []


if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        print("Set OPENAI_API_KEY first.")
        print(f"Using model: {MODEL} (override with AGENT_MODEL env var)")
        sys.exit(1)
    print(f"Using model: {MODEL}")
    result = run_game(strategy, team_name="llm_template", seed=42)
