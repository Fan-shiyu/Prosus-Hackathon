"""Team module for RestBench Ultimate Agent."""

from agents.team.agent import strategy, main
from agents.team.conflict_resolver import resolve_conflicts
from agents.team.forecast import predict_demand
from agents.team.helper import handle_helper_tasks, save_tracking_notes
from agents.team.inventory import manage_inventory
from agents.team.satisfaction import track_satisfaction

__all__ = [
    "strategy",
    "main",
    "resolve_conflicts",
    "predict_demand",
    "handle_helper_tasks",
    "save_tracking_notes",
    "manage_inventory",
    "track_satisfaction",
]
