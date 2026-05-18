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
    ap.add_argument("--seeds", default="42",
                    help="TRAIN seeds the search optimises on")
    ap.add_argument("--val-seeds", default="",
                    help="HELD-OUT seeds; a tune is adopted only if it also "
                         "beats baseline here (anti-overfit). Empty = skip.")
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

    # Keys the search space samples — used to seed the known-good baseline.
    search_keys = ["price_mult", "staff_base", "staff_weekend_bonus",
                   "safety_days", "coverage_buffer_days", "max_hold_days",
                   "forecast_safety", "reserve_days", "cadence_cost",
                   "marketing_amount", "reliability_floor"]

    def objective(trial):
        params = _suggest(trial)
        return _score_params(params, scenarios, seeds, args.url,
                             args.team_name, args.parallel)

    study = optuna.create_study(direction="maximize", study_name=args.study)
    # Trial 0 = current defaults (or existing jfam_params.json) so the search
    # can never end up worse than the known-good baseline.
    seed_params = dict(DEFAULT_PARAMS)
    seed_params.update(base_best)
    study.enqueue_trial({k: seed_params[k] for k in search_keys})
    study.optimize(objective, n_trials=args.trials + 1, n_jobs=1)

    baseline_val = study.trials[0].value  # the seeded defaults' score
    print(f"\nBaseline (current params) mean: {baseline_val:,.0f}")
    print(f"Best found mean:               {study.best_value:,.0f}")

    if study.best_value <= baseline_val + 1.0:
        print("No TRAIN improvement over current params — jfam_params.json "
              "left unchanged (defaults retained).")
        return

    best = dict(DEFAULT_PARAMS)
    best.update(base_best)
    best.update(study.best_params)

    # Anti-overfit gate: a TRAIN win must also hold on HELD-OUT seeds the
    # search never saw, else we reject it (real eval is unseen seeds).
    val_seeds = [int(s) for s in args.val_seeds.split(",") if s.strip()]
    if val_seeds:
        base_v = _score_params(seed_params, scenarios, val_seeds, args.url,
                               args.team_name, args.parallel)
        cand_v = _score_params(best, scenarios, val_seeds, args.url,
                               args.team_name, args.parallel)
        print(f"\nHeld-out {val_seeds}: baseline {base_v:,.0f} -> "
              f"candidate {cand_v:,.0f}")
        if cand_v <= base_v + 1.0:
            print("REJECTED: TRAIN gain did NOT generalise to held-out "
                  "seeds (overfit). jfam_params.json left unchanged.")
            return
        print("Held-out confirms generalisation.")

    best_path.write_text(json.dumps(best, indent=2))
    print(f"ADOPTED: TRAIN +{study.best_value - baseline_val:,.0f} "
          f"-> wrote {best_path}")
    print(json.dumps(study.best_params, indent=2))


if __name__ == "__main__":
    main()
