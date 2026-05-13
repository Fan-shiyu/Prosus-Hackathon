"""Server configuration from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

PORT: int = int(os.getenv("RESTBENCH_PORT", "8001"))

DATA_DIR: Path = Path(os.getenv("RESTBENCH_DATA_DIR", "./restbench_data"))

PERSIST: bool = os.getenv("RESTBENCH_PERSIST", "true").lower() in ("true", "1", "yes")

MAX_CONCURRENT_GAMES: int = int(os.getenv("RESTBENCH_MAX_CONCURRENT", "5"))

MAX_GAMES_PER_HOUR: int = int(os.getenv("RESTBENCH_MAX_GAMES_PER_HOUR", "60"))

ADMIN_TOKEN: str = os.getenv("RESTBENCH_ADMIN_TOKEN", "")

GAME_EXPIRY_SECONDS: int = int(os.getenv("RESTBENCH_GAME_EXPIRY", "7200"))

LOG_LEVEL: str = os.getenv("RESTBENCH_LOG_LEVEL", "INFO")

HIDDEN_UNLOCKED: bool = os.getenv("RESTBENCH_HIDDEN_UNLOCKED", "false").lower() in ("true", "1", "yes")
