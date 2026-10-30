"""Run ffmpeg with machine-readable progress and readable failures."""

from __future__ import annotations

import collections
import logging
import subprocess
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

log = logging.getLogger(__name__)

ProgressCallback = Callable[[float], None]


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.stderr_tail = stderr_tail


def _summarize(stderr_tail: str) -> str:
    text = stderr_tail.lower()
    if "no space left" in text:
        return "Disk full while writing video. Free some space and retry."
    if "invalid data found" in text or "moov atom not found" in text:
        return "Source media is corrupt or incomplete. Clear the cache (`ytclip cache clean --all`) and retry."
    if "permission denied" in text:
        return "Permission denied while writing output. Check the output directory permissions."
    last = stderr_tail.strip().splitlines()[-1:] or ["unknown error"]
    return f"ffmpeg failed: {last[0][:300]}"


def run_ffmpeg(
    args: Sequence[str],
    duration_s: float | None = None,
    on_progress: ProgressCallback | None = None,
    ffmpeg: str = "ffmpeg",
    cwd: Path | None = None,
) -> None:
    """Run ``ffmpeg <args>``; ``args`` is an argument list, never a shell string."""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error",
           "-progress", "pipe:1", *args]
    log.debug("ffmpeg %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=cwd)
    except FileNotFoundError:
        raise FFmpegError("ffmpeg was not found. Install it with: sudo apt install ffmpeg") from None

    stderr_lines: collections.deque[str] = collections.deque(maxlen=40)
    try:
        assert proc.stdout is not None and proc.stderr is not None
        # Drain stderr concurrently so a flood of decode errors cannot fill the pipe.
        drain = threading.Thread(target=lambda: stderr_lines.extend(proc.stderr), daemon=True)
        drain.start()
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and duration_s and on_progress and value.isdigit():
                on_progress(min(1.0, int(value) / 1_000_000 / duration_s))
        code = proc.wait()
        drain.join(timeout=5)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if code != 0:
        tail = "".join(stderr_lines)
        log.debug("ffmpeg stderr:\n%s", tail)
        raise FFmpegError(_summarize(tail), tail)
