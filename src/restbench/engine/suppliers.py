"""Supplier state machines and effective reliability.

Each supplier has a hidden state: Normal -> Disrupted -> Extended_Outage.
Transitions happen daily with tunable probabilities. The agent never sees
the state directly but can infer it from delivery failures and alerts.
"""

from __future__ import annotations

from restbench.core.rng import SimRNG
from restbench.core.types import WorldState
from restbench.engine.tuning import SupplierStateConfig


def advance_supplier_states(world: WorldState, rng: SimRNG, config: SupplierStateConfig) -> None:
    for state in world.supplier_states:
        roll = float(rng.supplier.random())
        old_status = state.status

        if state.status == "Normal":
            if roll < config.disruption_prob:
                state.status = "Disrupted"
        elif state.status == "Disrupted":
            if roll < config.recovery_from_disrupted_prob:
                state.status = "Normal"
            elif roll < config.recovery_from_disrupted_prob + config.escalation_prob:
                state.status = "Extended_Outage"
        elif state.status == "Extended_Outage":
            if roll < config.recovery_from_outage_prob:
                state.status = "Normal"

        if old_status == "Normal" and state.status == "Disrupted":
            if float(rng.supplier.random()) < config.alert_probability:
                world.alerts.append(f"Reports of supply chain issues at {state.name}")


def effective_reliability(supplier_name: str, world: WorldState, config: SupplierStateConfig) -> float:
    supplier_cfg = next((s for s in world.suppliers if s.name == supplier_name), None)
    base = supplier_cfg.reliability if supplier_cfg else 0.90

    state = next((s for s in world.supplier_states if s.name == supplier_name), None)
    if state is None:
        return base

    if state.status == "Disrupted":
        return base * config.disrupted_reliability_factor
    elif state.status == "Extended_Outage":
        return config.outage_reliability
    return base
