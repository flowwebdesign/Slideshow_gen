from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.models import JobRecord, JobState, SlideshowSettings, now_utc


ALLOWED_TRANSITIONS: dict[JobState, set[JobState]] = {
    JobState.UPLOADING: {JobState.QUEUED, JobState.FAILED, JobState.EXPIRED},
    JobState.QUEUED: {JobState.PREPARING, JobState.FAILED, JobState.EXPIRED},
    JobState.PREPARING: {JobState.RENDERING, JobState.FAILED, JobState.EXPIRED},
    JobState.RENDERING: {JobState.READY, JobState.FAILED, JobState.EXPIRED},
    JobState.READY: {JobState.DOWNLOADED, JobState.EXPIRED},
    JobState.DOWNLOADED: {JobState.EXPIRED},
    JobState.FAILED: {JobState.EXPIRED},
    JobState.EXPIRED: set(),
}


class JobRepository:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def initialise(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, state TEXT NOT NULL,
                    progress INTEGER NOT NULL, settings TEXT NOT NULL, photo_count INTEGER NOT NULL,
                    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    completed_at TEXT, downloaded_at TEXT
                )"""
            )

    def create(
        self, job_id: str, token_hash: str, settings: SlideshowSettings, photo_count: int,
        *, initial_state: JobState = JobState.QUEUED,
    ) -> JobRecord:
        now = now_utc().isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL)",
                (
                    job_id, token_hash, initial_state,
                    0 if initial_state == JobState.UPLOADING else 5,
                    settings.model_dump_json(), photo_count, now, now,
                ),
            )
        record = self.get(job_id)
        assert record is not None
        return record

    def get(self, job_id: str) -> JobRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return JobRecord.from_row(row) if row else None

    def list_states(self, states: set[JobState]) -> list[JobRecord]:
        if not states:
            return []
        placeholders = ",".join("?" for _ in states)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs WHERE state IN ({placeholders})", tuple(states)
            ).fetchall()
        return [JobRecord.from_row(row) for row in rows]

    def transition(self, job_id: str, target: JobState, progress: int, error: str | None = None) -> JobRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        if target not in ALLOWED_TRANSITIONS[current.state]:
            raise ValueError(f"invalid state transition: {current.state} -> {target}")
        now = now_utc().isoformat()
        completed = now if target == JobState.READY else current.completed_at.isoformat() if current.completed_at else None
        downloaded = now if target == JobState.DOWNLOADED else current.downloaded_at.isoformat() if current.downloaded_at else None
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs SET state=?, progress=?, error=?, updated_at=?, completed_at=?, downloaded_at=?
                   WHERE id=? AND state=?""",
                (target, progress, error, now, completed, downloaded, job_id, current.state),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("job changed concurrently")
        updated = self.get(job_id)
        assert updated is not None
        return updated

    def set_progress(self, job_id: str, progress: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET progress=?, updated_at=? WHERE id=?",
                (max(0, min(100, progress)), now_utc().isoformat(), job_id),
            )

    def delete(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    def set_times_for_test(
        self, job_id: str, *, updated_at: datetime | None = None,
        completed_at: datetime | None = None, downloaded_at: datetime | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[str] = []
        for name, value in (("updated_at", updated_at), ("completed_at", completed_at), ("downloaded_at", downloaded_at)):
            if value is not None:
                fields.append(f"{name}=?")
                values.append(value.isoformat())
        if fields:
            with self._connect() as connection:
                connection.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id=?", (*values, job_id))
