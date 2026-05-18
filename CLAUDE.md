# CLAUDE.md — Prosus/AISO AI Agent Hackathon (RestBench)

> Working context for this repo. Hackathon day: **18 May 2026**. Branch: `Jasper`.

---

## 1. TL;DR — What we are building

Build an **autonomous AI agent** that runs a simulated 22-table Italian restaurant
for **30 days** via a REST API. Each day the agent gets an **observation** (cash,
inventory, suppliers, weather, reviews, alerts) and submits **tool calls** (order
ingredients, set prices/menu/staff, run promotions). After 30 days it gets a
composite score: `total_score = net_profit − penalties`. **Going bankrupt = −100,000.**

The judges want a **reasoning, planning, acting** agent — not a chat copilot and
not a thinly-wrapped if/else. Winning agents **adapt to unseen scenarios**.

**Two-stage judging:**
1. **Profit → top 5.** Pure leaderboard: aggregate score over 30 eval games
   (10 scenarios × 3 seeds). No subjectivity.
2. **How you built it → top 3.** 5-min pitch + 3-min Q&A scored on: agent
   autonomy, impact, technical quality, creativity.

Prizes: €1,500 / €1,000 / €500 + fast-tracked Prosus recruitment for top 3.

---

## 2. Live server facts (verified 2026-05-18)

- **Server URL:** `http://52.48.183.209:8001` — **LIVE** (`/health` returns ok).
- **Swagger / interactive API:** http://52.48.183.209:8001/docs
- **Scenarios currently exposed:** `baseline`, `renovation`, `supply_crisis`,
  `tourist_season` (the 4 dev scenarios; 6 hidden ones unlock ~16:00).
- **You MUST set the server URL** — `runner.py` defaults to `localhost:8001`:
  ```bash
  export RESTBENCH_URL=http://52.48.183.209:8001
  ```
- **Local env:** Python 3.9.6; deps **not yet installed** → run
  `pip install -r requirements.txt` (httpx>=0.28, litellm>=1.70).

---

## 3. Repository layout

```
Prosus-Hackathon/
├── README.md            # Starter-kit quickstart, API table, tips
├── AGENT_CONTRACT.md    # Authoritative game spec: observation schema, tools, scoring
├── STRATEGY_GUIDE.md    # The "five tensions", hidden dynamics, 4 mastery levels
├── requirements.txt     # httpx, litellm
├── .gitignore           # NOTE: hides internal files (see §8)
└── agents/
    ├── runner.py            # run_game(strategy, ...) — the HTTP game loop. Reuse this.
    ├── do_nothing.py        # Baseline: 0 actions → bankrupt ~day 16, score −100,000
    ├── naive_rule.py        # Baseline: reorder + cut staff → survives, ~−15,000
    ├── starter_template.py  # Rule-based skeleton to copy & extend (Option B)
    ├── llm_template.py      # LiteLLM skeleton to copy & extend (Option A, recommended)
    ├── evaluate.py          # Multi-scenario × multi-seed matrix driver (parallel)
    ├── compare.py           # Runs do_nothing + naive_rule (+ LLM) side by side
    └── __init__.py          # empty
```

### How the code fits together

- **`agents/runner.py`** is the engine. A *strategy* is just a function:
  `strategy(observation: dict, day: int) -> list[dict]` returning tool calls
  like `{"tool": "place_order", "args": {...}}`. `run_game()` handles
  `POST /games` → 30× (`POST .../action`* → `POST .../end-turn`) → `GET .../score`.
- Every agent file defines `strategy(...)` and a `__main__` that calls
  `run_game(strategy, team_name=..., seed=42)`. **Our agent is the same shape.**
- **`agents/evaluate.py`** imports any `agents.<module>` with a `strategy`
  function and runs it across the scenario×seed grid in a `ThreadPoolExecutor`,
  then prints a report ending in `*** FINAL SCORE: <avg> ***`.

### Our workflow

```bash
export RESTBENCH_URL=http://52.48.183.209:8001
pip install -r requirements.txt
cp agents/llm_template.py agents/my_agent.py     # our agent lives here
export OPENAI_API_KEY=sk-...                      # request via Discord #request-your-api-key
export AGENT_MODEL=openai/gpt-4.1-mini            # any litellm model id
python -m agents.my_agent                          # single game (baseline, seed 42)
python -m agents.evaluate agents.my_agent --scenarios baseline,supply_crisis,tourist_season,renovation --seeds 42,88,123 --parallel 5
```

**Pick ONE unique team name and use it in every game** — the leaderboard groups
by team name. Set it via `--team-name` on evaluate, or `team_name=` in `run_game`.

---

## 4. Game mechanics (from AGENT_CONTRACT.md)

**Day order of operations** (everything you do happens *before* service — you
prepare for customers, you cannot react to today's):
1. Your actions applied (orders, menu, prices, staff, promos)
2. Deliveries arrive (supplier reliability may under-deliver)
3. Spoiled inventory removed (waste cost)
4. Service runs hour-by-hour 11:00–22:00 (customers seated FIFO by smallest fitting table)
5. Reputation updates (incl. negative "ghost reviews" from walkouts)
6. End-of-day accounting (revenue − staff − fixed − marketing − waste; cash<0 ⇒ bankrupt)
7. Weather advances

### Key numbers

| Parameter | Value |
|---|---|
| Starting cash | 15,000 EUR |
| Fixed daily cost | 300 EUR |
| Staff cost | 120 EUR/day/person |
| Starting staff | 8 (= 960/day; total overhead 1,260/day at 8) |
| Staff range | 3–15 |
| Tables | 22 (4×2-seat, 8×4-seat, 6×6-seat, 4×8-seat) |
| Sim length | 30 days, Day 1 = Monday |
| Service hours | 11:00–22:00 |
| Tick budget | 30,000 ms to submit tool calls |

### Tools (`POST /games/{id}/action`, body `{"tool","args"}`)

| Tool | Args | Notes |
|---|---|---|
| `place_order` | supplier, ingredient, quantity_kg | Lead time 1–2d, then only on supplier's delivery days. Cost at end of turn. Must meet `min_order_kg` & cash. |
| `set_menu` | dishes (list) | Min 5 dishes. New dishes have a kitchen learning curve. |
| `set_price` | dish, price | 0.8×–1.2× base price only. |
| `set_staff_level` | level (int) | 3–15. |
| `set_marketing_spend` | amount | 0–500 EUR/day. Diminishing returns. |
| `run_happy_hour` | *(none)* | 15:00–18:00 boost+discount. Decays on consecutive use. |
| `offer_daily_special` | dish | Must be on active menu. "Does more than you'd think." |
| `save_notes` | text | ≤4000 chars, persists to next observation. **This is the agent's only memory.** |

Names are **case-sensitive**; use exact strings from the observation. Invalid
actions return `{"status":"rejected","reason":"..."}`.

### Observation visibility

- **Exact:** day, cash, P&L, `inventory` (batch-level + expiry), `pending_orders`,
  `delivery_history`, `supplier_catalog`, `menu_book`, `active_menu`, `staff_level`,
  `weather_today`, `weather_forecast` (accuracy 85/70/55%), `alerts`, `notes`.
- **Banded (approximate):** `reputation_band` (Poor→Excellent),
  `walkout_band` (None/Few/Some/Many), `customer_trend` (Declining/Stable/Growing).
- **Delayed:** `recent_reviews` and `service_summary` describe visits 1–4 days ago.
- **#1 signal:** `service_summary.dishes_unavailable_at` — exactly which dish ran
  out and at what hour. Plus `pending_orders` — check before re-ordering.
- **Hidden:** true demand (censored by capacity/inventory), per-customer
  satisfaction, supplier reliability state, cohort sizes, scoring coefficients.

### Scoring priorities (rough order of impact)

1. **Don't go bankrupt** — instant −100,000, nothing else matters.
2. **Keep quality metrics above thresholds** — penalties are *quadratic* below
   threshold (a small dip costs far more than expected).
3. **Minimize walkouts** — direct + compounding negative-review costs.
4. **Control waste** — moderate ok, excessive penalized.
5. **Maximize net profit** — what's left after penalties.

"The simulation cares how you *finish*, not just how you average" — don't tank
quality in the final days; final reputation > average reputation.

---

## 5. Strategy notes (from STRATEGY_GUIDE.md + cheat sheet)

**The five tensions:** profit↔quality, short↔long term, specialization↔resilience,
cost↔coverage, exploration↔exploitation.

**Hidden dynamics that bite:**
- Supplier reliability is non-constant; concentrating volume on one supplier
  backfires during outages. Watch `delivery_history` (ordered vs delivered).
- Reputation = momentum-weighted moving average; bad weighs more than good;
  recovery is slow. Losing **regulars** triggers a death spiral.
- Price elasticity is non-linear & asymmetric across 0.8×–1.2×.
- Promotions decay with consecutive use; abrupt stop after sustained happy
  hours causes a demand dip.
- Overstocking accelerates spoilage of oldest stock (not just tied-up cash).
- Weather & weekday patterns drive footfall — track your own data.

**Patterns that win (none required):**
- Read `dishes_unavailable_at` and `pending_orders` **every turn** — most
  failures come from missing these two.
- Use `save_notes` as cross-turn memory (LLM has none otherwise).
- React to `alerts` — scenario events announce themselves there (free intel).
- **Hybrid:** cheap deterministic rules for safety (never bankrupt, reorder at
  threshold) + LLM for judgment (pricing, promos, recovery). This both scores
  well AND pitches well on "agent autonomy" + "technical quality".
- Test across all 4 known scenarios × seeds 42/88/123 — don't overfit baseline/seed 42.

**Four mastery levels:** Survive → Optimize → Adapt → Anticipate.

---

## 6. ⚠️ Important discrepancies & gotchas (resolve in our favor)

1. **Concurrency limit conflict.** README says "max **10** concurrent games";
   `evaluate.py` defaults to `MAX_PARALLEL = 10`; but its own docstring says
   "default: 5, matches server limit" and the **cheat sheet (authoritative team
   guidance) says "Max 5 concurrent games per team."**
   → **Always pass `--parallel 5`** during the eval phase to avoid `429`s.
   Also: **60 games/hour** cap per team (README) — 30-game matrix fits, but
   retries eat into it. Plan runs.
2. **Hidden scenario names leaked.** `evaluate.py` `FALLBACK_SCENARIOS` lists
   `inflation` and `health_scare` *in addition to* the 4 known scenarios. These
   are very likely 2 of the 6 hidden eval scenarios → design the agent to handle
   **cost inflation** and a **demand/reputation shock ("health scare")** even
   though it can't be tested against them now. Detect via `alerts` + observable
   drift in `supplier_catalog` prices / `customer_trend` / reviews.
3. **Score landmarks (cheat sheet is authoritative over README):**
   `do_nothing` ≈ −100,000 · `naive_rule` ≈ −15,000 · 0 = beat naïve ·
   **+15,000 to +35,000 = competitive.** (README's "−15k to −19k baselines" is looser.)
4. **Eval phase resets everything (~16:00):** dev-phase leaderboard wiped; only
   eval-phase games count; 6 hidden scenarios unlock (→ 10 total); **eval seeds
   differ from dev seeds 42/88/123**. Best score per (scenario,seed) cell counts
   — retries allowed. **Partial matrices rank below complete ones** — finishing
   all 30 beats almost-finishing with one great score. **Lock the agent before
   16:00; use eval time to run, not to tweak prompts.**
5. **Submission = 2 steps:** (a) valid leaderboard entry under our team name,
   (b) fill the submission form. **Repo must be public on GitHub, last commit
   before 17:00.** Include a short "next steps / what we'd build next" in the form.
6. `runner.py` default URL is `localhost:8001` — forgetting `RESTBENCH_URL`
   silently targets nothing useful. Export it everywhere.
7. Python 3.9 locally; `litellm`/`httpx` install needed. If a model id fails,
   it's a litellm provider/key issue, not the game.

---

## 7. Game-day playbook

**During the day (dev phase, ~9:00–16:00):**
- Lock a unique team name. Iterate `agents/my_agent.py` against the 4 known
  scenarios × seeds 42,88,123 until robust (no bankruptcies, consistent positive).
- Watch the live dashboard (auto-refresh ~15s).
- Prioritize *consistency over peak*: steady +5k everywhere beats +40k on
  baseline + a bankruptcy on a hidden scenario.

**When eval opens (~16:00):**
- Stop tuning. Run the full matrix:
  ```bash
  python -m agents.evaluate agents.my_agent --seeds <eval_seeds> --parallel 5
  ```
  (`--seeds` provided by organisers; if scenarios auto-fetched from server, omit
  `--scenarios` so it pulls all 10.)
- Run all **30** games before 17:00. Retry weak/failed cells (best per cell counts),
  but only if time and the 60/hr cap allow.

**If top 5 (announced 18:00, pitch 18:30):**
- 5 min pitch + 3 min Q&A. Have ready: one architecture diagram; the one thing
  the agent does that we're proud of; how it handled an unexpected hard scenario;
  agent runnable on a laptop for a quick live demo; code on screen.

---

## 8. Note on `.gitignore`

`.gitignore` hides internal/organizer files not part of the starter kit:
`VOLUNTEER_TIPS.md`, `research.py`, `stress_test.py`,
`test_hackathon_readiness.py`, `test_isolation.py`, `agents/smart_agent.py`,
`*.jsonl`, `restbench.db`, `restbench_data/`. These are not present and not
meant for participants — don't rely on them. (Our own agent should NOT be named
`smart_agent.py` or it'll be git-ignored — use `my_agent.py` or similar.)

---

## 9. Logistics & contacts (Hackathon Cheat Sheet — 18 May 2026)

- **WiFi:** network `AI-House Guest`, **no password**.
- **Building security:** `06 50822027`. Keep ID card + visitor pass on you.
- **API keys (free GPT-4/GPT-5 + VM):** request in Discord `#request-your-api-key`.
- **Photos:** upload to the shared public folder (link in cheat sheet).
- **Teams:** 3–5 people.

### Schedule

| Time | Item |
|---|---|
| 7:30–8:00 | Check-in & coffee |
| 8:00–8:45 | Opening ceremony (AISO & Prosus + challenge intro) |
| 8:45–9:00 | Team formation |
| 9:00–17:00 | **Hacking** |
| 12:00–13:00 | Lunch |
| ~16:00 | **Eval phase opens** (leaderboard reset, 6 hidden scenarios unlock) |
| **17:00** | **Submission deadline — tools down, last commit before this** |
| 17:00–18:00 | Recruiter booth + dinner + judging |
| 18:00–18:15 | Top 5 reveal |
| 18:30–19:15 | Final presentations |
| 19:15–19:45 | Winner selection & announcement |
| 19:45–21:15 | Networking drinks |

### Stage-1 "good solution" rules (verbatim intent)

- **Survive every game.** Bankruptcy = −100,000 and tanks the aggregate.
- **Be consistent.** Final rank = average across all 30 cells.
- **Adapt, don't memorise.** 6/10 final scenarios are hidden.
- **Complete the matrix.** Partial submissions rank below complete ones.

### Stage-2 pitch criteria

| Criterion | Judges look for |
|---|---|
| Agent autonomy | Thinks/plans/acts with minimal human input — not if/else |
| Impact | Real-world usefulness; relevance to autonomous-agents theme |
| Technical quality | Clean architecture, robust under unseen scenarios |
| Creativity | Novel approach / unexpected use of tools & APIs |

### People

- **AISO students:** Vitor Castro (VU), Jan Pecka (UvA), Filip Szturo (UvA),
  Amina Akhmedova (VU).
- **Prosus team:** Tatjana Obenaus (Head of Employer Brand), Andreea Tache
  (Marketing & Comms), Daniella Linera Roose (Talent Acq. Coord.), Jannica
  Heibel (Marketing & Comms), Gabriel Inyang (Global Talent Partner), Olga
  Sokolva (Talent Acq. Partner).
- **Mentors (Prosus):** Asad Ismail (Sr. AI Engineer), Yingying Deng (AI Talent
  Lab Resident), Isha Agrawal (AI Engineer), Gowtham Venkatesan, Nikolas
  Stavrou, Marin Marian, Saunaq Chakrabarty (AI Talent Lab Residents).

---

## 10. API quick reference

| Method | Endpoint | Description |
|---|---|---|
| POST | `/games` | Create game. Body `{team_name, scenario?, seed?}` |
| GET | `/games/{id}/observe` | Current observation |
| POST | `/games/{id}/action` | One tool call `{tool, args}` |
| POST | `/games/{id}/end-turn` | Advance one day |
| GET | `/games/{id}/score` | Final score breakdown |
| GET | `/games/{id}/status` | `{game_id, day, cash, status}` |
| GET | `/games/{id}/notes` | Read saved notes |
| GET | `/leaderboard` | Ranked scores (`?scenario=` filter) |
| GET | `/scenarios` | Available scenarios |
| GET | `/games` | List games (`?team_name=` filter) |
| DELETE | `/games/{id}` | Abandon a game |
| GET | `/health` | Health check |

Rate limits: per team ≤10 concurrent (README) / **≤5 recommended (cheat sheet)**,
≤60 games/hour. Exceeding → `429` (the `evaluate` harness surfaces this as
`status="error"`, `days=0`, score −100,000 — NOT a real bankruptcy/code bug;
re-run the cell sequentially to confirm).

---

## 11. Our agent — JFAM (built on branch `Jasper`)

**Architecture: 3-layer Balanced Hybrid.** L1 deterministic safety rules + L2
regime detector (zero-token, always on); L3 sparing LLM judgment (bounded
knob-nudges only, ~5–8 calls/game, OFF by default). Determinism is exploited to
tune the rule core for free.

**Files (`agents/`):**
- `jfam_core.py` — L1 rules + L2 regime + **forward demand forecasting**
  (the key idea: orders front-load ahead of weekend/peak days using the learned
  day-of-week cover profile). All knobs in `DEFAULT_PARAMS`; `jfam_params.json`
  (if present) overrides — committed when tuned.
- `jfam_agent.py` — submission entrypoint. `make_strategy(params, use_llm)`
  factory + module-level `strategy`. `python -m agents.jfam_agent [scenario] [seed]`.
- `jfam_llm.py` — L3: OpenAI-SDK client → LiteLLM proxy, bounded JSON deltas
  (price_mult / marketing / staff_bias / happy_hour), token meter →
  `traces/llm_usage.jsonl`. Fail-safe (errors return core actions).
- `jfam_tune.py` — Optuna search over PARAMS, pure-rules (zero-token), writes
  `jfam_params.json`. Rate-limit aware (`trials × scenarios × seeds < 60/hr`).
- `jfam_loglens.py` — traced run + stockout diagnostic → `traces/*.jsonl` for
  offline Claude-Code analysis (no API key needed).

**Config:** `.env` (git-ignored) / `.env.example`. Keys: `RESTBENCH_URL`,
`LITELLM_BASE_URL`, `LITELLM_API_KEY`, `AGENT_MODEL`, `JFAM_LLM_OFF` (default
`1` = pure-rules), `TEAM_NAME=JFAM_agents`. **Proxy budget is opaque** (virtual
key scoped to LLM routes only) — self-meter via `traces/llm_usage.jsonl`.

**Pure-rules results (untuned, zero LLM, all 30-day completions, 0 bankruptcies):**
baseline 42/88/123 → +31.5k / +33.8k / +23.5k (avg ~29.6k); supply_crisis 88 →
+36.1k; renovation 42 → +8.3k (weak spot — tune target); tourist_season 42 →
**+46.6k**. Already at/above the dev-phase leader **AKT** (avg ~28.4k; their
renovation was −281). Beats naive_rule (−15.8k) and starter (−19.5k).

**Confirmed eval intel (from `/leaderboard/dashboard`):** 10 eval scenarios =
4 known + hidden `black_swan, feast_or_famine, health_scare, inflation,
premium_pivot, silent_drift`; **eval seeds = 7, 55, 99**. L2 detectors target
these regimes via observable signals only (no scenario-name sniffing).

**Run cheatsheet:**
```bash
# single game
python -m agents.jfam_agent tourist_season 42
# diagnose stockouts
python -m agents.jfam_loglens renovation 42
# tune the free rules core (mind the 60/hr cap)
python -m agents.jfam_tune --trials 20 --scenarios baseline,renovation --seeds 42 --parallel 3
# matrix (eval phase): omit --scenarios to fetch all 10
RESTBENCH_URL=… python -m agents.evaluate agents.jfam_agent --seeds 7,55,99 --team-name JFAM_agents --parallel 5
```

**Plan file:** `/Users/jasp/.claude/plans/i-want-you-to-hashed-meadow.md`.
