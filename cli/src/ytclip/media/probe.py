"""FFprobe wrapper returning the stream facts the pipeline needs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ytclip.media.geometry import Size


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    fps: float
    duration_s: float
    has_audio: bool
    video_codec: str
    audio_codec: str | None

    @property
    def size(self) -> Size:
        return Size(self.width, self.height)


def _parse_rate(rate: str | None) -> float:
    try:
        value = float(Fraction(rate)) if rate and rate != "0/0" else 0.0
    except (ValueError, ZeroDivisionError):
        value = 0.0
    return value


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list", []):
        if "rotation" in side:
            return int(side["rotation"])
    return int(stream.get("tags", {}).get("rotate", 0))


def probe(path: Path, ffprobe: str = "ffprobe") -> MediaInfo:
    cmd = [ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except FileNotFoundError:
        raise ProbeError("ffprobe was not found. Install it with: sudo apt install ffmpeg") from None
    if result.returncode != 0:
        raise ProbeError(f"ffprobe could not read {path.name}: {result.stderr.strip()[-300:]}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise ProbeError(f"{path.name} contains no video stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width, height = int(video["width"]), int(video["height"])
    if abs(_rotation(video)) % 180 == 90:
        width, height = height, width  # ffmpeg auto-rotates on decode
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate")) or 30.0
    return MediaInfo(
        width=width,
        height=height,
        fps=fps,
        duration_s=duration,
        has_audio=audio is not None,
        video_codec=video.get("codec_name", "unknown"),
        audio_codec=audio.get("codec_name") if audio else None,
    )
