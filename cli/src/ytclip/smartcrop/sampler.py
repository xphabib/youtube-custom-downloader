"""Stream down-scaled frames from a clip interval through an ffmpeg pipe.

One ffmpeg process decodes the interval once and emits frames at ``fps``; frames are
yielded one at a time so memory stays bounded regardless of clip length.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ytclip.media.geometry import Size, even


@dataclass(frozen=True)
class SamplePlan:
    fps: float
    size: Size

    @classmethod
    def for_clip(cls, source: Size, duration_s: float, sample_fps: float,
                 max_samples: int, analysis_width: int) -> SamplePlan:
        fps = min(sample_fps, max_samples / max(duration_s, 0.001))
        width = even(min(analysis_width, source.width))
        return cls(fps=fps, size=Size(width, even(width / source.aspect)))


def iter_frames(path: Path, start_s: float, duration_s: float, plan: SamplePlan,
                ffmpeg: str = "ffmpeg") -> Iterator[tuple[float, np.ndarray]]:
    """Yield ``(time_in_clip_s, bgr_frame)`` pairs."""
    w, h = plan.size.width, plan.size.height
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-ss", f"{start_s:.3f}", "-i", str(path), "-t", f"{duration_s:.3f}",
        "-an", "-sn",
        "-vf", f"fps={plan.fps:.6f},scale={w}:{h}:flags=area",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
    ]
    frame_bytes = w * h * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    try:
        index = 0
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield index / plan.fps, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            index += 1
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()
