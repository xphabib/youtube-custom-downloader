"""Full job processing on synthetic footage: runner, fallback, policies, recovery."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import LocalSource, requires_ffmpeg
from ytclip.config import Config
from ytclip.media.probe import probe
from ytclip.models import JobStatus
from ytclip.pipeline import JobError, JobProcessor
from ytclip.repository import JobRepository
from ytclip.runner import Runner, RunnerBusyError, lock_is_held, run_lock

pytestmark = [pytest.mark.media, requires_ffmpeg]


def _runner(config: Config, repo: JobRepository, source: LocalSource, detector) -> Runner:
    processor = JobProcessor(config, repo, source, detector)
    return Runner(repo, processor, config.data_dir / "run.lock")


def test_run_processes_all_pending_jobs(config, repo, make_spec, moving_box, no_detector) -> None:
    repo.add(make_spec(title="Clip A"))
    repo.add(make_spec(start="0.5", end="2.5", aspectRatio="1:1", cropMode="blur", outputName="square"))
    repo.add(make_spec(start="1", end="2", aspectRatio="16:9", cropMode="center", resolution="720p"))
    source = LocalSource(moving_box)
    summary = _runner(config, repo, source, no_detector).run()

    assert summary.completed == [1, 2, 3] and summary.failed == []
    expected = {1: (480, 854, 2.0), 2: (480, 480, 2.0), 3: (1280, 720, 1.0)}
    for job_id, (w, h, duration) in expected.items():
        job = repo.get(job_id)
        assert job.status is JobStatus.COMPLETED and job.progress == 1.0
        out = Path(job.output_path)
        assert out.parent == config.output_dir.resolve()
        info = probe(out)
        assert (info.width, info.height) == (w, h) and info.has_audio
        assert info.duration_s == pytest.approx(duration, abs=0.15)
    assert Path(repo.get(2).output_path).name == "square.mp4"
    assert Path(repo.get(1).output_path).name.startswith("Clip_A_")
    assert repo.get(1).metadata["smart"]["detected_ratio"] > 0.5
    assert not any(config.work_dir.iterdir())  # intermediates cleaned up
    assert not list(config.output_dir.glob(".*partial"))


def test_smart_crop_falls_back_when_nothing_is_detected(config, repo, make_spec, static_video, no_detector) -> None:
    repo.add(make_spec(start="0", end="2"))
    config.smart.fallback = "blur"
    _runner(config, repo, LocalSource(static_video), no_detector).run()
    job = repo.get(1)
    assert job.status is JobStatus.COMPLETED
    assert job.metadata["applied_mode"] == "blur" and job.metadata["fallback_reason"] == "no subject detected"
    assert job.metadata["smart"]["detected_ratio"] == 0


def test_clip_beyond_video_end_fails_without_retry(config, repo, make_spec, moving_box, no_detector) -> None:
    repo.add(make_spec(start="00:00:02", end="00:00:30"))
    summary = _runner(config, repo, LocalSource(moving_box), no_detector).run()
    job = repo.get(1)
    assert summary.failed == [1] and job.status is JobStatus.FAILED
    assert "only 00:00:04 long" in job.error and "Edit the clip" in job.error


def test_end_slightly_past_duration_is_clamped(config, repo, make_spec, moving_box, no_detector) -> None:
    repo.add(make_spec(start="00:00:03", end="00:00:04.300", cropMode="center"))
    _runner(config, repo, LocalSource(moving_box), no_detector).run()
    job = repo.get(1)
    assert job.status is JobStatus.COMPLETED, job.error
    assert job.metadata["output"]["duration_s"] == pytest.approx(1.0, abs=0.15)


@pytest.mark.parametrize("policy", ["rename", "skip", "overwrite"])
def test_overwrite_policies(config, repo, make_spec, moving_box, no_detector, policy: str) -> None:
    config.overwrite = policy
    existing = config.output_dir / "same.mp4"
    existing.write_bytes(b"precious")
    repo.add(make_spec(cropMode="center", outputName="same"))
    _runner(config, repo, LocalSource(moving_box), no_detector).run()
    job = repo.get(1)
    if policy == "rename":
        assert job.status is JobStatus.COMPLETED and job.output_path.endswith("same-1.mp4")
        assert existing.read_bytes() == b"precious"
    elif policy == "skip":
        assert job.status is JobStatus.FAILED and "already exists" in job.error
        assert existing.read_bytes() == b"precious"
    else:
        assert job.status is JobStatus.COMPLETED and Path(job.output_path) == existing.resolve()
        assert existing.stat().st_size > 1000


def test_interrupted_job_recovers_on_next_run(config, repo, make_spec, moving_box, no_detector) -> None:
    job = repo.add(make_spec(cropMode="center"))
    # Simulate a runner killed mid-render: job left active, partial work files on disk.
    repo.claim(job.id, pid=999_999)
    repo.set_stage(job.id, JobStatus.PROCESSING, "rendering", 0.6)
    stale = config.work_dir / f"job-{job.id}"
    stale.mkdir(parents=True)
    (stale / "render.mp4").write_bytes(b"half a file")

    summary = _runner(config, repo, LocalSource(moving_box), no_detector).run()
    assert summary.recovered == [job.id] and summary.completed == [job.id]
    done = repo.get(job.id)
    assert done.status is JobStatus.COMPLETED and done.attempts == 2
    assert probe(Path(done.output_path)).width == 480
    assert any(e["stage"] == "recovered" for e in repo.events(job.id))


def test_keyboard_interrupt_returns_job_to_queue(config, repo, make_spec, moving_box, no_detector) -> None:
    repo.add(make_spec(cropMode="center"))

    class Interrupting(LocalSource):
        def fetch(self, job, on_progress):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _runner(config, repo, Interrupting(moving_box), no_detector).run()
    assert repo.get(1).status is JobStatus.PENDING
    assert not lock_is_held(config.data_dir / "run.lock")


def test_unexpected_errors_fail_only_that_job(config, repo, make_spec, moving_box, no_detector) -> None:
    repo.add(make_spec(cropMode="center"))
    repo.add(make_spec(cropMode="center", start="2", end="3"))

    class FlakySource(LocalSource):
        def fetch(self, job, on_progress):
            if job.id == 1:
                raise ValueError("kaboom")
            return super().fetch(job, on_progress)

    summary = _runner(config, repo, FlakySource(moving_box), no_detector).run()
    assert summary.failed == [1] and summary.completed == [2]
    assert "Unexpected error: kaboom" in repo.get(1).error


def test_run_specific_job_only(config, repo, make_spec, moving_box, no_detector) -> None:
    repo.add(make_spec(cropMode="center"))
    repo.add(make_spec(cropMode="center", start="2", end="3"))
    summary = _runner(config, repo, LocalSource(moving_box), no_detector).run([2])
    assert summary.completed == [2] and repo.get(1).status is JobStatus.PENDING


def test_completed_jobs_are_not_reprocessed(config, repo, make_spec, moving_box, no_detector) -> None:
    repo.add(make_spec(cropMode="center"))
    runner = _runner(config, repo, LocalSource(moving_box), no_detector)
    runner.run()
    first_output = repo.get(1).output_path
    summary = runner.run([1])
    assert summary.skipped == [1] and repo.get(1).output_path == first_output
    assert len(list(config.output_dir.glob("*.mp4"))) == 1


def test_only_one_runner_at_a_time(config) -> None:
    lock = config.data_dir / "run.lock"
    with run_lock(lock):
        assert lock_is_held(lock)
        with pytest.raises(RunnerBusyError):
            with run_lock(lock):
                pass
    assert not lock_is_held(lock)


def test_low_disk_space_is_reported(config, repo, make_spec, moving_box, no_detector) -> None:
    config.min_free_space_mb = 10**12
    repo.add(make_spec())
    _runner(config, repo, LocalSource(moving_box), no_detector).run()
    assert "free" in repo.get(1).error and "ytclip cache clean" in repo.get(1).error


def test_processor_wraps_errors_in_job_error(config, repo, make_spec, tmp_path, no_detector) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"garbage")
    job = repo.add(make_spec())
    repo.claim(job.id, os.getpid())
    with pytest.raises(JobError, match="ffprobe could not read"):
        JobProcessor(config, repo, LocalSource(bad), no_detector).process(repo.get(job.id))
