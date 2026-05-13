"""SQLite persistence for leaderboard scores and game transcripts."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class LeaderboardDB:
    """Write-through SQLite store for leaderboard entries.

    Thread-safe via a lock (SQLite in WAL mode allows concurrent reads,
    but we keep it simple for a hackathon server).
    """

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS leaderboard (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name TEXT NOT NULL,
                scenario TEXT NOT NULL,
                seed INTEGER NOT NULL,
                score REAL NOT NULL,
                days_survived INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                UNIQUE(team_name, scenario, seed)
            )
        """)
        self._conn.commit()

    def upsert(
        self,
        team_name: str,
        scenario: str,
        seed: int,
        score: float,
        days_survived: int,
        timestamp: float,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO leaderboard (team_name, scenario, seed, score, days_survived, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(team_name, scenario, seed) DO UPDATE SET "
                "score=excluded.score, days_survived=excluded.days_survived, timestamp=excluded.timestamp",
                (team_name, scenario, seed, score, days_survived, timestamp),
            )
            self._conn.commit()

    def all_entries(self, scenario: str | None = None) -> list[dict]:
        with self._lock:
            if scenario:
                rows = self._conn.execute(
                    "SELECT team_name, scenario, seed, score, days_survived, timestamp "
                    "FROM leaderboard WHERE scenario = ? ORDER BY score DESC",
                    (scenario,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT team_name, scenario, seed, score, days_survived, timestamp "
                    "FROM leaderboard ORDER BY score DESC",
                ).fetchall()
        return [
            {
                "team_name": r[0],
                "scenario": r[1],
                "seed": r[2],
                "score": r[3],
                "days_survived": r[4],
                "timestamp": r[5],
            }
            for r in rows
        ]

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM leaderboard")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def save_transcript(transcript: list[dict], game_id: str, data_dir: Path) -> Path:
    """Write a game transcript to a JSONL file."""
    path = data_dir / "transcripts" / f"{game_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for entry in transcript:
            f.write(json.dumps(entry, default=str) + "\n")
    return path
