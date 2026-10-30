"""YouTube URL recognition.

Only public watch, Shorts, live and youtu.be links are accepted. Every accepted URL is
normalised to a canonical form so the downloader never receives untrusted query strings.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
_PATH_PREFIXES = ("/shorts/", "/live/", "/embed/")


class UnsupportedUrlError(ValueError):
    """Raised for URLs that are not a supported YouTube video page."""


def extract_video_id(url: str) -> str:
    """Return the 11-character video ID for a supported YouTube URL."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsupportedUrlError("URL must start with http:// or https://")
    host = (parsed.hostname or "").lower()
    candidate: str | None = None
    if host in _SHORT_HOSTS:
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host in _YOUTUBE_HOSTS:
        if parsed.path == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [None])[0]
        else:
            for prefix in _PATH_PREFIXES:
                if parsed.path.startswith(prefix):
                    candidate = parsed.path[len(prefix):].split("/")[0]
                    break
    else:
        raise UnsupportedUrlError(f"'{host or url}' is not a YouTube address")
    if not candidate or not _VIDEO_ID_RE.match(candidate):
        raise UnsupportedUrlError("Not a YouTube video page (watch, Shorts or youtu.be link)")
    return candidate


def canonical_url(video_id: str) -> str:
    if not _VIDEO_ID_RE.match(video_id):
        raise UnsupportedUrlError(f"Invalid video id '{video_id}'")
    return f"https://www.youtube.com/watch?v={video_id}"
