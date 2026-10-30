"""SQLite queue persistence, duplicates, state transitions and recovery."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from ytclip.db import _MIGRATIONS, Database
from ytclip.models import JobStatus
from ytclip.repository import DuplicateJobError, JobNotFoundError, JobRepository, JobStateError


def test_create_get_list_and_counts(repo: JobRepository, make_spec) -> None:
    job = repo.add(make_spec(title="First"))
    assert job.id == 1 and job.status is JobStatus.PENDING and job.attempts == 0
    assert job.created_at and job.updated_at
    repo.add(make_spec(start="5", end="9"))
    assert [j.id for j in repo.list()] == [2, 1]
    assert repo.get(1).spec.title == "First"
    assert repo.counts()["pending"] == 2
    assert repo.pending_ids() == [1, 2]
    with pytest.raises(JobNotFoundError):
        repo.get(99)


def test_jobs_persist_across_connections(config, make_spec) -> None:
    first = Database(config.db_path)
    JobRepository(first).add(make_spec())
    first.close()
    second = Database(config.db_path)
    jobs = JobRepository(second).list()
    second.close()
    assert len(jobs) == 1 and jobs[0].spec.start_ms == 1000


def test_custom_crop_rect_roundtrips(repo: JobRepository, make_spec) -> None:
    rect = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
    job = repo.add(make_spec(cropMode="custom", cropRect=rect))
    assert repo.get(job.id).to_dict()["cropRect"] == rect
    assert repo.add(make_spec()).to_dict()["cropRect"] is None


def test_schema_v1_database_is_migrated(config, make_spec) -> None:
    conn = sqlite3.connect(config.db_path)
    conn.executescript(_MIGRATIONS[1])
    conn.execute("PRAGMA user_version=1")
    conn.close()
    db = Database(config.db_path)
    assert JobRepository(db).add(make_spec(cropMode="custom", cropRect={"x": 0, "y": 0, "width": 1, "height": 1}))
    db.close()


def test_duplicates_are_rejected_unless_allowed(repo: JobRepository, make_spec) -> None:
    original = repo.add(make_spec())
    with pytest.raises(DuplicateJobError) as info:
        repo.add(make_spec(outputName="different name, same clip"))
    assert info.value.existing.id == original.id
    assert repo.add(make_spec(), allow_duplicate=True).id == 2
    assert repo.add(make_spec(cropMode="blur")).id == 3  # different settings are not duplicates


def test_update_spec_only_when_editable(repo: JobRepository, make_spec) -> None:
    job = repo.add(make_spec())
    updated = repo.update_spec(job.id, make_spec(end="00:00:05", aspectRatio="1:1"))
    assert updated.spec.end_ms == 5000 and updated.spec.aspect_ratio.value == "1:1"
    other = repo.add(make_spec(start="10", end="12"))
    with pytest.raises(DuplicateJobError):
        repo.update_spec(other.id, make_spec(end="00:00:05", aspectRatio="1:1"))
    assert repo.claim(other.id, pid=1)
    with pytest.raises(JobStateError):
        repo.update_spec(other.id, make_spec(start="20", end="22"))


def test_failed_job_edit_resets_to_pending(repo: JobRepository, make_spec) -> None:
    job = repo.add(make_spec())
    repo.claim(job.id, 1)
    repo.fail(job.id, "boom")
    edited = repo.update_spec(job.id, make_spec(end="00:00:04"))
    assert edited.status is JobStatus.PENDING and edited.error is None and edited.attempts == 0


def test_remove_refuses_active_jobs(repo: JobRepository, make_spec) -> None:
    job = repo.add(make_spec())
    repo.claim(job.id, 1)
    with pytest.raises(JobStateError):
        repo.remove(job.id)
    repo.complete(job.id, "/out/x.mp4", {})
    assert repo.remove(job.id).id == job.id
    assert repo.list() == []


def test_claim_is_atomic(repo: JobRepository, make_spec) -> None:
    job = repo.add(make_spec())
    assert repo.claim(job.id, 1) is True
    assert repo.claim(job.id, 2) is False
    claimed = repo.get(job.id)
    assert claimed.status is JobStatus.DOWNLOADING and claimed.attempts == 1


def test_concurrent_claims_from_separate_connections(config, make_spec) -> None:
    JobRepository(Database(config.db_path)).add(make_spec())
    results: list[bool] = []

    def worker(pid: int) -> None:
        db = Database(config.db_path)
        results.append(JobRepository(db).claim(1, pid))
        db.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1


def test_progress_complete_and_events(repo: JobRepository, make_spec) -> None:
    job = repo.add(make_spec())
    repo.claim(job.id, 1)
    repo.set_stage(job.id, JobStatus.PROCESSING, "rendering", 0.5)
    repo.set_progress(job.id, 1.7)  # clamped
    assert repo.get(job.id).progress == 1.0
    repo.complete(job.id, "/out/a.mp4", {"applied_mode": "smart"})
    done = repo.get(job.id)
    assert done.status is JobStatus.COMPLETED and done.completed_at and done.metadata == {"applied_mode": "smart"}
    messages = [e["message"] for e in repo.events(job.id)]
    assert messages[0] == "Job added to queue" and "Saved /out/a.mp4" in messages[-1]


def test_retry_respects_max_attempts(repo: JobRepository, make_spec) -> None:
    job = repo.add(make_spec())
    for _ in range(3):
        repo.claim(job.id, 1)
        repo.fail(job.id, "network")
        repo.retry_failed(max_attempts=3)
    assert repo.get(job.id).status is JobStatus.FAILED  # third failure: attempts exhausted
    assert repo.retry_failed(max_attempts=3) == []
    assert repo.retry_failed(max_attempts=3, force=True) == [job.id]
    assert repo.get(job.id).status is JobStatus.PENDING


def test_retry_selected_ids_only(repo: JobRepository, make_spec) -> None:
    a, b = repo.add(make_spec()), repo.add(make_spec(start="7", end="9"))
    for job in (a, b):
        repo.claim(job.id, 1)
        repo.fail(job.id, "x")
    assert repo.retry_failed(3, [b.id]) == [b.id]
    assert repo.get(a.id).status is JobStatus.FAILED


def test_recover_interrupted_jobs(repo: JobRepository, make_spec) -> None:
    job = repo.add(make_spec())
    repo.claim(job.id, 4242)
    repo.set_stage(job.id, JobStatus.PROCESSING, "rendering", 0.7)
    assert repo.recover_interrupted() == [job.id]
    recovered = repo.get(job.id)
    assert recovered.status is JobStatus.PENDING and recovered.progress == 0
    assert repo.events(job.id)[-1]["level"] == "warning"


def test_media_cache_records(repo: JobRepository, make_spec) -> None:
    media = repo.upsert_media("dQw4w9WgXcQ", "h1080", "/c/a.mp4", "T", 12.0, 100)
    again = repo.upsert_media("dQw4w9WgXcQ", "h1080", "/c/b.mp4", None, 12.0, 120)
    assert again.id == media.id and again.path == "/c/b.mp4" and again.title == "T"
    repo.add(make_spec())
    assert repo.media_needed_by_pending() == {"dQw4w9WgXcQ"}


def test_schema_constraints(db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO jobs (url, video_id, start_ms, end_ms, aspect_ratio, crop_mode, resolution,"
                " dedupe_key, created_at, updated_at) VALUES ('u','v',10,5,'9:16','smart','1080p','k','t','t')"
            )
    indexes = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_jobs_status", "idx_jobs_dedupe", "idx_events_job"} <= indexes
    assert db.query("PRAGMA journal_mode")[0][0] == "wal"
