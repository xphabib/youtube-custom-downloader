"""Job and media persistence. All state transitions go through this module."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ytclip.db import Database, utcnow
from ytclip.models import AspectRatio, ClipSpec, CropMode, CropRect, Job, JobStatus, Resolution


class JobNotFoundError(LookupError):
    pass


class DuplicateJobError(Exception):
    def __init__(self, existing: Job) -> None:
        super().__init__(f"An identical clip is already queued as job #{existing.id}")
        self.existing = existing


class JobStateError(Exception):
    """The requested change is not allowed in the job's current status."""


@dataclass
class MediaRecord:
    id: int
    video_id: str
    variant: str
    path: str
    title: str | None
    duration_s: float | None
    size_bytes: int | None
    last_used_at: str


def _row_to_job(row: sqlite3.Row) -> Job:
    spec = ClipSpec(
        url=row["url"],
        video_id=row["video_id"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        aspect_ratio=AspectRatio(row["aspect_ratio"]),
        crop_mode=CropMode(row["crop_mode"]),
        resolution=Resolution(row["resolution"]),
        title=row["title"],
        output_name=row["output_name"],
        crop_rect=CropRect(**json.loads(row["crop_rect"])) if row["crop_rect"] else None,
    )
    return Job(
        id=row["id"],
        spec=spec,
        status=JobStatus(row["status"]),
        progress=row["progress"],
        stage=row["stage"],
        output_path=row["output_path"],
        error=row["error"],
        attempts=row["attempts"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        metadata=json.loads(row["metadata"] or "{}"),
    )


def _spec_columns(spec: ClipSpec) -> dict[str, Any]:
    return {
        "url": spec.url,
        "video_id": spec.video_id,
        "title": spec.title,
        "start_ms": spec.start_ms,
        "end_ms": spec.end_ms,
        "aspect_ratio": spec.aspect_ratio.value,
        "crop_mode": spec.crop_mode.value,
        "resolution": spec.resolution.value,
        "output_name": spec.output_name,
        "crop_rect": json.dumps(spec.crop_rect.to_dict()) if spec.crop_rect else None,
        "dedupe_key": spec.dedupe_key(),
    }


class JobRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---- reads -------------------------------------------------------------

    def get(self, job_id: int) -> Job:
        rows = self.db.query("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not rows:
            raise JobNotFoundError(f"Job #{job_id} does not exist")
        return _row_to_job(rows[0])

    def list(self, statuses: list[JobStatus] | None = None, limit: int | None = None) -> list[Job]:
        sql, params = "SELECT * FROM jobs", []
        if statuses:
            sql += f" WHERE status IN ({','.join('?' * len(statuses))})"
            params.extend(s.value for s in statuses)
        sql += " ORDER BY id DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [_row_to_job(r) for r in self.db.query(sql, tuple(params))]

    def pending_ids(self) -> list[int]:
        rows = self.db.query("SELECT id FROM jobs WHERE status = 'pending' ORDER BY id")
        return [r["id"] for r in rows]

    def counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in JobStatus}
        for row in self.db.query("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
            counts[row["status"]] = row["n"]
        return counts

    def events(self, job_id: int) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT at, level, stage, message, detail FROM job_events WHERE job_id = ? ORDER BY id",
            (job_id,),
        )
        return [dict(r) for r in rows]

    # ---- queue mutations ----------------------------------------------------

    def _find_duplicate(self, conn: sqlite3.Connection, spec: ClipSpec, exclude: int | None) -> Job | None:
        row = conn.execute(
            "SELECT * FROM jobs WHERE dedupe_key = ? AND id IS NOT ? ORDER BY id DESC LIMIT 1",
            (spec.dedupe_key(), exclude),
        ).fetchone()
        return _row_to_job(row) if row else None

    def add(self, spec: ClipSpec, allow_duplicate: bool = False) -> Job:
        with self.db.transaction() as conn:
            if not allow_duplicate and (dup := self._find_duplicate(conn, spec, None)):
                raise DuplicateJobError(dup)
            cols = _spec_columns(spec)
            now = utcnow()
            cols.update(status=JobStatus.PENDING.value, created_at=now, updated_at=now)
            cur = conn.execute(
                f"INSERT INTO jobs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                tuple(cols.values()),
            )
            job_id = cur.lastrowid
            self._event(conn, job_id, "info", "queued", "Job added to queue")
        return self.get(job_id)

    def update_spec(self, job_id: int, spec: ClipSpec, allow_duplicate: bool = False) -> Job:
        with self.db.transaction() as conn:
            job = self._get_in(conn, job_id)
            if not job.status.is_editable:
                raise JobStateError(f"Job #{job_id} is {job.status.value} and cannot be edited")
            if not allow_duplicate and (dup := self._find_duplicate(conn, spec, job_id)):
                raise DuplicateJobError(dup)
            cols = _spec_columns(spec)
            cols.update(status=JobStatus.PENDING.value, progress=0, stage=None, error=None,
                        attempts=0, updated_at=utcnow())
            conn.execute(
                f"UPDATE jobs SET {','.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                (*cols.values(), job_id),
            )
            self._event(conn, job_id, "info", "edited", "Clip settings changed")
        return self.get(job_id)

    def remove(self, job_id: int) -> Job:
        with self.db.transaction() as conn:
            job = self._get_in(conn, job_id)
            if job.status.is_active:
                raise JobStateError(f"Job #{job_id} is {job.status.value}; wait for it to finish")
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return job

    def clear(self) -> list[int]:
        """Delete every job that is not being processed right now. Output files are kept."""
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status NOT IN ('downloading', 'processing')"
            ).fetchall()
            ids = [r["id"] for r in rows]
            conn.executemany("DELETE FROM jobs WHERE id = ?", [(i,) for i in ids])
        return ids

    # ---- processing lifecycle ----------------------------------------------

    def claim(self, job_id: int, pid: int) -> bool:
        """Atomically move a pending job to downloading. False if someone else got it."""
        with self.db.transaction() as conn:
            now = utcnow()
            cur = conn.execute(
                "UPDATE jobs SET status = 'downloading', stage = 'starting', progress = 0,"
                " error = NULL, worker_pid = ?, attempts = attempts + 1, started_at = ?,"
                " updated_at = ? WHERE id = ? AND status = 'pending'",
                (pid, now, now, job_id),
            )
            if cur.rowcount:
                self._event(conn, job_id, "info", "starting", "Processing started")
            return bool(cur.rowcount)

    def set_stage(self, job_id: int, status: JobStatus, stage: str, progress: float = 0.0) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, stage = ?, progress = ?, updated_at = ? WHERE id = ?",
                (status.value, stage, progress, utcnow(), job_id),
            )
            self._event(conn, job_id, "info", stage, f"Stage: {stage}")

    def set_progress(self, job_id: int, progress: float) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE jobs SET progress = ?, updated_at = ? WHERE id = ?",
                (max(0.0, min(1.0, progress)), utcnow(), job_id),
            )

    def set_media(self, job_id: int, media_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE jobs SET media_id = ? WHERE id = ?", (media_id, job_id))

    def complete(self, job_id: int, output_path: str, metadata: dict[str, Any]) -> None:
        with self.db.transaction() as conn:
            now = utcnow()
            conn.execute(
                "UPDATE jobs SET status = 'completed', stage = 'done', progress = 1, error = NULL,"
                " output_path = ?, metadata = ?, worker_pid = NULL, completed_at = ?, updated_at = ?"
                " WHERE id = ?",
                (output_path, json.dumps(metadata), now, now, job_id),
            )
            self._event(conn, job_id, "info", "done", f"Saved {output_path}")

    def fail(self, job_id: int, error: str, detail: str | None = None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'failed', error = ?, worker_pid = NULL, updated_at = ?"
                " WHERE id = ?",
                (error, utcnow(), job_id),
            )
            self._event(conn, job_id, "error", None, error, detail)

    def retry_failed(self, max_attempts: int, job_ids: list[int] | None = None,
                     force: bool = False) -> list[int]:
        """Reset eligible failed jobs to pending. ``force`` ignores the attempt limit."""
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT id, attempts FROM jobs WHERE status = 'failed' ORDER BY id")
            eligible = [
                r["id"] for r in rows
                if (job_ids is None or r["id"] in job_ids) and (force or r["attempts"] < max_attempts)
            ]
            for job_id in eligible:
                conn.execute(
                    "UPDATE jobs SET status = 'pending', stage = NULL, progress = 0, updated_at = ?"
                    " WHERE id = ?",
                    (utcnow(), job_id),
                )
                self._event(conn, job_id, "info", "retry", "Queued for retry")
            return eligible

    def recover_interrupted(self) -> list[int]:
        """Return jobs stuck in an active state (crashed or killed runner) to pending.

        Only call while holding the runner lock, which guarantees no live worker owns them.
        """
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status IN ('downloading', 'processing')"
            ).fetchall()
            ids = [r["id"] for r in rows]
            for job_id in ids:
                conn.execute(
                    "UPDATE jobs SET status = 'pending', stage = NULL, progress = 0,"
                    " worker_pid = NULL, updated_at = ? WHERE id = ?",
                    (utcnow(), job_id),
                )
                self._event(conn, job_id, "warning", "recovered",
                            "Previous run was interrupted; job returned to the queue")
            return ids

    # ---- media cache --------------------------------------------------------

    def get_media(self, video_id: str, variant: str) -> MediaRecord | None:
        rows = self.db.query(
            "SELECT * FROM media WHERE video_id = ? AND variant = ?", (video_id, variant)
        )
        return self._row_to_media(rows[0]) if rows else None

    def upsert_media(self, video_id: str, variant: str, path: str, title: str | None,
                     duration_s: float | None, size_bytes: int | None) -> MediaRecord:
        with self.db.transaction() as conn:
            now = utcnow()
            conn.execute(
                "INSERT INTO media (video_id, variant, path, title, duration_s, size_bytes,"
                " created_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (video_id, variant) DO UPDATE SET path = excluded.path,"
                " title = COALESCE(excluded.title, media.title), duration_s = excluded.duration_s,"
                " size_bytes = excluded.size_bytes, last_used_at = excluded.last_used_at",
                (video_id, variant, path, title, duration_s, size_bytes, now, now),
            )
        media = self.get_media(video_id, variant)
        assert media is not None
        return media

    def touch_media(self, media_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE media SET last_used_at = ? WHERE id = ?", (utcnow(), media_id))

    def list_media(self) -> list[MediaRecord]:
        return [self._row_to_media(r) for r in self.db.query("SELECT * FROM media ORDER BY last_used_at")]

    def media_needed_by_pending(self) -> set[str]:
        rows = self.db.query(
            "SELECT DISTINCT video_id FROM jobs WHERE status IN ('pending','downloading','processing')"
            " OR (status = 'failed')"
        )
        return {r["video_id"] for r in rows}

    def delete_media(self, media_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM media WHERE id = ?", (media_id,))

    # ---- helpers ------------------------------------------------------------

    @staticmethod
    def _row_to_media(row: sqlite3.Row) -> MediaRecord:
        return MediaRecord(
            id=row["id"], video_id=row["video_id"], variant=row["variant"], path=row["path"],
            title=row["title"], duration_s=row["duration_s"], size_bytes=row["size_bytes"],
            last_used_at=row["last_used_at"],
        )

    @staticmethod
    def _get_in(conn: sqlite3.Connection, job_id: int) -> Job:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise JobNotFoundError(f"Job #{job_id} does not exist")
        return _row_to_job(row)

    @staticmethod
    def _event(conn: sqlite3.Connection, job_id: int, level: str, stage: str | None,
               message: str, detail: str | None = None) -> None:
        conn.execute(
            "INSERT INTO job_events (job_id, at, level, stage, message, detail) VALUES (?,?,?,?,?,?)",
            (job_id, utcnow(), level, stage, message, detail),
        )
