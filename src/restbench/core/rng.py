"""Deterministic sub-RNG factory for simulation reproducibility.

Uses NumPy's SeedSequence.spawn() to create independent RNG streams per
subsystem. Changing one subsystem's draws never perturbs another's.
"""

from __future__ import annotations

import numpy as np


_SUBSYSTEM_NAMES = [
    "demand",
    "weather",
    "satisfaction",
    "supplier",
    "spoilage",
    "cohort",
    "party",
    "patience",
    "review",
    "events",
    "staff",
    "kitchen",
    "general",
]


class SimRNG:
    """Collection of independent RNG streams, one per engine subsystem."""

    def __init__(self, master_seed: int):
        self._master_seed = master_seed
        seq = np.random.SeedSequence(master_seed)
        children = seq.spawn(len(_SUBSYSTEM_NAMES))
        for name, child in zip(_SUBSYSTEM_NAMES, children):
            setattr(self, name, np.random.default_rng(child))

    demand: np.random.Generator
    weather: np.random.Generator
    satisfaction: np.random.Generator
    supplier: np.random.Generator
    spoilage: np.random.Generator
    cohort: np.random.Generator
    party: np.random.Generator
    patience: np.random.Generator
    review: np.random.Generator
    events: np.random.Generator
    staff: np.random.Generator
    kitchen: np.random.Generator
    general: np.random.Generator

    @property
    def master_seed(self) -> int:
        return self._master_seed
