# JFAM — an autonomous agent that runs a restaurant

> Our entry for the Prosus / AISO AI Agent Hackathon (RestBench, 18 May 2026).
> Team: **`JFAM_agents`**. Built on branch `Jasper`.

This README is self-contained: if you have never seen this project before, read
top to bottom and you will understand **what the game is, what we built, how to
run it, and how the code works**. The full game rule-book lives in
[AGENT_CONTRACT.md](AGENT_CONTRACT.md); strategy theory in
[STRATEGY_GUIDE.md](STRATEGY_GUIDE.md); our day-by-day research log in
[CLAUDE.md](CLAUDE.md) §12.

---

## 1. The game, in one minute

A REST API simulates a 22-table Italian restaurant for **30 days**. Each day
your program receives an **observation** (cash, inventory, suppliers, weather,
reviews, alerts) and replies with **tool calls** (order ingredients, set
prices/menu/staff, run promotions). The simulation then runs that day's service
hour-by-hour and returns the result. After 30 days you get a score:

```
total_score = net_profit − penalties      (low quality, walkouts, waste)
going bankrupt = −100,000   (cash < 0 on any day — game over)
```

You start with **€15,000** and ~€1,260/day of overhead, so a do-nothing agent
goes bankrupt around day 16. The challenge: build an agent that **survives every
game and finishes profitable** — including on **6 hidden scenarios** it has never
seen (final ranking averages 10 scenarios × 3 seeds = 30 games).

Key things the agent can and cannot see:

- **Sees exactly:** cash, P&L, inventory + expiry, pending orders, supplier
  catalog, menu, weather forecast, `alerts` (scenario events announce here).
- **Sees only as a band:** reputation, walkouts, customer trend.
- **Sees delayed:** reviews and the service summary describe 1–4 days ago.
- **Never sees:** true demand, supplier reliability, the scoring coefficients.

The single most useful field is `service_summary.dishes_unavailable_at` — which
dish ran out, at what hour. Most agents fail by ignoring it.

---

## 2. What we built — design in a nutshell

**JFAM is a deterministic rule engine that thinks in "regimes", forecasts demand
day-of-week, and was tuned by an offline LLM analyst loop — not by an LLM making
live decisions.**

That last point is the core design bet, and it is deliberate. We A/B-tested an
LLM making per-day calls (price/staff/marketing nudges): it *lost* — −2% on
baseline, −53% on renovation — because it kept second-guessing an
already-well-tuned policy. The research literature (AIM-Bench, AgentBench,
HeuriGym) says the same: on a deterministic, well-modelled simulation, live LLM
micro-decisions underperform a good policy. So we moved the intelligence
**offline**:

> **The offline-analyst loop (this is the "AI" in our agent):**
> an LLM reads failure traces → proposes a *generalisable rule/parameter
> change* → we replay it deterministically across all scenarios × seeds → keep
> it **only if it beats baseline on held-out seeds it never saw**. Repeat.
> This is how every improvement below was found and proven not-overfit. It is
> NVIDIA-Eureka / DeepMind-FunSearch in spirit: the system improves its own
> operating policy. It uses abundant offline compute, not the scarce in-game
> LLM budget.

The live agent therefore runs **zero LLM tokens** by default. It is fast,
free, fully reproducible, and — because the sim is deterministic — every tuning
experiment is a free, exact measurement.

> **"Can't you run rules *and* an LLM and keep the best?"** We built exactly
> that and tested it ([`jfam_hybrid.py`](agents/jfam_hybrid.py)): the rule core
> is a hard floor; a strong LLM is consulted **only** in regimes the rules were
> never validated on (a confidence-gated deferral — the ReDAct pattern), then
> hard-vetoed back into legal, solvent bounds. It is **provably byte-identical
> to the locked agent on every known scenario** (it cannot regress what we
> already win), and an offline A/B ([`jfam_hybridlab.py`](agents/jfam_hybridlab.py),
> zero server cost) shows the LLM path *still* does not beat the tuned rules
> even on the hidden archetypes. The choice can't be made on the server
> (scoring is latest-per-cell; hidden scenarios get one try), so it has to be
> this single-agent per-turn arbitration — and the honest result reconfirms the
> rules-only bet.

### The three layers

```
observation ─▶ ┌─────────────────────────────────────────────┐ ─▶ tool calls
               │ L1  Deterministic safety + economics rules   │
               │     ordering · pricing · staffing · promos   │
               │ L2  Regime detector (reads alerts + signals) │
               │     normal · capacity_cut · supply_crisis ·  │
               │     demand_surge · inflation · reputation_   │
               │     shock · soft_demand · premium            │
               │ L3  (OFF) optional sparing LLM knob-nudges   │
               └─────────────────────────────────────────────┘
                          ▲ state persisted across days via save_notes
```

- **L1 — rules (always on).** Keeps the restaurant solvent and stocked: a
  forward demand forecast drives front-loaded ordering; cash reserves gate
  spend; pricing/staffing/promotions follow economic logic. Every behavioural
  number is a tunable parameter.
- **L2 — regime detection (always on, zero tokens).** Classifies the situation
  from **observable signals only** (alert keywords, customer trend, supplier
  cost drift, table utilisation). It never reads the scenario *name* — that is
  what lets it generalise to the hidden six. L1's behaviour is conditioned on
  the detected regime.
- **L3 — LLM (off by default).** A bounded advisor that may only nudge a few
  clamped knobs, fires ~5–8×/game, fails safe. Kept as documented, working
  fallback; disabled because it measured net-negative. Enable with
  `JFAM_LLM_OFF=0`.

### The ideas that actually moved the score

Each was found by the offline loop, mechanism-justified, and gated on held-out
seeds (full reasoning in [CLAUDE.md](CLAUDE.md) §12):

| Idea | Why it works |
|---|---|
| **Forward demand forecasting** | Orders front-load ahead of weekend/peak days using a *learned day-of-week cover profile*. Killed the recurring "Sunday 0-cover stockout". Single biggest win: −15.8k → +31.5k on baseline. |
| **Ceiling pricing** | Sim demand is fairly price-**inelastic** and quality penalties have huge headroom, so price at the legal 1.20× ceiling when demand is abundant. (Non-monotonic — endpoints beat the middle.) |
| **Persistent regimes** | Capacity cuts are announced *once* but last ~2 weeks. We parse the duration from the alert text and persist the regime, instead of re-detecting per day. |
| **Yield management** | When customers are walking on full tables (capacity-bound, not price-bound), push price toward the ceiling and suppress demand-stimulating promos. |
| **Endgame order discipline** | Never order inventory that arrives after day 30 — that is pure sunk cash. Worth ~€3.7k/game. |
| **Capacity-gated marketing** | Spend on marketing *only* where there is provable spare table capacity to seat the stimulated demand; otherwise it just burns cash and over-fills the room. |
| **OOD safety nets** | A cash-bleed safe-mode (fires only on the unambiguous solvency precursor) and inflation cost-pass-through, both provably **dormant on the known scenarios** so they add zero regression while hardening the hidden ones. |

**Result (24-game held-out matrix, 0 bankruptcies):** aggregate **≈45,200**.
Held-out seeds and in-sample seeds improve by the same ~24% ⇒ empirically *not*
overfit. Beats every rival with a complete matrix on the dashboard.

---

## 3. Quickstart

Requires **Python 3.9+**. From the repo root:

```bash
# 1. install deps (httpx + litellm; optuna only needed for tuning)
pip install -r requirements.txt

# 2. configure — copy the template and fill it in
cp .env.example .env
#   the only line you must have is the game server URL:
#     RESTBENCH_URL=http://52.48.183.209:8001
#   leave JFAM_LLM_OFF=1 (the shipped, winning configuration)

# 3. play one game (scenario + seed are optional; default baseline/42)
python -m agents.jfam_agent
python -m agents.jfam_agent tourist_season 88

# 4. run the full evaluation matrix (all scenarios × seeds, in parallel)
python -m agents.evaluate agents.jfam_agent \
    --scenarios baseline,supply_crisis,tourist_season,renovation \
    --seeds 42,88,123 --team-name JFAM_agents --parallel 5
# during the eval phase, omit --scenarios so it auto-fetches all 10 from the server
```

`.env` is git-ignored — never commit real keys. `RESTBENCH_URL` **must** be set
(the runner otherwise silently targets `localhost`). Keep the team name constant
at `JFAM_agents`: the leaderboard groups by it.

> **Rate limits:** max 5 concurrent games and 60 games/hour per team. Always
> pass `--parallel 5`. A `429` surfaces in the report as `status=error`,
> `days=0`, score `−100,000` — that is a throttle, *not* a real bankruptcy;
> re-run the cell.

---

## 4. Code map

Everything we wrote is in [`agents/`](agents/) and prefixed `jfam_`. Files
without that prefix (`runner.py`, `evaluate.py`, `do_nothing.py`, …) are the
unmodified hackathon starter kit; we reuse `runner.py` and `evaluate.py` as-is.

| File | Role | Run it? |
|---|---|---|
| [agents/jfam_core.py](agents/jfam_core.py) | **The brain.** L1 rules + L2 regime detector + demand forecaster. All tunable knobs in `DEFAULT_PARAMS`. Zero dependencies, zero tokens, fully deterministic. | no (library) |
| [agents/jfam_agent.py](agents/jfam_agent.py) | **The entrypoint.** Wraps the core, optionally adds L3, persists state, sanitises actions. This is what gets submitted. | `python -m agents.jfam_agent [scenario] [seed]` |
| [agents/jfam_llm.py](agents/jfam_llm.py) | **L3 (off).** Sparing LLM advisor: bounded JSON knob-deltas, fires rarely, metered, fail-safe. | via the agent when `JFAM_LLM_OFF=0` |
| [agents/jfam_tune.py](agents/jfam_tune.py) | **Free tuner.** Optuna search over the rule parameters, exploiting determinism. Anti-overfit gate: a win must hold on held-out seeds or it is rejected. Never regresses the shipped config. | `python -m agents.jfam_tune --trials 25 …` |
| [agents/jfam_loglens.py](agents/jfam_loglens.py) | **Tracer + stockout diagnostic.** Plays one game, writes the full (obs, actions, result) trace to `traces/`, prints a day-by-day "which ingredient stranded which day" table. The input to the offline-analyst loop. | `python -m agents.jfam_loglens [scenario] [seed]` |
| [agents/jfam_oodlab.py](agents/jfam_oodlab.py) | **Offline OOD harness.** Server-free. Mutates a real trace into adverse hidden-scenario shapes; asserts the safety nets are **dormant on healthy** and **active on adverse**. | `python -m agents.jfam_oodlab` |
| [agents/jfam_scenariolab.py](agents/jfam_scenariolab.py) | **All-10-scenario synthetic lab.** Server-free. Closed-loop cash + price-elasticity model over all 10 archetypes (incl. the 6 hidden) to ask "does the locked policy generalise / bleed / go bankrupt". | `python -m agents.jfam_scenariolab [name] [--full]` |
| [agents/jfam_hybrid.py](agents/jfam_hybrid.py) | **Rules+LLM hybrid (experimental).** Rule core as a hard floor; an LLM is consulted *only* in regimes the rules were never validated on (confidence-gated deferral), then hard-vetoed to legal/solvent bounds. Proven byte-identical to the locked agent on all 4 knowns ⇒ zero regression risk. Not the shipped submission — kept as the principled "use both safely" artifact. | `python -m agents.jfam_hybrid [scenario] [seed]` |
| [agents/jfam_hybridlab.py](agents/jfam_hybridlab.py) | **Offline hybrid A/B.** Drives the hybrid through the synthetic lab vs the locked rules, real LLM, zero server quota. Finding: the LLM path does not beat the tuned rules even on hidden archetypes — empirical backing for shipping rules-only. | `python -m agents.jfam_hybridlab` |

### How a game actually runs

A *strategy* is just a function `strategy(observation, day) -> [tool calls]`.
[`runner.py`](agents/runner.py) drives the HTTP loop:

```
POST /games                       create game, get observation
30× ┌ strategy(obs, day) → actions
    │ POST /games/{id}/action      (one call per action)
    └ POST /games/{id}/end-turn    advance a day, get next observation + result
GET /games/{id}/score             final breakdown
```

Inside [`jfam_agent.py`](agents/jfam_agent.py) the strategy is:
`load_state(obs)` → `core_strategy(...)` → *(optional L3)* → drop malformed
actions → append a `save_notes` call carrying the JSON state. **`save_notes` is
the agent's only memory between days** — the EWMA consumption rates, day-of-week
cover profile, supplier reliability, and detected regime all live there.

### Inside `jfam_core.py` (the brain), top to bottom

1. **`load_dotenv`** — zero-dependency `.env` loader.
2. **`DEFAULT_PARAMS`** — every behavioural number, each with a comment
   explaining *why* it is that value. `jfam_params.json` (if present) overrides
   it; none is shipped, so the agent runs on these audited defaults.
3. **State** (`load_state`/`dump_state`) — parse/serialise the JSON memory,
   kept under the 4000-char `save_notes` cap.
4. **Parsing helpers** — recipes, usable (non-expiring) stock, supplier delivery
   cadence, reliability tracking, supplier-cost drift (`cost_ratio`, the
   inflation signal).
5. **`update_consumption`** — EWMA kg/day per ingredient from yesterday's
   dishes sold + a decaying per-ingredient peak, so buffers cover spikes not
   just the mean. Cold-starts from a bootstrap before real data arrives.
6. **`detect_regime`** (L2) — keyword/signal classifier; parses durations like
   "two weeks" out of alerts and persists capacity-cut regimes.
7. **`make_forecaster` / `required_order`** — the forecasting heart: project
   per-day demand from the day-of-week profile and compute the *smallest* order
   that keeps projected stock ≥ 0 through the next delivery gap.
8. **`core_strategy`** (L1) — the policy, in order: update memory → detect
   regime → cash/inflation/cash-bleed checks → staffing → pricing → promotions
   → marketing → inventory ordering (cheapest reliable supplier, urgency-sorted,
   budget-capped, spoilage-capped, endgame-capped).

---

## 5. Developer workflow — the offline-analyst loop

This is how every improvement in this repo was made, and it is reproducible:

```bash
# 1. trace the current agent on a weak cell; read the stockout diagnostic
python -m agents.jfam_loglens renovation 42

# 2. (offline) an LLM/analyst reads traces/renovation_42.jsonl, forms a
#    MECHANISM-justified hypothesis ("renovation is table-supply-bound, so the
#    1.08 price carve-out is misdiagnosing scarce supply as scarce demand")

# 3. change the rule/param in jfam_core.py, then prove it generalises:
python -m agents.evaluate agents.jfam_agent \
    --scenarios baseline,supply_crisis,tourist_season,renovation \
    --seeds 7,55,99,42,88,123 --team-name jfam_dev --parallel 5
#    KEEP only if aggregate up AND held-out seeds {42,88,123} improve like the
#    in-sample {7,55,99} (not overfit) AND zero bankruptcies.

# 4. sanity-check hidden-scenario behaviour without a server:
python -m agents.jfam_oodlab          # safety nets dormant-on-healthy?
python -m agents.jfam_scenariolab     # all 10 archetypes — bleed/bankrupt?

# 5. (optional) free parameter search, same anti-overfit gate built in:
python -m agents.jfam_tune --trials 25 --scenarios baseline,renovation \
    --seeds 42 --val-seeds 88,123 --parallel 3
```

Two rules this loop enforces, learned the hard way (see [CLAUDE.md](CLAUDE.md)
§12):

- **Held-out gate is non-negotiable.** A change that improves training seeds but
  not unseen seeds is overfitting and is rejected automatically by both
  `jfam_tune` and our manual process.
- **Scoring is LATEST-per-cell, not best-per-cell** (verified empirically — the
  docs are wrong). A re-run *overwrites* a cell's score. The agent is
  deterministic so an identical re-run is safe, but **never run a degraded or
  variant agent under `JFAM_agents`** — it would replace a good score.

---

## 6. Configuration reference (`.env`)

| Variable | Purpose | Shipped value |
|---|---|---|
| `RESTBENCH_URL` | Game server. **Required** — runner defaults to localhost otherwise. | `http://52.48.183.209:8001` |
| `TEAM_NAME` | Leaderboard grouping key. Keep constant. | `JFAM_agents` |
| `JFAM_LLM_OFF` | `1` = pure rules (winning config). `0` = enable L3. | `1` |
| `LITELLM_BASE_URL` | OpenAI-compatible proxy for L3 (only if `JFAM_LLM_OFF=0`). | hackathon proxy |
| `LITELLM_API_KEY` | Proxy key (request in Discord `#request-your-api-key`). | — |
| `AGENT_MODEL` | L3 model id, proxy form `openai/<model>`. | `openai/gpt-4.1-mini` |

The agent is fully functional and competitive with **only `RESTBENCH_URL`
set**. Everything LLM-related is optional and off.

---

## 7. Why this is a good autonomous agent

For the judging criteria specifically:

- **Autonomy** — it plans (forward demand forecast), perceives (regime
  detection from raw signals), and acts (8 tools) with zero human input per
  game. No scenario names, no hardcoded scripts.
- **Robust under unseen conditions** — every rule is signal-driven and
  mechanism-justified, gated on held-out seeds, and the OOD nets are *provably
  dormant* on knowns so they cannot regress while still catching hidden shocks.
- **The system improves its own policy** — the offline-analyst loop is a
  genuine self-improvement mechanism (read failures → hypothesise → prove →
  keep), not an if/else tree. That is the real "agentic" story here, and it is
  reproducible from this repo.

---

Good luck, and go feed some customers.
