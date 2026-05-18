"""Regime detection for weekend_safe_agent.

Regimes (checked in priority order):
  renovation_or_capacity  - capacity constraints halving revenue potential
  demand_collapse         - reputation-driven demand death spiral
  supplier_crisis         - reliability collapse across supply chain
  tourist_surge           - demand spike (capped at 3 consecutive days)
  normal                  - default operating mode

tourist_surge cooldown: max 3 consecutive days active, then 2 forced-normal
days regardless of cover counts. Prevents over-ordering into post-surge lull.
State keys consumed/written: surge_days, surge_pause.
"""

from __future__ import annotations


def detect_regime(observation: dict, state: dict) -> str:
    """Return the current operating regime string. May mutate state for surge cooldown."""
    cov: list[int] = state.get("cov", [])
    alerts_raw = observation.get("alerts", []) or []
    alerts = [str(a).lower() for a in alerts_raw]
    rep = observation.get("reputation_band", "Very Good")

    # ── renovation_or_capacity ────────────────────────────────────────────────
    if any("renovat" in a or "capacity" in a or "closed" in a for a in alerts):
        state["surge_days"] = 0
        return "renovation_or_capacity"
    if len(cov) >= 5 and sum(cov[-5:]) / 5 < 35:
        state["surge_days"] = 0
        return "renovation_or_capacity"

    # ── demand_collapse ───────────────────────────────────────────────────────
    if len(cov) >= 3 and rep in ("Fair", "Poor"):
        window7 = cov[-7:] if len(cov) >= 7 else cov
        avg7 = sum(window7) / len(window7)
        avg3 = sum(cov[-3:]) / 3
        if avg7 > 0 and avg3 < 0.5 * avg7:
            state["surge_days"] = 0
            return "demand_collapse"

    # ── supplier_crisis ───────────────────────────────────────────────────────
    if any("supply" in a or "shortage" in a or "disruption" in a for a in alerts):
        state["surge_days"] = 0
        return "supplier_crisis"
    rel_dict = state.get("rel", {})
    if rel_dict:
        all_ratios = [v for sup_d in rel_dict.values() for v in sup_d.values()]
        if all_ratios and sum(all_ratios) / len(all_ratios) < 0.65:
            state["surge_days"] = 0
            return "supplier_crisis"

    # ── tourist_surge — 3-day cap, 2-day cooldown ─────────────────────────────
    surge_pause = state.get("surge_pause", 0)
    if surge_pause > 0:
        # In forced-normal cooldown after surge
        state["surge_pause"] = surge_pause - 1
        state["surge_days"] = 0
        return "normal"

    if len(cov) >= 3:
        window7 = cov[-7:] if len(cov) >= 7 else cov
        avg7 = sum(window7) / len(window7)
        avg3 = sum(cov[-3:]) / 3
        if avg7 > 0 and avg3 > 1.3 * avg7:
            surge_days = state.get("surge_days", 0) + 1
            if surge_days >= 3:
                # Cap reached — enter 2-day forced normal
                state["surge_days"] = 0
                state["surge_pause"] = 2
                return "normal"
            state["surge_days"] = surge_days
            return "tourist_surge"

    state["surge_days"] = 0
    return "normal"
