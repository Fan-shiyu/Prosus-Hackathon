"""Load scenario definitions from YAML, resolve events, apply tuning overrides."""

from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from restbench.engine.tuning import TuningConfig
from restbench.scenarios.types import (
    EventDefinition,
    EventSchedule,
    ResolvedEvent,
    ScenarioDefinition,
)
import restbench

_SCENARIOS_DIR = restbench.data_dir() / "scenarios"

_BASELINE = ScenarioDefinition(name="baseline", display_name="Baseline", description="No events. The default scenario.")


def load_scenario(name: str) -> ScenarioDefinition:
    if name == "baseline":
        return _BASELINE

    for subdir in ("known", "hidden"):
        path = _SCENARIOS_DIR / subdir / f"{name}.yaml"
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f)
            return ScenarioDefinition(**data)

    available = [s["name"] for s in list_scenarios(include_hidden=False)]
    raise ValueError(f"Unknown scenario '{name}'. Available: {available}")


def list_scenarios(include_hidden: bool = False) -> list[dict]:
    results = [{"name": "baseline", "display_name": "Baseline", "description": "No events. The default scenario.", "difficulty": "easy"}]

    for subdir in ("known", "hidden"):
        if subdir == "hidden" and not include_hidden:
            continue
        dir_path = _SCENARIOS_DIR / subdir
        if not dir_path.exists():
            continue
        for path in sorted(dir_path.glob("*.yaml")):
            if path.stem == "baseline":
                continue
            with open(path) as f:
                data = yaml.safe_load(f)
            results.append({
                "name": data.get("name", path.stem),
                "display_name": data.get("display_name", path.stem),
                "description": data.get("description", ""),
                "difficulty": data.get("difficulty", "medium"),
            })

    return results


def resolve_events(events: list[EventDefinition], rng: np.random.Generator) -> EventSchedule:
    schedule: EventSchedule = {}

    for event in events:
        if event.probability < 1.0:
            if float(rng.random()) >= event.probability:
                continue

        if event.day is not None:
            day = event.day
        elif event.day_range is not None:
            low, high = event.day_range
            day = int(rng.integers(low, high + 1))
        else:
            continue

        resolved = ResolvedEvent(type=event.type, day=day, params=event.params, alert=event.alert)
        schedule.setdefault(day, []).append(resolved)

        duration = event.duration_days or event.params.get("duration_days")
        if duration is not None and duration > 0:
            restore_day = day + duration
            restore_type = _restore_type_for(event.type)
            if restore_type:
                restore = ResolvedEvent(type=restore_type, day=restore_day, params=event.params, alert=None)
                schedule.setdefault(restore_day, []).append(restore)

    return schedule


def _restore_type_for(event_type: str) -> str | None:
    restore_map = {
        "demand_surge": "demand_restore",
        "demand_drop": "demand_restore",
        "equipment_failure": "equipment_restore",
        "weather_lock": "weather_unlock",
        "satisfaction_modifier": "satisfaction_restore",
        "cost_increase": "cost_restore",
    }
    return restore_map.get(event_type)


def apply_tuning_overrides(tuning: TuningConfig, overrides: dict[str, Any]) -> TuningConfig:
    if not overrides:
        return tuning

    grouped: dict[str, dict[str, Any]] = {}
    for dotted_path, value in overrides.items():
        parts = dotted_path.split(".", 1)
        if len(parts) == 1:
            grouped.setdefault("_root", {})[parts[0]] = value
        else:
            grouped.setdefault(parts[0], {})[parts[1]] = value

    kwargs: dict[str, Any] = {}
    for field_name, sub_overrides in grouped.items():
        if field_name == "_root":
            kwargs.update(sub_overrides)
            continue

        sub_config = getattr(tuning, field_name)
        for sub_path, value in sub_overrides.items():
            if "." in sub_path:
                attr_name, dict_key = sub_path.split(".", 1)
                current_dict = dict(getattr(sub_config, attr_name))
                current_dict[dict_key] = value
                sub_config = replace(sub_config, **{attr_name: current_dict})
            else:
                sub_config = replace(sub_config, **{sub_path: value})
        kwargs[field_name] = sub_config

    return replace(tuning, **kwargs)
