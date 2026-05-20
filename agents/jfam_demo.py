"""JFAM demo — runs one game, writes a self-contained HTML replay.

The agent itself is the same locked submission (`jfam_agent.strategy`).
This wrapper records a rich per-day trace and embeds it into a
zero-dependency HTML page you can open offline or share.

    python -m agents.jfam_demo                       # baseline, seed 42
    python -m agents.jfam_demo tourist_season 7      # any scenario / seed
    python -m agents.jfam_demo --open                # opens the HTML

Outputs `demo/jfam_demo.html` and `demo/jfam_trace.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path
from typing import Any

import httpx

from agents.jfam_core import core_strategy, dump_state, load_dotenv, load_state

load_dotenv()


BASE_URL = os.getenv("RESTBENCH_URL", "http://52.48.183.209:8001")
OUT_DIR = Path("demo")


REGIME_COLORS = {
    "normal":             ("#3b82f6", "Normal"),
    "capacity_cut":       ("#f59e0b", "Capacity cut"),
    "supply_crisis":      ("#ef4444", "Supply crisis"),
    "demand_surge":       ("#10b981", "Demand surge"),
    "inflation":          ("#f97316", "Inflation"),
    "reputation_shock":   ("#dc2626", "Reputation shock"),
    "premium":            ("#8b5cf6", "Premium pivot"),
    "soft_demand":        ("#6b7280", "Soft demand"),
}


def _summarise_actions(actions: list[dict]) -> list[dict]:
    """Boil tool calls down to UI-friendly events. Drop save_notes."""
    out = []
    for a in actions:
        tool = a.get("tool")
        args = a.get("args", {})
        if tool == "save_notes":
            continue
        if tool == "place_order":
            out.append({
                "kind": "order",
                "label": f"Order {args.get('quantity_kg')}kg {args.get('ingredient')} "
                         f"from {args.get('supplier')}",
            })
        elif tool == "set_price":
            out.append({
                "kind": "price",
                "label": f"Reprice {args.get('dish')} → €{args.get('price'):.2f}",
            })
        elif tool == "set_staff_level":
            out.append({"kind": "staff", "label": f"Staff → {args.get('level')}"})
        elif tool == "set_marketing_spend":
            out.append({
                "kind": "marketing",
                "label": f"Marketing €{args.get('amount')}/day",
            })
        elif tool == "run_happy_hour":
            out.append({"kind": "happy", "label": "Run happy hour (15-18)"})
        elif tool == "offer_daily_special":
            out.append({
                "kind": "special",
                "label": f"Daily special: {args.get('dish')}",
            })
        elif tool == "set_menu":
            n = len(args.get("dishes", []))
            out.append({"kind": "menu", "label": f"Set menu ({n} dishes)"})
        else:
            out.append({"kind": tool or "?", "label": json.dumps(args)[:60]})
    return out


def run_demo(scenario: str, seed: int, team_name: str = "JFAM_demo") -> dict[str, Any]:
    trace: dict[str, Any] = {
        "scenario": scenario,
        "seed": seed,
        "team_name": team_name,
        "days": [],
        "score": None,
    }

    transport = httpx.HTTPTransport(retries=3)
    with httpx.Client(base_url=BASE_URL, timeout=60.0, transport=transport) as c:
        r = c.post("/games", json={
            "team_name": team_name,
            "scenario": scenario,
            "seed": seed,
        })
        r.raise_for_status()
        data = r.json()
        game_id = data["game_id"]
        observation = data["observation"]
        day = data["day"]

        print(f"Game {game_id} ▸ {scenario}/{seed} ▸ cash {observation['cash']:.0f}")

        for _ in range(30):
            state = load_state(observation)
            actions, state = core_strategy(observation, day, state)

            # state['regime'] is set by core_strategy via detect_regime.
            regime = state.get("regime", "normal")

            # Filter & summarise actions for the UI.
            ui_actions = _summarise_actions(actions)

            cash_before = observation["cash"]
            rep_band = observation.get("reputation_band", "Unknown")
            walkout_band = observation.get("walkout_band", "Unknown")
            trend = observation.get("customer_trend", "Unknown")
            staff = observation.get("staff_level", "?")
            wt = observation.get("weather_today") or "—"
            weather = wt if isinstance(wt, str) else wt.get("description", "—")
            alerts = list(observation.get("alerts") or [])

            # Persist notes alongside.
            actions = [a for a in actions if isinstance(a, dict) and a.get("tool")]
            actions.append({"tool": "save_notes", "args": {"text": dump_state(state)}})

            for tc in actions:
                rr = c.post(f"/games/{game_id}/action", json=tc)
                rr.raise_for_status()

            rr = c.post(f"/games/{game_id}/end-turn")
            rr.raise_for_status()
            turn = rr.json()

            dr = turn["day_result"]
            observation = turn["observation"]
            new_day = turn["day"]
            status = turn["status"]

            day_record = {
                "day": day,                      # the day we just acted on
                "weekday": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][(day - 1) % 7],
                "cash_before": round(cash_before, 2),
                "cash_after": round(observation["cash"], 2),
                "covers": dr.get("total_covers", 0),
                "revenue": round(dr.get("total_revenue", 0.0), 2),
                "walkout_band_yesterday": dr.get("walkout_band", "—"),
                "regime": regime,
                "rep_band": rep_band,
                "walkout_band": walkout_band,
                "trend": trend,
                "staff": staff,
                "weather": weather,
                "alerts": alerts,
                "actions": ui_actions,
            }
            trace["days"].append(day_record)

            print(
                f"  Day {day:>2} [{regime:>14}] cash {observation['cash']:>7.0f} | "
                f"covers {dr.get('total_covers', 0):>3} | "
                f"rev €{dr.get('total_revenue', 0):>6.0f} | "
                f"{len(ui_actions)} actions"
            )

            day = new_day
            if status != "in_progress":
                print(f"Status: {status}")
                break

        r = c.get(f"/games/{game_id}/score")
        r.raise_for_status()
        trace["score"] = r.json()

    return trace


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>JFAM — autonomous restaurant agent (Prosus × AISO hackathon)</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {
    --bg:#0b0f14; --panel:#121821; --panel2:#0e131b; --border:#1f2937;
    --text:#e5e7eb; --muted:#94a3b8; --accent:#10b981; --danger:#ef4444;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Inter", system-ui, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.4;
  }
  header { padding: 28px 32px 8px; border-bottom: 1px solid var(--border); }
  h1 { margin: 0 0 4px; font-size: 22px; font-weight: 600; }
  .sub { color: var(--muted); font-size: 13px; }
  .layout {
    display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px;
    padding: 16px 32px 32px; max-width: 1280px; margin: 0 auto;
  }
  .panel {
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px;
  }
  .panel h2 { margin: 0 0 12px; font-size: 13px; font-weight: 600;
              text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
  .stat {
    background: var(--panel2); border: 1px solid var(--border); border-radius: 8px;
    padding: 10px 12px;
  }
  .stat .v { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .stat .l { color: var(--muted); font-size: 11px; text-transform: uppercase;
             letter-spacing: .05em; margin-top: 2px; }
  .badge {
    display: inline-block; padding: 3px 9px; border-radius: 999px;
    font-size: 11px; font-weight: 600; color: white;
  }
  canvas { display: block; width: 100%; height: 220px; }
  .controls { display: flex; gap: 8px; align-items: center; margin-top: 12px; }
  button {
    background: #1f2937; color: var(--text); border: 1px solid var(--border);
    border-radius: 8px; padding: 6px 14px; font-size: 13px; cursor: pointer;
  }
  button:hover { background: #2c3a4f; }
  input[type=range] { flex: 1; accent-color: var(--accent); }
  .meta { font-variant-numeric: tabular-nums; color: var(--muted); font-size: 12px; }
  .day-title { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
  .day-title .num { font-size: 26px; font-weight: 700; }
  .day-title .dow { color: var(--muted); }
  .row { display: flex; justify-content: space-between; padding: 4px 0;
         border-bottom: 1px dashed var(--border); font-size: 13px; }
  .row:last-child { border: 0; }
  .row .k { color: var(--muted); }
  .actions { display: flex; flex-direction: column; gap: 6px; max-height: 260px;
             overflow: auto; padding-right: 4px; }
  .action {
    display: flex; align-items: center; gap: 8px;
    background: var(--panel2); border: 1px solid var(--border); border-radius: 6px;
    padding: 6px 10px; font-size: 13px;
  }
  .pill { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .alerts { color: #fbbf24; font-size: 12px; margin-top: 6px; min-height: 16px; }
  .scorecard { margin-top: 16px; padding: 16px; background: var(--panel2);
               border: 1px solid var(--border); border-radius: 8px;
               display: none; }
  .scorecard.show { display: block; }
  .scorecard .big { font-size: 32px; font-weight: 700; color: var(--accent);
                    font-variant-numeric: tabular-nums; }
  footer { text-align: center; color: var(--muted); font-size: 12px;
           padding: 16px; border-top: 1px solid var(--border); }
  a { color: var(--accent); text-decoration: none; }
  @media (max-width: 900px) {
    .layout { grid-template-columns: 1fr; padding: 12px; }
    .stats { grid-template-columns: repeat(2, 1fr); }
  }
</style>
</head>
<body>
<header>
  <h1>JFAM — autonomous restaurant agent</h1>
  <div class="sub">
    Live replay of one 30-day game from the Prosus × AISO 2026 hackathon.
    Scenario: <strong id="scenario-name">__SCENARIO__</strong> ·
    seed <strong>__SEED__</strong> ·
    pure-rules core, zero LLM tokens during play.
  </div>
</header>

<div class="layout">
  <div>
    <div class="panel">
      <h2>Cash trajectory (€)</h2>
      <canvas id="chart"></canvas>
      <div class="controls">
        <button id="playBtn">▶ Play</button>
        <button id="resetBtn">↻ Restart</button>
        <input type="range" id="scrub" min="0" max="29" value="0">
        <span class="meta" id="dayLabel">Day 1</span>
      </div>
    </div>

    <div class="panel" style="margin-top:16px;">
      <h2>The day’s state</h2>
      <div class="day-title">
        <span class="num" id="dayNum">1</span>
        <span class="dow" id="dayDow">Mon</span>
        <span class="badge" id="regimeBadge" style="background:#3b82f6">Normal</span>
      </div>
      <div class="stats">
        <div class="stat"><div class="v" id="sCash">€0</div><div class="l">Cash</div></div>
        <div class="stat"><div class="v" id="sCovers">0</div><div class="l">Covers yesterday</div></div>
        <div class="stat"><div class="v" id="sRev">€0</div><div class="l">Revenue yesterday</div></div>
        <div class="stat"><div class="v" id="sStaff">0</div><div class="l">Staff today</div></div>
      </div>
      <div style="margin-top:12px;">
        <div class="row"><span class="k">Reputation band</span><span id="sRep">—</span></div>
        <div class="row"><span class="k">Walkout band</span><span id="sWalk">—</span></div>
        <div class="row"><span class="k">Customer trend</span><span id="sTrend">—</span></div>
        <div class="row"><span class="k">Weather</span><span id="sWeather">—</span></div>
      </div>
      <div class="alerts" id="alerts"></div>
    </div>
  </div>

  <div>
    <div class="panel">
      <h2>What the agent decided</h2>
      <div class="actions" id="actionList"></div>
    </div>
    <div class="panel scorecard" id="scorecard">
      <h2>Final score</h2>
      <div class="big" id="scoreBig">€0</div>
      <div class="meta" id="scoreSub" style="margin-top:8px;"></div>
    </div>
  </div>
</div>

<footer>
  JFAM · Prosus × AISO Spring Hackathon 2026 · finalist, top 4. ·
  <a href="https://github.com/Fan-shiyu/Prosus-Hackathon" target="_blank">code</a>
</footer>

<script>
const TRACE = __TRACE_JSON__;
const REGIME = __REGIME_COLORS__;

const elPlay = document.getElementById('playBtn');
const elReset = document.getElementById('resetBtn');
const elScrub = document.getElementById('scrub');
const elDayLabel = document.getElementById('dayLabel');
const canvas = document.getElementById('chart');
const ctx = canvas.getContext('2d');
let playing = false;
let idx = 0;
let timer = null;

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = canvas.clientWidth * dpr;
  canvas.height = canvas.clientHeight * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener('resize', () => { resizeCanvas(); drawChart(idx); });
resizeCanvas();

function fmtMoney(v) {
  const n = Math.round(v);
  return '€' + n.toLocaleString('en-US');
}

function drawChart(upto) {
  const W = canvas.clientWidth, H = canvas.clientHeight;
  ctx.clearRect(0, 0, W, H);
  const cash = TRACE.days.map(d => d.cash_after);
  const maxC = Math.max(...cash, 60000);
  const minC = Math.min(...cash, 0);
  const padL = 48, padR = 12, padT = 12, padB = 22;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  // Axes
  ctx.strokeStyle = '#1f2937';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = padT + (plotH * i / 4);
    const v = maxC - (maxC - minC) * i / 4;
    ctx.beginPath();
    ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.fillStyle = '#64748b';
    ctx.font = '10px -apple-system,system-ui,sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(fmtMoney(v), padL - 6, y + 3);
  }

  // Cash line
  ctx.strokeStyle = '#10b981';
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i <= upto; i++) {
    const x = padL + (plotW * i / (TRACE.days.length - 1));
    const y = padT + plotH * (1 - (cash[i] - minC) / (maxC - minC || 1));
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();

  // Marker
  if (upto >= 0) {
    const i = upto;
    const x = padL + (plotW * i / (TRACE.days.length - 1));
    const y = padT + plotH * (1 - (cash[i] - minC) / (maxC - minC || 1));
    ctx.fillStyle = '#10b981';
    ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fill();
  }

  // Starting cash marker (15,000)
  ctx.strokeStyle = '#6b728044';
  ctx.setLineDash([3, 3]);
  const y0 = padT + plotH * (1 - (15000 - minC) / (maxC - minC || 1));
  ctx.beginPath(); ctx.moveTo(padL, y0); ctx.lineTo(W - padR, y0); ctx.stroke();
  ctx.setLineDash([]);
}

function render(i) {
  const d = TRACE.days[i];
  document.getElementById('dayNum').textContent = 'Day ' + d.day;
  document.getElementById('dayDow').textContent = d.weekday;
  const [color, label] = REGIME[d.regime] || ['#3b82f6', d.regime];
  const b = document.getElementById('regimeBadge');
  b.textContent = label; b.style.background = color;

  document.getElementById('sCash').textContent = fmtMoney(d.cash_after);
  document.getElementById('sCovers').textContent = d.covers;
  document.getElementById('sRev').textContent = fmtMoney(d.revenue);
  document.getElementById('sStaff').textContent = d.staff;
  document.getElementById('sRep').textContent = d.rep_band;
  document.getElementById('sWalk').textContent = d.walkout_band;
  document.getElementById('sTrend').textContent = d.trend;
  document.getElementById('sWeather').textContent = d.weather;

  document.getElementById('alerts').textContent =
    (d.alerts && d.alerts.length) ? '⚠ ' + d.alerts.join(' · ') : '';

  const list = document.getElementById('actionList');
  list.innerHTML = '';
  if (!d.actions.length) {
    const e = document.createElement('div');
    e.className = 'action';
    e.innerHTML = '<span class="pill" style="background:#475569"></span><span style="color:#94a3b8">No tool calls today (besides save_notes)</span>';
    list.appendChild(e);
  }
  d.actions.forEach(a => {
    const e = document.createElement('div');
    e.className = 'action';
    const colors = {
      order:'#3b82f6', price:'#a855f7', staff:'#f59e0b',
      marketing:'#10b981', happy:'#ec4899', special:'#06b6d4', menu:'#fbbf24'
    };
    const c = colors[a.kind] || '#64748b';
    e.innerHTML = '<span class="pill" style="background:' + c + '"></span>' +
                  '<span>' + a.label + '</span>';
    list.appendChild(e);
  });

  elDayLabel.textContent = 'Day ' + d.day + ' of 30';
  elScrub.value = i;
  drawChart(i);

  // Reveal score on last day
  const sc = document.getElementById('scorecard');
  if (i === TRACE.days.length - 1 && TRACE.score) {
    const s = TRACE.score.score || TRACE.score;
    document.getElementById('scoreBig').textContent = fmtMoney(s.total_score || s);
    document.getElementById('scoreSub').innerHTML =
      'Net profit: ' + fmtMoney(s.net_profit) + ' · ' +
      'walkout pen: ' + fmtMoney(s.walkout_penalty) + ' · ' +
      'waste pen: ' + fmtMoney(s.waste_penalty) + ' · ' +
      'days survived: ' + TRACE.score.days_survived + '/30';
    sc.classList.add('show');
  } else {
    sc.classList.remove('show');
  }
}

function step() {
  if (idx < TRACE.days.length - 1) {
    idx += 1;
    render(idx);
  } else {
    pause();
  }
}
function play() {
  playing = true;
  elPlay.textContent = '❚❚ Pause';
  if (idx >= TRACE.days.length - 1) idx = 0;
  timer = setInterval(step, 600);
}
function pause() {
  playing = false;
  elPlay.textContent = '▶ Play';
  if (timer) clearInterval(timer);
}
elPlay.addEventListener('click', () => playing ? pause() : play());
elReset.addEventListener('click', () => { pause(); idx = 0; render(idx); });
elScrub.addEventListener('input', e => { pause(); idx = +e.target.value; render(idx); });

elScrub.max = TRACE.days.length - 1;
render(0);
</script>
</body>
</html>
"""


def write_html(trace: dict[str, Any], path: Path) -> None:
    html = (
        HTML_TEMPLATE
        .replace("__SCENARIO__", trace["scenario"])
        .replace("__SEED__", str(trace["seed"]))
        .replace("__TRACE_JSON__", json.dumps(trace))
        .replace("__REGIME_COLORS__", json.dumps(REGIME_COLORS))
    )
    path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", nargs="?", default="baseline")
    parser.add_argument("seed", nargs="?", type=int, default=42)
    parser.add_argument("--open", action="store_true",
                        help="Open the HTML in your browser after writing it.")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    trace = run_demo(args.scenario, args.seed)

    json_path = OUT_DIR / "jfam_trace.json"
    html_path = OUT_DIR / "jfam_demo.html"
    json_path.write_text(json.dumps(trace, indent=2), encoding="utf-8")
    write_html(trace, html_path)

    s = trace["score"]["score"]
    print(f"\nFinal score: €{s['total_score']:.0f}  ·  "
          f"days survived {trace['score']['days_survived']}/30")
    print(f"Wrote {html_path}  ({html_path.stat().st_size//1024} KB)")
    print(f"Wrote {json_path}")

    if args.open:
        webbrowser.open(html_path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
