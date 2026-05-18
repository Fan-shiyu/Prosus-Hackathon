"""Generate a static HTML dashboard from logs/*.jsonl files.

Usage:
    python dashboard.py                        # reads logs/, writes dashboard.html
    python dashboard.py --logs-dir path/to/logs --output my.html
"""
from __future__ import annotations

import argparse
import json
import pathlib
from datetime import datetime

SCENARIO_COLORS = {
    "baseline":      "#4ECDC4",
    "supply_crisis": "#FF6B6B",
    "tourist_season": "#45B7D1",
    "renovation":    "#FFA07A",
    "inflation":     "#98D8C8",
    "health_scare":  "#C3A6FF",
}
DEFAULT_COLOR = "#AAAAAA"
WALKOUT_MAP = {"None": 0, "Few": 1, "Some": 2, "Many": 3}


def load_runs(log_dir: str) -> list[dict]:
    runs = []
    p = pathlib.Path(log_dir)
    if not p.exists():
        return runs
    for path in sorted(p.glob("*.jsonl")):
        meta: dict = {}
        days: list[dict] = []
        summary: dict = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = rec.get("type")
                if t == "meta":
                    meta = rec
                elif t == "day":
                    days.append(rec)
                elif t == "summary":
                    summary = rec
        if days:
            runs.append({"meta": meta, "days": days, "summary": summary, "file": path.name})
    return runs


def _fmt(v: object) -> str:
    if isinstance(v, float):
        return f"{v:,.0f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v) if v is not None else "—"


def _run_label(run: dict) -> str:
    m = run["meta"]
    return f"{m.get('scenario', '?')} s{m.get('seed', '?')}"


def generate_html(runs: list[dict], generated_at: str) -> str:
    if not runs:
        return (
            "<!DOCTYPE html><html><body style='font-family:sans-serif;padding:40px'>"
            "<h1>No logs found</h1><p>Run the agent first — logs will appear in logs/</p>"
            "</body></html>"
        )

    scores = [r["summary"]["score"] for r in runs if "score" in r["summary"]]
    avg_score = sum(scores) / len(scores) if scores else 0
    best_score = max(scores) if scores else 0
    bankruptcies = sum(1 for r in runs if r["summary"].get("status") == "bankrupt")
    scenarios = sorted({r["meta"].get("scenario", "?") for r in runs})
    seeds = sorted({str(r["meta"].get("seed", "?")) for r in runs})

    avg_cls = "good" if avg_score > 0 else "bad"
    bankrupt_cls = "bad" if bankruptcies > 0 else "good"

    # Summary table rows
    table_rows_html_parts = []
    sorted_runs = sorted(runs, key=lambda r: (r["meta"].get("scenario", ""), r["meta"].get("seed", 0)))
    for run in sorted_runs:
        m = run["meta"]
        s = run["summary"]
        score = s.get("score")
        status = s.get("status", "?")
        row_cls = "bankrupt" if status == "bankrupt" else ""
        score_cls = "score good" if isinstance(score, (int, float)) and score > 0 else "score bad"
        table_rows_html_parts.append(
            f'<tr class="{row_cls}">'
            f'<td>{m.get("scenario","?")}</td>'
            f'<td>{m.get("seed","?")}</td>'
            f'<td class="{score_cls}">{_fmt(score)}</td>'
            f'<td>{_fmt(s.get("net_profit"))}</td>'
            f'<td>{_fmt(s.get("walk_pen"))}</td>'
            f'<td>{_fmt(s.get("rep_pen"))}</td>'
            f'<td>{_fmt(s.get("waste_pen"))}</td>'
            f'<td>{_fmt(s.get("days"))}</td>'
            f'<td>{_fmt(s.get("final_cash"))}</td>'
            f'<td class="{row_cls}">{status}</td>'
            f'</tr>'
        )
    table_html = "\n".join(table_rows_html_parts)

    runs_json = json.dumps([
        {
            "label": _run_label(r),
            "scenario": r["meta"].get("scenario", "?"),
            "seed": str(r["meta"].get("seed", "?")),
            "color": SCENARIO_COLORS.get(r["meta"].get("scenario", ""), DEFAULT_COLOR),
            "days": [d["day"] for d in r["days"]],
            "cash_start": [d.get("cash_start", 0) for d in r["days"]],
            "cash_end": [d.get("cash_end", 0) for d in r["days"]],
            "covers": [d.get("covers", 0) for d in r["days"]],
            "revenue": [d.get("revenue", 0) for d in r["days"]],
            "walkouts": [WALKOUT_MAP.get(d.get("walkouts", "None"), 0) for d in r["days"]],
            "staff": [d.get("staff", 0) for d in r["days"]],
            "mkt": [d.get("mkt", 0) for d in r["days"]],
            "ok": [d.get("ok", 0) for d in r["days"]],
            "rej": [d.get("rej", 0) for d in r["days"]],
            "rep": [d.get("rep", "?") for d in r["days"]],
            "summary": r["summary"],
        }
        for r in runs
    ], separators=(",", ":"))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Restaurant Agent Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;color:#1a1a2e}}
header{{background:#1a1a2e;color:#fff;padding:20px 32px;display:flex;align-items:baseline;gap:16px}}
header h1{{font-size:1.4rem;font-weight:700}}
header p{{font-size:0.8rem;opacity:.6}}
.container{{max-width:1400px;margin:0 auto;padding:24px 32px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin-bottom:24px}}
.card{{background:#fff;border-radius:12px;padding:18px 22px;box-shadow:0 1px 6px rgba(0,0,0,.07)}}
.card .lbl{{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:#888;margin-bottom:6px}}
.card .val{{font-size:1.9rem;font-weight:700}}
.good{{color:#27ae60}}.bad{{color:#e74c3c}}.neutral{{color:#1a1a2e}}
.panel{{background:#fff;border-radius:12px;padding:22px;box-shadow:0 1px 6px rgba(0,0,0,.07);margin-bottom:22px}}
.panel h2{{font-size:.9rem;font-weight:600;color:#555;text-transform:uppercase;letter-spacing:.05em;margin-bottom:14px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-bottom:22px}}
@media(max-width:860px){{.grid2{{grid-template-columns:1fr}}}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}
th{{text-align:left;padding:9px 12px;font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:#888;border-bottom:2px solid #eee}}
td{{padding:9px 12px;border-bottom:1px solid #f0f0f0}}
tr:last-child td{{border-bottom:none}}
tr.bankrupt{{background:#fff8f8}}
tr.bankrupt td{{color:#e74c3c}}
td.score.good{{color:#27ae60;font-weight:600}}
td.score.bad{{color:#e74c3c;font-weight:600}}
.filter-bar{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:12px 22px;background:#fff;border-radius:12px;box-shadow:0 1px 6px rgba(0,0,0,.07);margin-bottom:22px}}
.filter-lbl{{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:#888;margin-right:4px}}
.fbtn{{padding:5px 14px;border-radius:20px;border:2px solid #ddd;background:#fff;cursor:pointer;font-size:.78rem;font-weight:500;transition:background .15s,color .15s}}
.fbtn.on{{color:#fff}}
</style>
</head>
<body>
<header>
  <h1>Restaurant Agent Dashboard</h1>
  <p>Generated {generated_at} &middot; {len(runs)} runs</p>
</header>
<div class="container">

<div class="cards">
  <div class="card"><div class="lbl">Runs</div><div class="val neutral">{len(runs)}</div></div>
  <div class="card"><div class="lbl">Avg Score</div><div class="val {avg_cls}">{avg_score:+,.0f}</div></div>
  <div class="card"><div class="lbl">Best Score</div><div class="val good">{best_score:+,.0f}</div></div>
  <div class="card"><div class="lbl">Bankruptcies</div><div class="val {bankrupt_cls}">{bankruptcies}</div></div>
  <div class="card"><div class="lbl">Scenarios</div><div class="val neutral">{len(scenarios)}</div></div>
</div>

<div class="panel"><h2>Score by Scenario &amp; Seed</h2><div id="ch-scores"></div></div>

<div class="filter-bar"><span class="filter-lbl">Filter scenarios:</span><div id="filter-btns" style="display:flex;gap:8px;flex-wrap:wrap"></div></div>

<div class="grid2">
  <div class="panel"><h2>Cash Trajectory</h2><div id="ch-cash"></div></div>
  <div class="panel"><h2>Daily Covers</h2><div id="ch-covers"></div></div>
</div>

<div class="grid2">
  <div class="panel"><h2>Daily Revenue</h2><div id="ch-revenue"></div></div>
  <div class="panel"><h2>Walkout Severity</h2><div id="ch-walkouts"></div></div>
</div>

<div class="grid2">
  <div class="panel"><h2>Staff Level</h2><div id="ch-staff"></div></div>
  <div class="panel"><h2>Marketing Spend</h2><div id="ch-mkt"></div></div>
</div>

<div class="panel"><h2>Penalty Breakdown per Run</h2><div id="ch-penalties"></div></div>

<div class="panel">
  <h2>All Runs</h2>
  <table>
    <thead><tr>
      <th>Scenario</th><th>Seed</th><th>Score</th><th>Net Profit</th>
      <th>Walkout Pen</th><th>Rep Pen</th><th>Waste Pen</th>
      <th>Days</th><th>Final Cash</th><th>Status</th>
    </tr></thead>
    <tbody>{table_html}</tbody>
  </table>
</div>

</div>
<script>
const RUNS={runs_json};
const BASE={{margin:{{l:55,r:15,t:10,b:45}},paper_bgcolor:'white',plot_bgcolor:'#fafafa',
  font:{{family:'-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',size:11}},
  legend:{{orientation:'h',y:-0.2,font:{{size:10}}}},hovermode:'closest'}};
const CFG={{responsive:true,displayModeBar:false}};

// Scores — use the most-recent run per (scenario, seed) in case of re-runs
(()=>{{
  const scenarios=[...new Set(RUNS.map(r=>r.scenario))].sort();
  const seeds=[...new Set(RUNS.map(r=>r.seed))].sort();
  const showText=RUNS.length<=12;
  const traces=seeds.map(seed=>{{
    const ys=scenarios.map(sc=>{{
      const matches=RUNS.filter(r=>r.scenario===sc&&r.seed===seed);
      const r=matches.length?matches[matches.length-1]:null;
      return r?(r.summary.score??null):null;
    }});
    return{{name:'seed '+seed,x:scenarios,y:ys,type:'bar',
      text:showText?ys.map(v=>v!=null?(v>=0?'+':'')+Math.round(v).toLocaleString():''):[],
      textposition:'outside',textfont:{{size:10}}}};
  }});
  Plotly.newPlot('ch-scores',traces,{{...BASE,barmode:'group',height:300,
    margin:{{l:60,r:15,t:20,b:60}},
    shapes:[{{type:'line',x0:-0.5,x1:scenarios.length-0.5,y0:0,y1:0,
      line:{{color:'#e74c3c',width:1,dash:'dot'}}}}],
    yaxis:{{title:'Score'}}}},CFG);
}})();

// Scenario filter buttons — toggle visibility across all line charts
const LINE_IDS=['ch-cash','ch-covers','ch-revenue','ch-walkouts','ch-staff','ch-mkt'];
const SC_COLORS=Object.fromEntries(RUNS.map(r=>[r.scenario,r.color]));
const allScenarios=[...new Set(RUNS.map(r=>r.scenario))].sort();
const activeScenarios=new Set(allScenarios);

(()=>{{
  const bar=document.getElementById('filter-btns');
  allScenarios.forEach(sc=>{{
    const btn=document.createElement('button');
    btn.className='fbtn on';
    btn.textContent=sc;
    const c=SC_COLORS[sc]||'#aaa';
    btn.style.borderColor=c;
    btn.style.background=c;
    btn.onclick=()=>{{
      if(activeScenarios.has(sc)){{activeScenarios.delete(sc);btn.classList.remove('on');btn.style.background='#fff';btn.style.color=c;}}
      else{{activeScenarios.add(sc);btn.classList.add('on');btn.style.background=c;btn.style.color='#fff';}}
      const vis=RUNS.map(r=>activeScenarios.has(r.scenario)?true:'legendonly');
      LINE_IDS.forEach(id=>{{
        const el=document.getElementById(id);
        if(el&&el.data)Plotly.restyle(id,{{visible:vis}});
      }});
    }};
    bar.appendChild(btn);
  }});
}})();

// Line chart helper
function lineChart(id,key,ytitle,extra={{}}){{
  const traces=RUNS.map(r=>{{
    return{{name:r.label,x:r.days,y:r[key],type:'scatter',mode:'lines',
      line:{{color:r.color,width:1.8}},showlegend:false,
      hovertemplate:'%{{y:,.0f}}<extra>'+r.label+'</extra>'}};
  }});
  Plotly.newPlot(id,traces,{{...BASE,height:260,yaxis:{{title:ytitle}},
    xaxis:{{title:'Day'}},...extra}},CFG);
}}

lineChart('ch-cash','cash_end','Cash (EUR)',{{
  shapes:[{{type:'line',x0:1,x1:30,y0:0,y1:0,line:{{color:'#e74c3c',width:1,dash:'dot'}}}}]}});
lineChart('ch-covers','covers','Covers');
lineChart('ch-revenue','revenue','Revenue (EUR)');
lineChart('ch-staff','staff','Staff Level');
lineChart('ch-mkt','mkt','Marketing (EUR)');

// Walkouts
(()=>{{
  const traces=RUNS.map(r=>{{
    return{{name:r.label,x:r.days,y:r.walkouts,type:'scatter',mode:'lines+markers',
      line:{{color:r.color,width:1.5}},marker:{{size:4}},showlegend:false,
      hovertemplate:'%{{y}}<extra>'+r.label+'</extra>'}};
  }});
  Plotly.newPlot('ch-walkouts',traces,{{...BASE,height:260,
    yaxis:{{title:'Severity',tickvals:[0,1,2,3],ticktext:['None','Few','Some','Many']}},
    xaxis:{{title:'Day'}}}},CFG);
}})();

// Penalties stacked bar — short label to fit 18 bars
(()=>{{
  const labels=RUNS.map(r=>r.label.replace('tourist_season','tourist').replace('supply_crisis','supply'));
  const abs=v=>Math.abs(v??0);
  const nRuns=RUNS.length;
  Plotly.newPlot('ch-penalties',[
    {{name:'Walkout',x:labels,y:RUNS.map(r=>abs(r.summary.walk_pen)),type:'bar',marker:{{color:'#FF6B6B'}}}},
    {{name:'Reputation',x:labels,y:RUNS.map(r=>abs(r.summary.rep_pen)),type:'bar',marker:{{color:'#FFA07A'}}}},
    {{name:'Satisfaction',x:labels,y:RUNS.map(r=>abs(r.summary.sat_pen)),type:'bar',marker:{{color:'#FFD166'}}}},
    {{name:'Waste',x:labels,y:RUNS.map(r=>abs(r.summary.waste_pen)),type:'bar',marker:{{color:'#98D8C8'}}}},
  ],{{...BASE,barmode:'stack',height:nRuns>12?420:320,
    margin:{{l:60,r:15,t:10,b:nRuns>12?120:90}},
    yaxis:{{title:'Penalty (EUR)'}},
    xaxis:{{tickangle:nRuns>8?-50:-30}}}},CFG);
}})();
</script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate HTML dashboard from game logs")
    parser.add_argument("--logs-dir", default="logs", help="Directory containing .jsonl log files")
    parser.add_argument("--output", default="dashboard.html", help="Output HTML file path")
    args = parser.parse_args()

    runs = load_runs(args.logs_dir)
    print(f"Loaded {len(runs)} runs from {args.logs_dir}/")

    html = generate_html(runs, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    out = pathlib.Path(args.output)
    out.write_text(html, encoding="utf-8")
    print(f"Dashboard written → {out.resolve()}")
    print("Open it in a browser: start dashboard.html")


if __name__ == "__main__":
    main()
