"""Pydantic models for scenario definitions and resolved events."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class EventDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    day: int | None = None
    day_range: list[int] | None = None
    probability: float = 1.0
    duration_days: int | None = None
    params: dict[str, Any] = {}
    alert: str | None = None


class ScenarioDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    display_name: str = ""
    description: str = ""
    difficulty: str = "medium"
    hidden: bool = False
    tuning_overrides: dict[str, Any] = {}
    events: list[EventDefinition] = []


class ResolvedEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    day: int
    params: dict[str, Any] = {}
    alert: str | None = None


EventSchedule = dict[int, list[ResolvedEvent]]
