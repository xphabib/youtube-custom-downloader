"""Queue runner: single-instance lock, crash recovery and per-job error handling."""

from __future__ import annotations

import fcntl
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ytclip.models import Job, JobStatus
from ytclip.pipeline import JobError, JobProcessor
from ytclip.repository import JobRepository

log = logging.getLogger(__name__)


class RunnerBusyError(RuntimeError):
    pass


@contextmanager
def run_lock(path: Path) -> Iterator[None]:
    """Exclusive advisory lock released automatically if the process dies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunnerBusyError("Another `ytclip run` is already processing the queue.") from None
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def lock_is_held(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


@dataclass
class RunSummary:
    completed: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    recovered: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)


class Runner:
    def __init__(self, repo: JobRepository, processor: JobProcessor, lock_path: Path) -> None:
        self.repo = repo
        self.processor = processor
        self.lock_path = lock_path

    def run(self, job_ids: list[int] | None = None) -> RunSummary:
        """Process the given jobs, or every pending job (including ones added meanwhile)."""
        summary = RunSummary()
        with run_lock(self.lock_path):
            summary.recovered = self.repo.recover_interrupted()
            if job_ids is not None:
                for job_id in job_ids:
                    self._run_one(self.repo.get(job_id), summary)
                return summary
            seen: set[int] = set()
            while queue := [i for i in self.repo.pending_ids() if i not in seen]:
                for job_id in queue:
                    seen.add(job_id)
                    self._run_one(self.repo.get(job_id), summary)
        return summary

    def _run_one(self, job: Job, summary: RunSummary) -> None:
        if job.status is not JobStatus.PENDING or not self.repo.claim(job.id, os.getpid()):
            summary.skipped.append(job.id)
            return
        job = self.repo.get(job.id)
        try:
            self.processor.process(job)
        except JobError as exc:
            log.warning("Job %s failed: %s", job.id, exc)
            self.repo.fail(job.id, str(exc))
            summary.failed.append(job.id)
        except KeyboardInterrupt:
            # Leave the queue consistent: the job goes back to pending for the next run.
            self.repo.recover_interrupted()
            raise
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the queue
            log.exception("Job %s crashed", job.id)
            self.repo.fail(job.id, f"Unexpected error: {exc}. Run with --debug and see the log.",
                           detail=repr(exc))
            summary.failed.append(job.id)
        else:
            summary.completed.append(job.id)
