"""Detailed evaluation capturing zero-cover days, final rep, top stockout."""
from __future__ import annotations
import collections
import json
import os

import httpx

from agents.weekend_safe_agent import strategy

BASE_URL = os.getenv("RESTBENCH_URL", "http://52.48.183.209:8001")
SCENARIOS = ["baseline", "supply_crisis", "tourist_season", "renovation"]
SEEDS = [42, 88, 123]

rows = []

for scenario in SCENARIOS:
    for seed in SEEDS:
        transport = httpx.HTTPTransport(retries=3)
        with httpx.Client(base_url=BASE_URL, timeout=60.0, transport=transport) as client:
            r = client.post("/games", json={
                "team_name": "weekend_safe_d",
                "scenario": scenario,
                "seed": seed,
            })
            r.raise_for_status()
            data = r.json()
            game_id = data["game_id"]
            observation = data["observation"]
            day = data["day"]

            zero_covers = 0
            stockout_count: collections.Counter = collections.Counter()
            final_rep = "Very Good"

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

                dr = td["day_result"]
                if dr["total_covers"] == 0:
                    zero_covers += 1

                final_rep = observation.get("reputation_band", "?")

                notes_raw = observation.get("notes", "") or ""
                try:
                    state = json.loads(notes_raw) if notes_raw.strip().startswith("{") else {}
                    for entry in state.get("sl", []):
                        stockout_count[entry.get("i", "?")] += 1
                except Exception:
                    pass

                if td["status"] != "in_progress":
                    break

            r3 = client.get(f"/games/{game_id}/score")
            r3.raise_for_status()
            score_data = r3.json()

            top = stockout_count.most_common(1)
            top_ing = top[0][0] if top else "none"
            row = {
                "scenario": scenario,
                "seed": seed,
                "score": score_data["score"]["total_score"],
                "zero_covers": zero_covers,
                "final_rep": final_rep,
                "top_stockout": top_ing,
                "status": score_data["status"],
                "rep_pen": score_data["score"]["reputation_penalty"],
                "walk_pen": score_data["score"]["walkout_penalty"],
            }
            rows.append(row)
            print(
                f"  {scenario} seed={seed}: score={row['score']:.0f} "
                f"zeros={zero_covers} rep={final_rep} stockout={top_ing}"
            )

print()
print(f"{'Scenario':<18} {'Seed':>5} {'Score':>9} {'ZeroCvr':>7} {'FinalRep':<12} {'TopStockout':<18} Status")
print("-" * 80)
for r in rows:
    print(
        f"{r['scenario']:<18} {r['seed']:>5} {r['score']:>9.0f} "
        f"{r['zero_covers']:>7} {r['final_rep']:<12} {r['top_stockout']:<18} {r['status']}"
    )

# Save results as JSON for Notion page
with open("logs/weekend_safe_detail.json", "w") as f:
    json.dump(rows, f, indent=2)
print("\nResults saved to logs/weekend_safe_detail.json")
