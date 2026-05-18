# Project Context — Prosus Hackathon Restaurant Agent

## What this project is
30-day Italian restaurant simulation scored on `net_profit - penalties`.
Bankruptcy (cash < 0) = -100,000. Survival is the primary constraint.
Server: `http://52.48.183.209:8001`. Set `RESTBENCH_URL` before running.

## Current best agent
`agents/weekend_safe_agent.py` + `agents/llm_manager.py` + `agents/regime_detector.py`

Architecture: deterministic supply-chain spine (ordering, staffing, regime detection)
with a narrow LLM revenue layer (menu, pricing, happy hour, daily special, marketing).
The LLM never touches inventory or staffing. v4 avg score: **+8,579** across 4 scenarios × 3 seeds.

Run evaluation:
```
$env:PYTHONIOENCODING="utf-8"
$env:RESTBENCH_URL="http://52.48.183.209:8001"
$env:OPENAI_API_KEY="..."
$env:OPENAI_API_BASE="http://litellm-production.eba-pvykax23.eu-west-1.elasticbeanstalk.com/"
$env:AGENT_MODEL="gpt-4.1-mini"
python -m agents.evaluate agents.weekend_safe_agent --scenarios baseline,supply_crisis,tourist_season,renovation --seeds 42,88,123 --parallel 3 --quiet
```

---

## Hard-won lessons — what we got wrong and why

These are rationalizations that sounded right and cost us time or score.
Read before proposing changes.

| Rationalization | Reality |
|---|---|
| "More staff means fewer walkouts, that'll fix it" | Walkouts on zero-cover days are noise — customers never arrived. Staff costs went from 600 to 1,200 EUR/day and we bankrupted faster. Read the signal before acting on it. |
| "The walkout band is the obvious thing to optimize" | Walkouts are downstream. Stockouts → walkouts → ghost reviews → reputation collapse → demand collapse. Fix the cause, not the symptom. |
| "Use the cheapest supplier — same ingredient, save money" | Italian Imports Co. was 30% cheaper on pasta. Wednesday-only delivery. Order Wednesday morning, get pasta *next* Wednesday. Effective lead time, not price, is the metric. |
| "Reputation collapsed because we made customers unhappy" | Reputation collapsed because no customers were served at all on weekends. We were modelling sentiment when the problem was arithmetic. |
| "Just throw an LLM at the whole loop — it's an agent hackathon" | Day 13 bankruptcy. The LLM ordered from "FreshFishCo" and "Supplier A" — names it invented. LLMs hallucinate structured data. Constrain the boundary, don't constrain the prompt. |
| "Better prompt engineering will stop the hallucinations" | Whack-a-mole. Fix supplier names, it invents ingredient names. Fix those, it picks quantities below min_order. The fix is validation at the API boundary, not better prose. |
| "The simulation is mostly random — variance is unavoidable" | Most "variance" was the same root cause hitting on different days. Once we found the cheapest-supplier trap, three scenarios converged. Variance often means you haven't isolated the mechanism yet. |
| "If naive_rule survives, our agent should beat it easily" | naive_rule scored -15,814. Our first "improved" agent scored -100,000. Survival is a high bar, not a low one. |
| "Bigger safety buffer fixes the stockout" | Bigger buffer + Italian Imports Wed-only + 0.85 reliability = still stockout on Saturday. Buffers only help if the calendar math is right. |
| "We're scoring +8k, just keep tuning the same module" | The biggest jumps came from changing *what* the agent decides, not how well. Adding the LLM revenue manager added +5.9k. Tuning the spine after that gave +200. Know when to stop polishing and start adding. |
| "The LLM is making us lose on baseline, just remove it" | Removing it loses tourist_season +9k and supply_crisis +12k. The LLM has a role, not a yes/no value. Constrain *when* it runs, don't disable it. |
| "We'll fix the renovation scenario before the deadline" | One cell on a 30-cell matrix. Spending an hour for marginal improvement on -10k may regress two other cells. Pick battles by aggregate impact, not by which loss is largest. |

---

## Key mechanics (non-obvious, verified by log analysis)

- **Effective lead time** = `lead_time_days + days_until_next_valid_delivery_day`. Italian Imports (Wed-only, 3-day lead) has a 7-day effective lead when ordered Wednesday.
- **No supplier delivers Sunday.** Canal Dairy only on Saturday (dairy only). Order on Thursday for weekend cover.
- **Zero-cover days** in early runs were 100% caused by inventory stockout, not reputation. Reputation is downstream.
- **`service_summary` can be None** on day 1. Always use `obs.get("service_summary") or {}`.
- **Notes field is 4000 chars hard limit.** Truncation silently corrupts JSON mid-key. Keep serialized state under 3800 chars with a pre-write trim loop.
- **LLM call conditions** (see `strategy()` in `weekend_safe_agent.py`): regime change, rep change, zero covers, many walkouts, Thu/Fri, new alert, or 3+ days since last call. Skip in steady-state renovation and when cash < 2000.
- **Rate cap is 28 calls/game** — games were hitting 20 at day 20-23, losing LLM judgment in the final week.

---

## Scenario notes

| Scenario | v4 avg | Notes |
|---|---|---|
| baseline | +4,691 | High LLM variance on seed 42 (swings ±6k run-to-run) |
| supply_crisis | +11,228 | LLM pricing during scarcity is the main lever |
| tourist_season | +18,867 | LLM surge pricing + daily specials drive the gain |
| renovation | -470 | Steady-state renovation skips LLM by design; recovery detection helps |
