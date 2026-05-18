"""JFAM tuner — exploit the sim's determinism to tune the rule core for FREE.

Pure-rules runs cost zero LLM tokens; only the game server's rate limit
(~5 concurrent, 60 games/hour) applies. Optuna searches jfam_core.PARAMS and
writes the best set to agents/jfam_params.json (which jfam_core auto-loads).

  python -m agents.jfam_tune --trials 25 --scenarios baseline,renovation \
      --seeds 42 --parallel 3

Rate-limit math: games_per_trial = len(scenarios)*len(seeds). Keep
trials*games_per_trial well under 60/hour. Start narrow (the weak scenario,
one seed), widen once promising.
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from agents.jfam_core import DEFAULT_PARAMS, load_dotenv

load_dotenv()

from agents.jfam_agent import make_strategy  # noqa: E402
from agents.runner import run_game  # noqa: E402

PARAMS_FILE = Path(__file__).resolve().parent / "jfam_params.json"


def _suggest(trial) -> dict:
    """Search space — the knobs that move the score most."""
    p = dict(DEFAULT_PARAMS)
    p["price_mult"] = trial.suggest_float("price_mult", 0.95, 1.20)
    p["staff_base"] = trial.suggest_int("staff_base", 5, 11)
    p["staff_weekend_bonus"] = trial.suggest_int("staff_weekend_bonus", 0, 4)
    p["safety_days"] = trial.suggest_float("safety_days", 1.0, 5.0)
    p["coverage_buffer_days"] = trial.suggest_float(
        "coverage_buffer_days", 1.0, 5.0)
    p["max_hold_days"] = trial.suggest_float("max_hold_days", 6.0, 16.0)
    p["forecast_safety"] = trial.suggest_float("forecast_safety", 1.0, 1.5)
    p["reserve_days"] = trial.suggest_float("reserve_days", 1.5, 5.0)
    p["cadence_cost"] = trial.suggest_float("cadence_cost", 0.0, 1.5)
    p["marketing_amount"] = trial.suggest_float("marketing_amount", 0.0, 350.0)
    p["reliability_floor"] = trial.suggest_float(
        "reliability_floor", 0.55, 0.9)
    return p


def _score_params(params, scenarios, seeds, base_url, team, parallel) -> float:
    strat = make_strategy(params=params, use_llm=False)
    jobs = [(sc, sd) for sc in scenarios for sd in seeds]
    scores: list[float] = []

    def _one(sc, sd):
        try:
            r = run_game(strat, base_url=base_url, team_name=team,
                         scenario=sc, seed=sd, verbose=False)
            return r["score"]["total_score"]
        except Exception as e:
            print(f"  game error {sc}/{sd}: {e}")
            return -100_000.0

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = {pool.submit(_one, sc, sd): (sc, sd) for sc, sd in jobs}
        for f in as_completed(futs):
            scores.append(f.result())
    # Mean, but a single bankruptcy should dominate (it already scores -100k).
    return sum(scores) / len(scores)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--scenarios", default="baseline,renovation")
    ap.add_argument("--seeds", default="42")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--url", default=os.getenv("RESTBENCH_URL",
                                               "http://localhost:8001"))
    ap.add_argument("--team-name", default=os.getenv("TEAM_NAME",
                                                     "JFAM_agents"))
    ap.add_argument("--study", default="jfam")
    args = ap.parse_args()

    import optuna

    scenarios = args.scenarios.split(",")
    seeds = [int(s) for s in args.seeds.split(",")]
    gpt = len(scenarios) * len(seeds)
    print(f"Tuning: {args.trials} trials x {gpt} games = "
          f"{args.trials * gpt} games. Keep <60/hour.")

    best_path = PARAMS_FILE
    base_best = json.loads(best_path.read_text()) if best_path.exists() else {}

    def objective(trial):
        params = _suggest(trial)
        return _score_params(params, scenarios, seeds, args.url,
                             args.team_name, args.parallel)

    study = optuna.create_study(direction="maximize", study_name=args.study)
    study.optimize(objective, n_trials=args.trials, n_jobs=1)

    best = dict(DEFAULT_PARAMS)
    best.update(base_best)
    best.update(study.best_params)
    best_path.write_text(json.dumps(best, indent=2))
    print(f"\nBest mean score: {study.best_value:,.0f}")
    print(f"Best params -> {best_path}")
    print(json.dumps(study.best_params, indent=2))


if __name__ == "__main__":
    main()
