"""JFAM loglens — traced game runs for offline (Claude Code) analysis.

Plays one game while recording the full (observation, actions, day_result)
trace to traces/<scenario>_<seed>.jsonl, then prints a stockout-focused
diagnostic so we can see exactly which ingredient stranded which day.

  python -m agents.jfam_loglens baseline 42
  python -m agents.jfam_loglens baseline 42 --quiet   # diagnostic only
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from agents.jfam_core import load_dotenv

load_dotenv()

import httpx  # noqa: E402

from agents.jfam_agent import strategy  # noqa: E402

TRACE_DIR = Path(__file__).resolve().parent / "traces"


def run_traced(scenario: str, seed: int, *, quiet: bool = False) -> dict:
    base = os.getenv("RESTBENCH_URL", "http://localhost:8001")
    team = os.getenv("TEAM_NAME", "JFAM_agents")
    TRACE_DIR.mkdir(exist_ok=True)
    trace_path = TRACE_DIR / f"{scenario}_{seed}.jsonl"
    rows: list[dict] = []

    with httpx.Client(base_url=base, timeout=60.0,
                      transport=httpx.HTTPTransport(retries=3)) as c:
        r = c.post("/games", json={"team_name": team, "scenario": scenario,
                                   "seed": seed})
        r.raise_for_status()
        d = r.json()
        gid, obs, day = d["game_id"], d["observation"], d["day"]

        with trace_path.open("w") as fh:
            for _ in range(30):
                acts = strategy(obs, day)
                for tc in acts:
                    c.post(f"/games/{gid}/action", json=tc)
                rr = c.post(f"/games/{gid}/end-turn").json()
                row = {"day": day, "obs": obs, "actions": acts,
                       "result": rr.get("day_result", {})}
                fh.write(json.dumps(row) + "\n")
                rows.append(row)
                obs, day, status = rr["observation"], rr["day"], rr["status"]
                if status != "in_progress":
                    break

        score = c.get(f"/games/{gid}/score").json()

    _diagnose(rows, score, quiet)
    print(f"\nTrace written: {trace_path}")
    return score


def _usable(obs: dict) -> dict[str, float]:
    return {i["ingredient"]: round(i.get("total_kg", 0), 1)
            for i in obs.get("inventory", [])}


def _diagnose(rows: list[dict], score: dict, quiet: bool) -> None:
    print(f"\n{'Day':>3} {'DoW':<4} {'Cov':>5} {'Rev':>8}  "
          f"{'unavailable_at / low-stock ingredients'}")
    print("-" * 78)
    for row in rows:
        obs, res = row["obs"], row["result"]
        day = row["day"]
        dow = obs.get("day_of_week", "")[:3]
        ss = res or {}
        cov = ss.get("total_covers", "?")
        rev = ss.get("total_revenue", 0) or 0
        unavail = ss.get("dishes_unavailable_at", {}) or {}
        stock = _usable(obs)  # stock at START of this day
        low = sorted(k for k, v in stock.items() if v < 1.0)
        flag = ""
        if unavail:
            flag = "OUT=" + ",".join(f"{k}@{v}" for k, v in unavail.items())
        elif cov == 0:
            flag = "ZERO-COVERS  start-stock<1: " + ",".join(low)
        elif low:
            flag = "low: " + ",".join(low)
        mark = "  <==" if (cov == 0 or unavail) else ""
        print(f"{day:>3} {dow:<4} {str(cov):>5} {rev:>8.0f}  {flag}{mark}")

    s = score.get("score", {})
    print(f"\nFinal score: {s.get('total_score')}  "
          f"(profit {s.get('net_profit')}, "
          f"walk {s.get('walkout_penalty')}, rep {s.get('reputation_penalty')}, "
          f"waste {s.get('waste_penalty')})  "
          f"days={score.get('days_survived')} cash={score.get('final_cash')}")


if __name__ == "__main__":
    scen = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    sd = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 42
    run_traced(scen, sd, quiet="--quiet" in sys.argv)
