"""Process one job: fetch source, analyse, render, verify, publish.

The source provider is injectable so the full pipeline can be exercised in tests on
local synthetic footage without touching the network.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from ytclip.config import Config
from ytclip.downloader import DownloadError, MediaCache
from ytclip.filenames import UnsafePathError, confined_path, output_filename, unique_path
from ytclip.media.ffmpeg import FFmpegError
from ytclip.media.geometry import crop_size, output_size
from ytclip.media.probe import MediaInfo, ProbeError, probe
from ytclip.media.render import EncodeSettings, RenderRequest, render
from ytclip.media.verify import Expectation, VerificationError, verify_output
from ytclip.models import CropMode, Job, JobStatus
from ytclip.repository import JobRepository
from ytclip.smartcrop.detectors import Detector
from ytclip.smartcrop.planner import CropPlan, plan_smart_crop
from ytclip.timeutil import format_timestamp

log = logging.getLogger(__name__)

END_TOLERANCE_S = 0.5


class JobError(RuntimeError):
    """A failure with a message fit to show the user."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class SourceMedia:
    path: Path
    media_id: int | None
    title: str | None
    cached: bool


class SourceProvider(Protocol):
    def fetch(self, job: Job, on_progress: Callable[[float], None]) -> SourceMedia: ...


class YouTubeSource:
    def __init__(self, cache: MediaCache) -> None:
        self.cache = cache

    def fetch(self, job: Job, on_progress: Callable[[float], None]) -> SourceMedia:
        result = self.cache.ensure(job.spec.video_id, job.spec.title, on_progress)
        return SourceMedia(result.path, result.media.id, result.media.title, result.cached)


class Reporter(Protocol):
    def stage(self, job: Job, stage: str) -> None: ...
    def progress(self, job: Job, fraction: float) -> None: ...


class NullReporter:
    def stage(self, job: Job, stage: str) -> None:
        pass

    def progress(self, job: Job, fraction: float) -> None:
        pass


class _Progress:
    """Maps a stage's 0..1 progress into an overall range and throttles DB writes."""

    def __init__(self, repo: JobRepository, job: Job, reporter: Reporter, interval: float = 0.5) -> None:
        self.repo, self.job, self.reporter, self.interval = repo, job, reporter, interval
        self._last_write = 0.0

    def span(self, low: float, high: float) -> Callable[[float], None]:
        def update(fraction: float) -> None:
            overall = low + (high - low) * max(0.0, min(1.0, fraction))
            self.reporter.progress(self.job, overall)
            now = time.monotonic()
            if now - self._last_write >= self.interval:
                self._last_write = now
                self.repo.set_progress(self.job.id, overall)
        return update


def _check_free_space(path: Path, minimum_mb: int) -> None:
    free_mb = shutil.disk_usage(path).free // (1024 * 1024)
    if free_mb < minimum_mb:
        raise JobError(
            f"Only {free_mb} MB free in {path} (minimum {minimum_mb} MB). "
            "Free space or run `ytclip cache clean`."
        )


def _resolve_end(job: Job, info: MediaInfo) -> float:
    start_s, end_s = job.spec.start_ms / 1000, job.spec.end_ms / 1000
    if start_s >= info.duration_s:
        raise JobError(
            f"Clip starts at {format_timestamp(job.spec.start_ms)} but the video is only "
            f"{format_timestamp(int(info.duration_s * 1000))} long. Edit the clip times.",
            retryable=False,
        )
    if end_s > info.duration_s + END_TOLERANCE_S:
        raise JobError(
            f"Clip ends at {format_timestamp(job.spec.end_ms)} but the video is only "
            f"{format_timestamp(int(info.duration_s * 1000))} long. Edit the clip times.",
            retryable=False,
        )
    return min(end_s, info.duration_s)


class JobProcessor:
    def __init__(self, config: Config, repo: JobRepository, source: SourceProvider,
                 detector_factory: Callable[[], Detector], reporter: Reporter | None = None) -> None:
        self.config = config
        self.repo = repo
        self.source = source
        self.detector_factory = detector_factory
        self.reporter = reporter or NullReporter()
        self._detector: Detector | None = None

    @property
    def detector(self) -> Detector:
        if self._detector is None:
            self._detector = self.detector_factory()
        return self._detector

    def _stage(self, job: Job, status: JobStatus, stage: str, progress: float) -> None:
        self.repo.set_stage(job.id, status, stage, progress)
        self.reporter.stage(job, stage)

    def process(self, job: Job) -> Path:
        """Run the job to completion. Raises ``JobError`` with a user-facing message."""
        try:
            return self._process(job)
        except JobError:
            raise
        except DownloadError as exc:
            raise JobError(str(exc), exc.retryable) from exc
        except (FFmpegError, ProbeError, VerificationError, UnsafePathError) as exc:
            raise JobError(str(exc)) from exc
        except OSError as exc:
            raise JobError(f"File system error: {exc}") from exc

    def _process(self, job: Job) -> Path:
        cfg = self.config
        cfg.ensure_dirs()
        progress = _Progress(self.repo, job, self.reporter)
        _check_free_space(cfg.cache_dir, cfg.min_free_space_mb)

        self._stage(job, JobStatus.DOWNLOADING, "downloading", 0.0)
        source = self.source.fetch(job, progress.span(0.0, 0.35))
        if source.media_id is not None:
            self.repo.set_media(job.id, source.media_id)
        info = probe(source.path)
        end_s = _resolve_end(job, info)
        start_s = job.spec.start_ms / 1000
        duration_s = end_s - start_s

        work = cfg.work_dir / f"job-{job.id}"
        shutil.rmtree(work, ignore_errors=True)  # leftovers from an interrupted attempt
        work.mkdir(parents=True)
        _check_free_space(work, cfg.min_free_space_mb)

        mode, plan, metadata = self._plan(job, source, info, start_s, duration_s, progress)
        self._stage(job, JobStatus.PROCESSING, "rendering", 0.55)
        out_size = output_size(job.spec.aspect_ratio, job.spec.resolution)
        rendered = work / "render.mp4"
        render(
            RenderRequest(source.path, info, start_s, duration_s, job.spec.aspect_ratio, out_size,
                          rendered, mode, plan, work / "crop.cmd", job.spec.crop_rect),
            EncodeSettings(cfg.video_crf, cfg.video_preset, cfg.audio_bitrate),
            progress.span(0.55, 0.95),
        )

        self._stage(job, JobStatus.PROCESSING, "verifying", 0.96)
        verified = verify_output(rendered, Expectation(out_size, duration_s, info.has_audio))
        final = self._publish(job, rendered, source.title)
        metadata.update(
            output={"width": verified.width, "height": verified.height,
                    "duration_s": round(verified.duration_s, 3), "has_audio": verified.has_audio},
            source={"width": info.width, "height": info.height, "fps": round(info.fps, 3),
                    "cached": source.cached},
        )
        self.repo.complete(job.id, str(final), metadata)
        self.reporter.progress(job, 1.0)
        self.reporter.stage(job, "done")
        shutil.rmtree(work, ignore_errors=True)
        return final

    def _plan(self, job: Job, source: SourceMedia, info: MediaInfo, start_s: float,
              duration_s: float, progress: _Progress) -> tuple[CropMode, CropPlan | None, dict]:
        mode = job.spec.crop_mode
        metadata: dict = {"requested_mode": mode.value, "applied_mode": mode.value}
        if mode is not CropMode.SMART:
            return mode, None, metadata
        crop = crop_size(info.size, job.spec.aspect_ratio)
        self._stage(job, JobStatus.PROCESSING, "analysing", 0.35)
        plan = plan_smart_crop(source.path, info.size, crop, start_s, duration_s,
                               self.config.smart, self.detector, progress.span(0.35, 0.55))
        metadata["smart"] = plan.stats
        if plan.subject_found:
            return mode, plan, metadata
        fallback = CropMode(self.config.smart.fallback)
        log.info("Job %s: no subject detected, falling back to %s", job.id, fallback.value)
        metadata["applied_mode"] = fallback.value
        metadata["fallback_reason"] = "no subject detected"
        return fallback, None, metadata

    def _publish(self, job: Job, rendered: Path, fetched_title: str | None) -> Path:
        """Move the verified file into the output directory according to the overwrite policy."""
        spec = job.spec
        if fetched_title and not spec.title and not spec.output_name:
            spec = replace(spec, title=fetched_title)
        target = confined_path(self.config.output_dir, output_filename(spec))
        if target.exists():
            policy = self.config.overwrite
            if policy == "skip":
                raise JobError(f"{target} already exists (overwrite policy is 'skip').", retryable=False)
            if policy == "rename":
                target = unique_path(target)
        # Stage next to the target so the final rename is atomic even across filesystems.
        staging = target.with_name(f".{target.name}.partial")
        shutil.move(rendered, staging)
        os.replace(staging, target)
        return target
