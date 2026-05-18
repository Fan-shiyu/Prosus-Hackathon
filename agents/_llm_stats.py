"""One-shot: run baseline 3 seeds, capture LLM stats from notes + day log."""
from __future__ import annotations
import json
import os
import time
import httpx
from agents.weekend_safe_agent import strategy, _load_state

BASE_URL = os.getenv("RESTBENCH_URL", "http://52.48.183.209:8001")
SEEDS = [42, 88, 123]

for seed in SEEDS:
    transport = httpx.HTTPTransport(retries=3)
    with httpx.Client(base_url=BASE_URL, timeout=60.0, transport=transport) as client:
        r = client.post("/games", json={
            "team_name": "llm_stats",
            "scenario": "baseline",
            "seed": seed,
        })
        r.raise_for_status()
        data = r.json()
        game_id, observation, day = data["game_id"], data["observation"], data["day"]

        llm_calls = 0
        llm_skipped = 0
        last_obs = observation

        for _turn in range(30):
            tool_calls = strategy(observation, day)
            for tc in tool_calls:
                try:
                    client.post(f"/games/{game_id}/action", json=tc).raise_for_status()
                except Exception:
                    pass
            r2 = client.post(f"/games/{game_id}/end-turn")
            r2.raise_for_status()
            td = r2.json()
            observation = td["observation"]
            day = td["day"]
            if td["status"] != "in_progress":
                break

        # Extract llm_log from final notes
        state = _load_state(observation)
        ll = state.get("ll", [])
        lc = state.get("lc", 0)

        r3 = client.get(f"/games/{game_id}/score")
        r3.raise_for_status()
        score = r3.json()["score"]["total_score"]

        print(f"\n=== baseline seed={seed} score={score:.0f} ===")
        print(f"  LLM calls made (from state): {lc}")
        print(f"  llm_log entries ({len(ll)}):")
        for entry in ll:
            print(f"    day={entry.get('d','?')}: {entry.get('r','')}")
