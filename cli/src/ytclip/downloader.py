"""Source media download (yt-dlp) with a per-video cache.

One cached file per (video, max height) is shared by every clip of that video.
yt-dlp is invoked as a subprocess with an argument list built from a validated video
ID; no user-supplied strings reach a shell. Only media the site serves to the user
is fetched: there is no DRM handling, no cookie extraction and no geo/age bypass.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ytclip.media.probe import ProbeError, probe
from ytclip.repository import JobRepository, MediaRecord
from ytclip.youtube import canonical_url

log = logging.getLogger(__name__)

PROGRESS_PREFIX = "ytclip-progress"
TITLE_PREFIX = "ytclip-title"
FILE_PREFIX = "ytclip-file"


class DownloadError(RuntimeError):
    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


_ERROR_HINTS: list[tuple[re.Pattern[str], str, bool]] = [
    (re.compile(r"private video", re.I), "This video is private. Only public or unlisted videos you can view are supported.", False),
    (re.compile(r"members[- ]only|join this channel", re.I), "This video is for channel members only and cannot be downloaded.", False),
    (re.compile(r"drm", re.I), "This video is DRM-protected; ytclip does not process protected media.", False),
    (re.compile(r"sign in to confirm your age|age[- ]restricted", re.I), "This video is age-restricted and requires sign-in; ytclip does not bypass access controls.", False),
    (re.compile(r"sign in to confirm you.re not a bot", re.I), "YouTube is asking for bot verification. Wait a while and retry, or update yt-dlp.", True),
    (re.compile(r"video unavailable|has been removed|does not exist", re.I), "The video is unavailable (removed, region-locked or the URL is wrong).", False),
    (re.compile(r"premiere|live event will begin|is not yet available", re.I), "The video has not been published yet.", True),
    (re.compile(r"http error 429|too many requests", re.I), "YouTube is rate limiting downloads (HTTP 429). Wait and run `ytclip retry`.", True),
    (re.compile(r"no space left", re.I), "Disk full while downloading. Free space or run `ytclip cache clean`.", True),
    (re.compile(r"unable to download|timed out|connection|network|resolve host|errno", re.I), "Network error while downloading. Check your connection and run `ytclip retry`.", True),
    (re.compile(r"requested format is not available", re.I), "No downloadable video format was found for this video. Try updating yt-dlp.", False),
]


def classify_error(stderr: str) -> DownloadError:
    for pattern, message, retryable in _ERROR_HINTS:
        if pattern.search(stderr):
            return DownloadError(message, retryable)
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    detail = lines[-1][:300] if lines else "unknown error"
    return DownloadError(f"yt-dlp failed: {detail}")


def format_selector(max_height: int) -> str:
    h = int(max_height)
    return (
        f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/"
        f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
    )


def js_runtime_args() -> list[str]:
    """yt-dlp needs a JS runtime for YouTube; it only looks for deno unless told otherwise."""
    if shutil.which("deno"):
        return []
    for runtime in ("node", "bun"):
        if path := shutil.which(runtime):
            return ["--js-runtimes", f"{runtime}:{path}"]
    return []


def build_command(video_id: str, target_stem: Path, max_height: int) -> list[str]:
    return [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist", "--no-mtime", "--no-config",
        *js_runtime_args(),
        # --print implies --quiet, which hides progress; --progress re-enables it.
        "--newline", "--continue", "--progress",
        "--retries", "5", "--fragment-retries", "5", "--socket-timeout", "30",
        "-f", format_selector(max_height),
        "--merge-output-format", "mp4",
        "--progress-template",
        f"download:{PROGRESS_PREFIX} %(progress.downloaded_bytes)s %(progress.total_bytes)s "
        "%(progress.total_bytes_estimate)s",
        "--print", f"before_dl:{TITLE_PREFIX} %(title)s",
        "--print", f"after_move:{FILE_PREFIX} %(filepath)s",
        "--no-simulate",
        "-o", f"{target_stem}.%(ext)s",
        "--", canonical_url(video_id),
    ]


def parse_progress(line: str) -> float | None:
    parts = line.split()
    if len(parts) != 4 or parts[0] != PROGRESS_PREFIX:
        return None
    try:
        done = float(parts[1])
    except ValueError:
        return None
    for total in parts[2:]:
        try:
            if float(total) > 0:
                return min(1.0, done / float(total))
        except ValueError:
            continue
    return None


@dataclass
class DownloadResult:
    media: MediaRecord
    path: Path
    cached: bool


class MediaCache:
    def __init__(self, repo: JobRepository, downloads_dir: Path, max_height: int) -> None:
        self.repo = repo
        self.downloads_dir = downloads_dir
        self.max_height = max_height

    @property
    def variant(self) -> str:
        return f"h{self.max_height}"

    def cached(self, video_id: str) -> MediaRecord | None:
        record = self.repo.get_media(video_id, self.variant)
        if record is None:
            return None
        path = Path(record.path)
        inside = path.resolve().is_relative_to(self.downloads_dir.resolve())
        try:
            if inside and path.is_file():
                probe(path)
                return record
        except ProbeError:
            log.warning("Cached file %s is unreadable; downloading again", path)
        if inside:  # never delete files outside the cache directory
            path.unlink(missing_ok=True)
        self.repo.delete_media(record.id)
        return None

    def ensure(self, video_id: str, title: str | None,
               on_progress: Callable[[float], None] | None = None) -> DownloadResult:
        if record := self.cached(video_id):
            self.repo.touch_media(record.id)
            return DownloadResult(record, Path(record.path), cached=True)
        target_dir = self.downloads_dir / video_id
        target_dir.mkdir(parents=True, exist_ok=True)
        path, fetched_title = self._download(video_id, target_dir / f"{video_id}_{self.variant}",
                                             on_progress)
        info = probe(path)
        record = self.repo.upsert_media(video_id, self.variant, str(path), fetched_title or title,
                                        info.duration_s, path.stat().st_size)
        return DownloadResult(record, path, cached=False)

    def _download(self, video_id: str, stem: Path,
                  on_progress: Callable[[float], None] | None) -> tuple[Path, str | None]:
        cmd = build_command(video_id, stem, self.max_height)
        log.debug("yt-dlp %s", cmd)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert proc.stdout is not None and proc.stderr is not None
        title = filepath = None
        stderr_chunks: list[str] = []
        drain = threading.Thread(target=lambda: stderr_chunks.extend(proc.stderr), daemon=True)
        drain.start()
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line.startswith(TITLE_PREFIX + " "):
                    title = line[len(TITLE_PREFIX) + 1:].strip() or None
                elif line.startswith(FILE_PREFIX + " "):
                    filepath = line[len(FILE_PREFIX) + 1:].strip()
                elif on_progress and (fraction := parse_progress(line)) is not None:
                    on_progress(fraction)
            code = proc.wait()
            drain.join(timeout=5)
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        stderr = "".join(stderr_chunks[-60:])
        if code != 0:
            log.debug("yt-dlp stderr:\n%s", stderr)
            raise classify_error(stderr)
        path = Path(filepath) if filepath else stem.with_suffix(".mp4")
        if not path.is_file() or not path.resolve().is_relative_to(self.downloads_dir.resolve()):
            raise DownloadError("yt-dlp reported success but the downloaded file is missing")
        return path, title
