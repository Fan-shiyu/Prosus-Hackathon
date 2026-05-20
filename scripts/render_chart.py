"""Render a static cash trajectory chart from a jfam_demo trace.

    python scripts/render_chart.py demo/jfam_trace.json docs/img/cash.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch


REGIME_COLORS = {
    "normal":             "#3b82f6",
    "capacity_cut":       "#f59e0b",
    "supply_crisis":      "#ef4444",
    "demand_surge":       "#10b981",
    "inflation":          "#f97316",
    "reputation_shock":   "#dc2626",
    "premium":            "#8b5cf6",
    "soft_demand":        "#6b7280",
}


def main(trace_path: str, out_path: str) -> None:
    trace = json.loads(Path(trace_path).read_text())
    days = [d["day"] for d in trace["days"]]
    cash = [d["cash_after"] for d in trace["days"]]
    regimes = [d["regime"] for d in trace["days"]]
    covers = [d["covers"] for d in trace["days"]]
    scenario = trace["scenario"]
    seed = trace["seed"]
    score = trace["score"]["score"]["total_score"]

    plt.style.use("dark_background")
    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(10, 5), height_ratios=[3, 1], sharex=True,
        gridspec_kw={"hspace": 0.08},
    )
    fig.patch.set_facecolor("#0b0f14")
    for a in (ax, ax2):
        a.set_facecolor("#0b0f14")
        for spine in a.spines.values():
            spine.set_color("#1f2937")
        a.tick_params(colors="#94a3b8")
        a.grid(True, color="#1f2937", linewidth=0.6)

    ax.plot(days, cash, color="#10b981", linewidth=2.5, zorder=3)
    ax.fill_between(days, cash, 0, color="#10b981", alpha=0.10, zorder=2)
    ax.axhline(15000, color="#6b7280", linestyle=":", linewidth=1, alpha=0.6)
    ax.text(0.5, 15000 + 600, "starting cash €15,000",
            color="#6b7280", fontsize=8)

    used = []
    last = None
    span_start = days[0]
    for i, r in enumerate(regimes):
        if r != last and last is not None:
            if last != "normal":
                ax.axvspan(span_start - 0.5, days[i] - 0.5,
                           color=REGIME_COLORS.get(last, "#444"), alpha=0.10, zorder=1)
                if last not in used:
                    used.append(last)
            span_start = days[i]
        last = r
    if last and last != "normal":
        ax.axvspan(span_start - 0.5, days[-1] + 0.5,
                   color=REGIME_COLORS.get(last, "#444"), alpha=0.10, zorder=1)
        if last not in used:
            used.append(last)

    ax.set_ylabel("Cash (€)", color="#e5e7eb")
    ax.set_title(
        f"JFAM — {scenario}/seed {seed} — final score €{score:,.0f}",
        color="#e5e7eb", loc="left", pad=12, fontsize=12,
    )
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(lambda v, _: f"€{int(v):,}")

    ax2.bar(days, covers, color="#94a3b8", width=0.7, alpha=0.7)
    ax2.set_xlabel("Day", color="#e5e7eb")
    ax2.set_ylabel("Covers", color="#e5e7eb")
    ax2.set_xticks(range(0, 31, 5))

    if used:
        handles = [Patch(facecolor=REGIME_COLORS.get(r, "#444"), alpha=0.35, label=r)
                   for r in used]
        ax.legend(handles=handles, loc="upper left", fontsize=8,
                  facecolor="#0e131b", edgecolor="#1f2937", labelcolor="#e5e7eb")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor="#0b0f14")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
