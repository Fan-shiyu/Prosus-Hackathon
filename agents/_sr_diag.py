"""Diagnostic: run baseline seed 42, dump self-review log, active overrides, LLM log."""
from __future__ import annotations
import json
import os
import httpx
from agents.weekend_safe_agent import strategy, _load_state

BASE_URL = os.getenv("RESTBENCH_URL", "http://52.48.183.209:8001")

transport = httpx.HTTPTransport(retries=3)
with httpx.Client(base_url=BASE_URL, timeout=60.0, transport=transport) as client:
    r = client.post("/games", json={
        "team_name": "sr_diag_v1",
        "scenario": "baseline",
        "seed": 42,
    })
    r.raise_for_status()
    data = r.json()
    game_id, observation, day = data["game_id"], data["observation"], data["day"]

    cover_log = []
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
        cover_log.append((td["day"] - 1, td["day_result"]["total_covers"]))
        observation = td["observation"]
        day = td["day"]
        if td["status"] != "in_progress":
            break

    state = _load_state(observation)
    print("=== SELF REVIEW LOG ===")
    for e in state.get("srl", []):
        print(f"  day={e['d']}: {e['ins']}")
    print()
    print("=== ACTIVE OVERRIDES AT END ===")
    for ov in state.get("ao", []):
        print(f"  {ov}")
    print()
    print("=== LLM LOG (last 5) ===")
    for e in state.get("ll", []):
        out = e.get("out", "pending")
        print(f"  day={e['d']}: {e.get('r', '')} | out={out}")
    print()
    print("=== COVER LOG ===")
    for d, cov in cover_log:
        print(f"  day={d}: {cov} covers")
    print()
    r3 = client.get(f"/games/{game_id}/score")
    r3.raise_for_status()
    score_data = r3.json()
    s = score_data["score"]
    print(f"FINAL SCORE: {s['total_score']:.0f}")
    print(f"  net_profit={s['net_profit']:.0f}")
    print(f"  rep_penalty={s['reputation_penalty']:.0f}")
    print(f"  walk_penalty={s['walkout_penalty']:.0f}")
