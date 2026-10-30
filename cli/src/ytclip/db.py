"""SQLite connection management and schema migrations.

WAL mode lets the native host (spawned by Chrome) insert jobs while ``ytclip run``
is updating progress. Writes use ``BEGIN IMMEDIATE`` so that read-modify-write
sequences (claiming a job, duplicate checks) are atomic across processes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 2

_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE media (
        id            INTEGER PRIMARY KEY,
        video_id      TEXT NOT NULL,
        variant       TEXT NOT NULL,
        path          TEXT NOT NULL,
        title         TEXT,
        duration_s    REAL,
        width         INTEGER,
        height        INTEGER,
        has_audio     INTEGER,
        size_bytes    INTEGER,
        created_at    TEXT NOT NULL,
        last_used_at  TEXT NOT NULL,
        UNIQUE (video_id, variant)
    );

    CREATE TABLE jobs (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        url           TEXT NOT NULL,
        video_id      TEXT NOT NULL,
        title         TEXT,
        start_ms      INTEGER NOT NULL CHECK (start_ms >= 0),
        end_ms        INTEGER NOT NULL CHECK (end_ms > start_ms),
        aspect_ratio  TEXT NOT NULL,
        crop_mode     TEXT NOT NULL,
        resolution    TEXT NOT NULL,
        output_name   TEXT,
        dedupe_key    TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'pending',
        progress      REAL NOT NULL DEFAULT 0,
        stage         TEXT,
        output_path   TEXT,
        error         TEXT,
        attempts      INTEGER NOT NULL DEFAULT 0,
        worker_pid    INTEGER,
        media_id      INTEGER REFERENCES media(id) ON DELETE SET NULL,
        metadata      TEXT NOT NULL DEFAULT '{}',
        created_at    TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        started_at    TEXT,
        completed_at  TEXT
    );
    CREATE INDEX idx_jobs_status ON jobs(status, id);
    CREATE INDEX idx_jobs_dedupe ON jobs(dedupe_key);
    CREATE INDEX idx_jobs_video ON jobs(video_id);

    CREATE TABLE job_events (
        id        INTEGER PRIMARY KEY,
        job_id    INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        at        TEXT NOT NULL,
        level     TEXT NOT NULL,
        stage     TEXT,
        message   TEXT NOT NULL,
        detail    TEXT
    );
    CREATE INDEX idx_events_job ON job_events(job_id, id);
    """,
    # Custom crop window as JSON {"x","y","width","height"} (fractions of the frame).
    2: """
    ALTER TABLE jobs ADD COLUMN crop_rect TEXT;
    """,
}


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, timeout=15, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._migrate()

    def _migrate(self) -> None:
        with self.transaction() as conn:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database {self.path} has schema v{current}; this ytclip supports v{SCHEMA_VERSION}."
                )
            for version in range(current + 1, SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS[version].split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute(f"PRAGMA user_version={version}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Exclusive-write transaction; rolled back on any exception."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self._conn.close()
