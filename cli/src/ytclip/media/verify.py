"""Post-render verification. A job is only marked completed after this passes."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ytclip.media.geometry import Size
from ytclip.media.probe import MediaInfo, ProbeError, probe


class VerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Expectation:
    size: Size
    duration_s: float
    has_audio: bool


def duration_tolerance(duration_s: float, fps: float) -> float:
    return max(0.25, 3 / max(fps, 1), duration_s * 0.01)


def _decode_tail(path: Path, duration_s: float) -> None:
    """Decode the last seconds to prove the file is complete and playable."""
    start = max(0.0, duration_s - 3)
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{start:.3f}",
         "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, timeout=300, check=False,
    )
    if result.returncode != 0 or result.stderr.strip():
        raise VerificationError(f"Output does not decode cleanly: {result.stderr.strip()[-200:]}")


def verify_output(path: Path, expected: Expectation) -> MediaInfo:
    if not path.is_file() or path.stat().st_size == 0:
        raise VerificationError(f"Output file {path.name} is missing or empty")
    try:
        info = probe(path)
    except ProbeError as exc:
        raise VerificationError(str(exc)) from exc
    if info.size != expected.size:
        raise VerificationError(
            f"Output is {info.width}x{info.height}, expected {expected.size.width}x{expected.size.height}"
        )
    tolerance = duration_tolerance(expected.duration_s, info.fps)
    if abs(info.duration_s - expected.duration_s) > tolerance:
        raise VerificationError(
            f"Output lasts {info.duration_s:.2f}s, expected {expected.duration_s:.2f}s"
        )
    if expected.has_audio and not info.has_audio:
        raise VerificationError("Source has audio but the output does not")
    _decode_tail(path, info.duration_s)
    return info
