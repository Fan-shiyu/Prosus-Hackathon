"""FastAPI server for RestBench simulation benchmark.

Teams interact via HTTP: create a game, observe state, submit tool calls
(multiple per turn), then end-turn to advance the simulation.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

import restbench.config as config
from restbench.api.persistence import LeaderboardDB, save_transcript
from restbench.core.observation import make_observation
from restbench.core.rng import SimRNG
from restbench.core.types import (
    AgentAction,
    AgentObservation,
    CreateGameRequest,
    GameScoreResponse,
    GameStatusResponse,
    PurchaseOrderRequest,
    ScoreBreakdown,
    ToolCall,
    WorldState,
)
from restbench.engine.guardrails import endgame_weighted_reputation
from restbench.engine.tuning import TuningConfig
from restbench.engine.world import create_world
from restbench.harness.orchestrator import run_day
from restbench.scenarios.handlers import process_events
from restbench.scenarios.loader import apply_tuning_overrides, list_scenarios, load_scenario, resolve_events
from restbench.scenarios.types import EventSchedule

logging.basicConfig(level=getattr(logging, config.LOG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)


_db: LeaderboardDB | None = None
_total_games_created: int = 0
_games_expired: int = 0
_hidden_unlocked: bool = config.HIDDEN_UNLOCKED


async def _expire_idle_games():
    """Background task: remove games idle longer than GAME_EXPIRY_SECONDS."""
    global _games_expired
    while True:
        await asyncio.sleep(300)
        now = time.time()
        expired_ids = [
            gid for gid, g in games.items()
            if now - g.last_activity > config.GAME_EXPIRY_SECONDS
        ]
        for gid in expired_ids:
            session = games.pop(gid, None)
            if session and config.PERSIST:
                try:
                    session.transcript.append({"event": "expired", "reason": "idle_timeout"})
                    save_transcript(session.transcript, session.game_id, config.DATA_DIR)
                except Exception:
                    logger.exception("Failed to save transcript for expired game %s", gid)
            _games_expired += 1
        if expired_ids:
            logger.info("Expired %d idle game(s): %s", len(expired_ids), expired_ids)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db
    if config.PERSIST:
        db_path = config.DATA_DIR / "leaderboard.db"
        _db = LeaderboardDB(db_path)
        rows = _db.all_entries()
        for r in rows:
            leaderboard.append(LeaderboardEntry(
                team_name=r["team_name"],
                scenario=r["scenario"],
                seed=r["seed"],
                score=r["score"],
                days_survived=r["days_survived"],
                timestamp=r["timestamp"],
            ))
        logger.info("Loaded %d leaderboard entries from %s", len(rows), db_path)

    expiry_task = asyncio.create_task(_expire_idle_games())
    yield
    expiry_task.cancel()
    if _db is not None:
        _db.close()


app = FastAPI(
    title="RestBench",
    version="0.1.0",
    description="Restaurant simulation benchmark for AI agent hackathon",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_start_time = time.monotonic()

_TEAM_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,29}$")


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - start
    logger.info(
        "%s %s %d %.3fs",
        request.method, request.url.path, response.status_code, elapsed,
    )
    return response


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class GameSession:
    game_id: str
    team_name: str
    world: WorldState
    rng: SimRNG
    tuning: TuningConfig
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    status: str = "in_progress"  # in_progress | completed | bankrupt
    cumulative_walkouts: int = 0
    cumulative_satisfaction: list[float] = field(default_factory=list)
    cumulative_waste_cost: float = 0.0
    cumulative_ingredient_cost: float = 0.0
    event_schedule: EventSchedule = field(default_factory=dict)
    default_tuning: TuningConfig = field(default_factory=TuningConfig)
    reputation_snapshots: dict[int, float] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)


games: dict[str, GameSession] = {}


@dataclass
class LeaderboardEntry:
    team_name: str
    scenario: str
    seed: int
    score: float
    days_survived: int
    timestamp: float


leaderboard: list[LeaderboardEntry] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_game(game_id: str) -> GameSession:
    game = games.get(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")
    return game


def _check_in_progress(game: GameSession) -> None:
    if game.status != "in_progress":
        raise HTTPException(status_code=400, detail=f"Game is already {game.status}")


def _validate_team_name(name: str) -> None:
    if not _TEAM_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="team_name must be 2-30 characters: letters, digits, hyphens, underscores "
                   "(must start with letter or digit)",
        )


def _check_rate_limit(team_name: str) -> None:
    active = sum(
        1 for g in games.values()
        if g.team_name == team_name and g.status == "in_progress"
    )
    if active >= config.MAX_CONCURRENT_GAMES:
        raise HTTPException(
            status_code=429,
            detail=f"Too many active games ({active}/{config.MAX_CONCURRENT_GAMES}). "
                   "Finish or delete an existing game first.",
        )
    now = time.time()
    recent = sum(
        1 for g in games.values()
        if g.team_name == team_name and now - g.created_at < 3600
    )
    if recent >= config.MAX_GAMES_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail=f"Too many games created in the last hour ({recent}/{config.MAX_GAMES_PER_HOUR}).",
        )


# ---------------------------------------------------------------------------
# Admin auth
# ---------------------------------------------------------------------------


async def _require_admin(authorization: str | None = Header(None)):
    if not config.ADMIN_TOKEN:
        raise HTTPException(403, "Admin endpoints disabled (RESTBENCH_ADMIN_TOKEN not set)")
    if authorization != f"Bearer {config.ADMIN_TOKEN}":
        raise HTTPException(401, "Invalid or missing admin token")


# ---------------------------------------------------------------------------
# Team endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    uptime = int(time.monotonic() - _start_time)
    return {
        "status": "ok",
        "active_games": sum(1 for g in games.values() if g.status == "in_progress"),
        "uptime_seconds": uptime,
    }


@app.post("/games")
async def create_game(req: CreateGameRequest):
    global _total_games_created

    _validate_team_name(req.team_name)
    _check_rate_limit(req.team_name)

    game_id = str(uuid.uuid4())

    try:
        scenario_def = load_scenario(req.scenario)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if scenario_def.hidden and not _hidden_unlocked:
        raise HTTPException(
            status_code=403,
            detail=f"Scenario '{req.scenario}' is not available during the development phase.",
        )

    tuning = TuningConfig()
    tuning = apply_tuning_overrides(tuning, scenario_def.tuning_overrides)
    default_tuning = tuning

    rng = SimRNG(master_seed=req.seed)
    event_schedule = resolve_events(scenario_def.events, rng.events)

    world = create_world(seed=req.seed, scenario=req.scenario, tuning=tuning)

    tuning = process_events(world, tuning, event_schedule, rng, default_tuning=default_tuning)

    observation = make_observation(world, tuning)

    session = GameSession(
        game_id=game_id,
        team_name=req.team_name,
        world=world,
        rng=rng,
        tuning=tuning,
        event_schedule=event_schedule,
        default_tuning=default_tuning,
    )
    games[game_id] = session
    _total_games_created += 1

    session.transcript.append({
        "event": "game_created",
        "day": world.day,
        "team_name": req.team_name,
        "scenario": req.scenario,
        "seed": req.seed,
    })

    logger.info("Game %s created: team=%s scenario=%s seed=%d", game_id[:8], req.team_name, req.scenario, req.seed)

    return {
        "game_id": game_id,
        "observation": observation.model_dump(),
        "day": world.day,
        "status": "in_progress",
    }


@app.get("/games/{game_id}/observe")
async def observe(game_id: str):
    game = _get_game(game_id)
    async with game.lock:
        observation = make_observation(game.world, game.tuning)
        return {
            "observation": observation.model_dump(),
            "day": game.world.day,
            "status": game.status,
        }


@app.post("/games/{game_id}/action")
async def submit_action(game_id: str, tool_call: ToolCall):
    game = _get_game(game_id)
    _check_in_progress(game)

    async with game.lock:
        game.last_activity = time.time()
        status, reason = _validate_tool_call(tool_call, game)

        if status == "accepted":
            game.pending_tool_calls.append(tool_call)
        game.transcript.append({
            "event": "action",
            "day": game.world.day,
            "tool": tool_call.tool,
            "args": tool_call.args,
            "status": status,
            "reason": reason,
        })

        response = {"status": status}
        if reason:
            response["reason"] = reason
        return response


@app.post("/games/{game_id}/end-turn")
async def end_turn(game_id: str):
    game = _get_game(game_id)
    _check_in_progress(game)

    async with game.lock:
        game.last_activity = time.time()
        action = _merge_tool_calls(game.pending_tool_calls, game.world)
        game.pending_tool_calls = []

        game.tuning = process_events(game.world, game.tuning, game.event_schedule, game.rng, default_tuning=game.default_tuning)

        result = await asyncio.to_thread(run_day, game.world, game.rng, game.tuning, action)

        game.cumulative_walkouts += result.total_walkouts
        game.cumulative_satisfaction.extend(result.satisfaction_scores)
        game.cumulative_waste_cost += game.world.waste_today_cost
        game.cumulative_ingredient_cost += game.world.ingredient_spend_today
        game.reputation_snapshots[game.world.day - 1] = game.world.reputation_ewma

        if game.world.bankrupt:
            game.status = "bankrupt"
        elif game.world.day > game.tuning.simulation.total_days:
            game.status = "completed"

        observation = make_observation(game.world, game.tuning)

        walkouts = result.total_walkouts
        if walkouts == 0:
            walkout_band = "None"
        elif walkouts <= 5:
            walkout_band = "Few"
        elif walkouts <= 20:
            walkout_band = "Some"
        else:
            walkout_band = "Many"

        day_result = {
            "total_covers": result.total_covers,
            "total_revenue": round(result.total_revenue, 2),
            "walkout_band": walkout_band,
            "dishes_sold": result.dishes_sold,
            "substitutions": result.substitution_count,
        }

        game.transcript.append({
            "event": "end_turn",
            "day": game.world.day - 1,
            "result": day_result,
            "status": game.status,
        })

        return {
            "observation": observation.model_dump(),
            "day": game.world.day,
            "day_result": day_result,
            "status": game.status,
        }


@app.get("/games/{game_id}/status")
async def game_status(game_id: str):
    game = _get_game(game_id)
    return GameStatusResponse(
        game_id=game.game_id,
        team_name=game.team_name,
        scenario=game.world.scenario_name,
        seed=game.world.seed,
        day=game.world.day,
        status=game.status,
        cash=round(game.world.cash, 2),
    )


@app.get("/games/{game_id}/score")
async def game_score(game_id: str):
    game = _get_game(game_id)
    if game.status == "in_progress":
        raise HTTPException(status_code=400, detail="Game is still in progress")

    score = _compute_score(game)

    ts = time.time()
    entry = LeaderboardEntry(
        team_name=game.team_name,
        scenario=game.world.scenario_name,
        seed=game.world.seed,
        score=score.total_score,
        days_survived=game.world.day - 1,
        timestamp=ts,
    )

    existing = next(
        (i for i, e in enumerate(leaderboard)
         if e.team_name == entry.team_name
         and e.scenario == entry.scenario
         and e.seed == entry.seed),
        None,
    )
    if existing is not None:
        leaderboard[existing] = entry
    else:
        leaderboard.append(entry)

    if _db is not None:
        _db.upsert(
            team_name=entry.team_name,
            scenario=entry.scenario,
            seed=entry.seed,
            score=entry.score,
            days_survived=entry.days_survived,
            timestamp=ts,
        )

    if config.PERSIST:
        save_transcript(game.transcript, game.game_id, config.DATA_DIR)

    return GameScoreResponse(
        game_id=game.game_id,
        team_name=game.team_name,
        scenario=game.world.scenario_name,
        seed=game.world.seed,
        score=score,
        days_survived=game.world.day - 1,
        final_cash=round(game.world.cash, 2),
        status=game.status,
    )


@app.get("/leaderboard")
async def get_leaderboard(scenario: str | None = None):
    entries = leaderboard
    if scenario:
        entries = [e for e in entries if e.scenario == scenario]

    best: dict[str, LeaderboardEntry] = {}
    for e in entries:
        key = e.team_name
        if key not in best or e.score > best[key].score:
            best[key] = e

    ranked = sorted(best.values(), key=lambda e: e.score, reverse=True)
    return [
        {
            "rank": i + 1,
            "team_name": e.team_name,
            "score": e.score,
            "days_survived": e.days_survived,
            "scenario": e.scenario,
            "seed": e.seed,
        }
        for i, e in enumerate(ranked)
    ]


@app.get("/leaderboard/evaluation")
async def get_evaluation_leaderboard(scenarios: str | None = None):
    """Hackathon ranking: average score per team across all (scenario, seed) entries.

    ?scenarios=baseline,inflation — restrict to specific scenarios.
    Default: all entries.
    """
    entries = leaderboard
    if scenarios:
        allowed = {s.strip() for s in scenarios.split(",")}
        entries = [e for e in entries if e.scenario in allowed]

    team_scores: dict[str, list[float]] = {}
    team_details: dict[str, dict] = {}
    for e in entries:
        team_scores.setdefault(e.team_name, []).append(e.score)
        if e.team_name not in team_details:
            team_details[e.team_name] = {"best": e.score, "worst": e.score}
        else:
            team_details[e.team_name]["best"] = max(team_details[e.team_name]["best"], e.score)
            team_details[e.team_name]["worst"] = min(team_details[e.team_name]["worst"], e.score)

    ranked = sorted(
        team_scores.items(),
        key=lambda kv: sum(kv[1]) / len(kv[1]),
        reverse=True,
    )

    return [
        {
            "rank": i + 1,
            "team_name": team,
            "avg_score": round(sum(scores) / len(scores), 2),
            "games_played": len(scores),
            "best_score": round(team_details[team]["best"], 2),
            "worst_score": round(team_details[team]["worst"], 2),
        }
        for i, (team, scores) in enumerate(ranked)
    ]


@app.get("/scenarios")
async def get_scenarios():
    return list_scenarios(include_hidden=_hidden_unlocked)


@app.get("/games/{game_id}/notes")
async def read_notes(game_id: str):
    game = _get_game(game_id)
    return {"notes": game.world.agent_notes}


@app.get("/games")
async def list_games(team_name: str | None = None):
    results = []
    for g in games.values():
        if team_name and g.team_name != team_name:
            continue
        results.append({
            "game_id": g.game_id,
            "team_name": g.team_name,
            "scenario": g.world.scenario_name,
            "day": g.world.day,
            "status": g.status,
        })
    return results


@app.delete("/games/{game_id}")
async def delete_game(game_id: str):
    game = _get_game(game_id)
    del games[game_id]
    return {"status": "deleted", "game_id": game_id}


# ---------------------------------------------------------------------------
# Admin endpoints (require RESTBENCH_ADMIN_TOKEN)
# ---------------------------------------------------------------------------


@app.get("/admin/status", dependencies=[Depends(_require_admin)])
async def admin_status():
    active = [g for g in games.values() if g.status == "in_progress"]
    teams_active = sorted({g.team_name for g in active})
    games_by_team: dict[str, int] = {}
    for g in active:
        games_by_team[g.team_name] = games_by_team.get(g.team_name, 0) + 1

    return {
        "uptime_seconds": int(time.monotonic() - _start_time),
        "total_games_created": _total_games_created,
        "active_games": len(active),
        "finished_games_in_memory": sum(1 for g in games.values() if g.status != "in_progress"),
        "leaderboard_entries": len(leaderboard),
        "games_expired": _games_expired,
        "teams_active": teams_active,
        "games_by_team": games_by_team,
        "hidden_unlocked": _hidden_unlocked,
        "config": {
            "max_concurrent_games": config.MAX_CONCURRENT_GAMES,
            "max_games_per_hour": config.MAX_GAMES_PER_HOUR,
            "game_expiry_seconds": config.GAME_EXPIRY_SECONDS,
            "persist": config.PERSIST,
        },
    }


@app.get("/admin/games", dependencies=[Depends(_require_admin)])
async def admin_list_games():
    now = time.time()
    return [
        {
            "game_id": g.game_id,
            "team_name": g.team_name,
            "scenario": g.world.scenario_name,
            "seed": g.world.seed,
            "day": g.world.day,
            "status": g.status,
            "cash": round(g.world.cash, 2),
            "created_at": g.created_at,
            "last_activity": g.last_activity,
            "idle_seconds": int(now - g.last_activity),
            "actions_submitted": len(g.transcript),
        }
        for g in games.values()
    ]


@app.get("/admin/leaderboard", dependencies=[Depends(_require_admin)])
async def admin_leaderboard():
    return [
        {
            "team_name": e.team_name,
            "scenario": e.scenario,
            "seed": e.seed,
            "score": e.score,
            "days_survived": e.days_survived,
            "timestamp": e.timestamp,
        }
        for e in sorted(leaderboard, key=lambda e: e.score, reverse=True)
    ]


@app.post("/admin/reset-leaderboard", dependencies=[Depends(_require_admin)])
async def admin_reset_leaderboard():
    leaderboard.clear()
    if _db is not None:
        _db.clear()
    return {"status": "leaderboard_cleared"}


@app.post("/admin/unlock-hidden", dependencies=[Depends(_require_admin)])
async def admin_unlock_hidden():
    global _hidden_unlocked
    _hidden_unlocked = True
    return {"status": "hidden_scenarios_unlocked", "hidden_unlocked": True}


@app.post("/admin/lock-hidden", dependencies=[Depends(_require_admin)])
async def admin_lock_hidden():
    global _hidden_unlocked
    _hidden_unlocked = False
    return {"status": "hidden_scenarios_locked", "hidden_unlocked": False}


@app.delete("/admin/games", dependencies=[Depends(_require_admin)])
async def admin_kill_all_games():
    count = len(games)
    games.clear()
    return {"status": "all_games_deleted", "count": count}


# ---------------------------------------------------------------------------
# Tool call validation and merging
# ---------------------------------------------------------------------------

VALID_TOOLS = {
    "place_order", "set_menu", "set_price", "set_staff_level",
    "set_marketing_spend", "run_happy_hour", "offer_daily_special", "save_notes",
}


def _validate_tool_call(tool_call: ToolCall, game: GameSession) -> tuple[str, str]:
    if tool_call.tool not in VALID_TOOLS:
        return "rejected", f"Unknown tool '{tool_call.tool}'"

    if tool_call.tool == "place_order":
        required = {"supplier", "ingredient", "quantity_kg"}
        if not required.issubset(tool_call.args.keys()):
            return "rejected", f"place_order requires: {required}"

    if tool_call.tool == "set_menu":
        if "dishes" not in tool_call.args:
            return "rejected", "set_menu requires 'dishes' (list of dish names)"

    if tool_call.tool == "set_price":
        if "dish" not in tool_call.args or "price" not in tool_call.args:
            return "rejected", "set_price requires 'dish' and 'price'"

    if tool_call.tool == "set_staff_level":
        if "level" not in tool_call.args:
            return "rejected", "set_staff_level requires 'level'"

    if tool_call.tool == "set_marketing_spend":
        if "amount" not in tool_call.args:
            return "rejected", "set_marketing_spend requires 'amount'"

    if tool_call.tool == "offer_daily_special":
        if "dish" not in tool_call.args:
            return "rejected", "offer_daily_special requires 'dish'"

    if tool_call.tool == "save_notes":
        if "text" not in tool_call.args:
            return "rejected", "save_notes requires 'text'"

    return "accepted", ""


def _merge_tool_calls(tool_calls: list[ToolCall], world: WorldState) -> AgentAction:
    orders: list[PurchaseOrderRequest] = []
    set_active_menu: list[str] | None = None
    set_prices: dict[str, float] = {}
    set_staff_level: int | None = None
    marketing_spend: float | None = None
    run_happy_hour: bool = False
    offer_daily_special: str | None = None
    notes: str = ""

    for tc in tool_calls:
        if tc.tool == "place_order":
            orders.append(PurchaseOrderRequest(
                supplier=tc.args["supplier"],
                ingredient=tc.args["ingredient"],
                quantity_kg=float(tc.args["quantity_kg"]),
            ))
        elif tc.tool == "set_menu":
            set_active_menu = tc.args["dishes"]
        elif tc.tool == "set_price":
            set_prices[tc.args["dish"]] = float(tc.args["price"])
        elif tc.tool == "set_staff_level":
            set_staff_level = int(tc.args["level"])
        elif tc.tool == "set_marketing_spend":
            marketing_spend = float(tc.args["amount"])
        elif tc.tool == "run_happy_hour":
            run_happy_hour = True
        elif tc.tool == "offer_daily_special":
            offer_daily_special = tc.args["dish"]
        elif tc.tool == "save_notes":
            notes = str(tc.args["text"])

    return AgentAction(
        orders=orders,
        set_active_menu=set_active_menu,
        set_prices=set_prices if set_prices else None,
        set_staff_level=set_staff_level,
        marketing_spend=marketing_spend,
        run_happy_hour=run_happy_hour,
        offer_daily_special=offer_daily_special,
        notes=notes,
    )


def _compute_score(game: GameSession) -> ScoreBreakdown:
    world = game.world
    tuning = game.tuning
    sc = tuning.scoring

    starting_cash = tuning.economics.starting_cash
    net_profit = world.cash - starting_cash

    mean_sat = (
        sum(game.cumulative_satisfaction) / len(game.cumulative_satisfaction)
        if game.cumulative_satisfaction else 0.0
    )
    sat_penalty = 0.0
    if mean_sat < sc.satisfaction_threshold:
        gap = sc.satisfaction_threshold - mean_sat
        sat_penalty = gap * gap * sc.satisfaction_penalty_coeff

    final_rep = endgame_weighted_reputation(
        game.reputation_snapshots,
        tuning.simulation.total_days,
        sc.endgame_weight_day25,
        sc.endgame_weight_day27,
        sc.endgame_weight_day30,
    )
    if not game.reputation_snapshots:
        final_rep = world.reputation_ewma

    rep_penalty = 0.0
    if final_rep < sc.reputation_threshold:
        gap = sc.reputation_threshold - final_rep
        rep_penalty = gap * gap * sc.reputation_penalty_coeff

    walkout_penalty = game.cumulative_walkouts * sc.walkout_penalty_per

    waste_penalty = 0.0
    if game.cumulative_ingredient_cost > 0:
        waste_rate = game.cumulative_waste_cost / game.cumulative_ingredient_cost
        if waste_rate > sc.waste_threshold:
            waste_penalty = (waste_rate - sc.waste_threshold) * sc.waste_penalty_coeff

    total = net_profit - sat_penalty - rep_penalty - walkout_penalty - waste_penalty

    if world.bankrupt:
        total = sc.bankruptcy_score

    return ScoreBreakdown(
        net_profit=round(net_profit, 2),
        satisfaction_penalty=round(sat_penalty, 2),
        reputation_penalty=round(rep_penalty, 2),
        walkout_penalty=round(walkout_penalty, 2),
        waste_penalty=round(waste_penalty, 2),
        total_score=round(total, 2),
    )
