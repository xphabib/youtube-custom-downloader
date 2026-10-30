"""Typed domain models shared by the CLI, the queue and the native messaging host."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_active(self) -> bool:
        return self in (JobStatus.DOWNLOADING, JobStatus.PROCESSING)

    @property
    def is_editable(self) -> bool:
        return self in (JobStatus.PENDING, JobStatus.FAILED)


class AspectRatio(StrEnum):
    PORTRAIT = "9:16"
    LANDSCAPE = "16:9"
    SQUARE = "1:1"

    @property
    def ratio(self) -> tuple[int, int]:
        w, h = self.value.split(":")
        return int(w), int(h)


class CropMode(StrEnum):
    SMART = "smart"
    CENTER = "center"
    BLUR = "blur"
    CUSTOM = "custom"


class Resolution(StrEnum):
    P1080 = "1080p"
    P720 = "720p"
    P480 = "480p"

    @property
    def short_side(self) -> int:
        return int(self.value.rstrip("p"))


@dataclass(frozen=True)
class CropRect:
    """A crop window chosen by the user, as fractions (0-1) of the source frame.

    Fractions keep it valid whatever resolution is downloaded. The renderer fits the
    output aspect ratio inside it.
    """

    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    def key(self) -> str:
        return ",".join(f"{v:.4f}" for v in (self.x, self.y, self.width, self.height))


@dataclass(frozen=True)
class ClipSpec:
    """A validated request to render one clip. Produced by ``validation.validate_clip``."""

    url: str
    video_id: str
    start_ms: int
    end_ms: int
    aspect_ratio: AspectRatio = AspectRatio.PORTRAIT
    crop_mode: CropMode = CropMode.SMART
    resolution: Resolution = Resolution.P1080
    title: str | None = None
    output_name: str | None = None
    crop_rect: CropRect | None = None  # only for CropMode.CUSTOM

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def dedupe_key(self) -> str:
        """Identity used to detect accidental duplicates (filename is not part of it)."""
        return "|".join(
            (
                self.video_id,
                str(self.start_ms),
                str(self.end_ms),
                self.aspect_ratio.value,
                self.crop_mode.value,
                self.resolution.value,
                *([self.crop_rect.key()] if self.crop_rect else []),
            )
        )


@dataclass
class Job:
    """A persisted queue entry."""

    id: int
    spec: ClipSpec
    status: JobStatus
    progress: float = 0.0
    stage: str | None = None
    output_path: str | None = None
    error: str | None = None
    attempts: int = 0
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Wire format used by the native host and ``--json`` CLI output."""
        s = self.spec
        return {
            "id": self.id,
            "url": s.url,
            "videoId": s.video_id,
            "title": s.title,
            "startMs": s.start_ms,
            "endMs": s.end_ms,
            "aspectRatio": s.aspect_ratio.value,
            "cropMode": s.crop_mode.value,
            "cropRect": s.crop_rect.to_dict() if s.crop_rect else None,
            "resolution": s.resolution.value,
            "outputName": s.output_name,
            "status": self.status.value,
            "progress": round(self.progress, 3),
            "stage": self.stage,
            "outputPath": self.output_path,
            "error": self.error,
            "attempts": self.attempts,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "completedAt": self.completed_at,
        }
