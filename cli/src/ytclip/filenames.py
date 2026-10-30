"""Safe output filenames and paths confined to configured directories."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from ytclip.models import ClipSpec
from ytclip.timeutil import compact_timestamp

MAX_STEM_LENGTH = 120
_UNSAFE_CHARS = re.compile(r"[^\w.\- ]+", re.UNICODE)
_WHITESPACE = re.compile(r"[\s_]+")
_DOT_RUNS = re.compile(r"\.{2,}")
_RESERVED = {"", ".", ".."}


class UnsafePathError(ValueError):
    """Raised when a path would escape its configured directory."""


def sanitize_stem(name: str) -> str:
    """Reduce arbitrary text to a safe single path component without extension."""
    text = unicodedata.normalize("NFKC", name)
    text = text.replace("/", " ").replace("\\", " ").replace("\x00", "")
    if text.lower().endswith(".mp4"):
        text = text[:-4]
    text = _UNSAFE_CHARS.sub(" ", text)
    text = _DOT_RUNS.sub(" ", text)
    text = _WHITESPACE.sub("_", text.strip())
    text = text.strip("._-")[:MAX_STEM_LENGTH].rstrip("._-")
    return text if text not in _RESERVED else ""


def default_stem(spec: ClipSpec) -> str:
    base = sanitize_stem(spec.title or "") or spec.video_id
    ratio = spec.aspect_ratio.value.replace(":", "x")
    span = f"{compact_timestamp(spec.start_ms)}-{compact_timestamp(spec.end_ms)}"
    return sanitize_stem(f"{base[:80]}_{span}_{ratio}") or spec.video_id


def output_filename(spec: ClipSpec) -> str:
    stem = sanitize_stem(spec.output_name) if spec.output_name else ""
    return f"{stem or default_stem(spec)}.mp4"


def confined_path(root: Path, filename: str) -> Path:
    """Join ``filename`` onto ``root`` and refuse anything that resolves outside it."""
    root_resolved = root.resolve()
    candidate = (root_resolved / filename).resolve()
    if candidate.parent != root_resolved:
        raise UnsafePathError(f"Refusing path outside {root_resolved}: {filename}")
    return candidate


def unique_path(path: Path) -> Path:
    """Return ``path`` or the first free ``stem-N.mp4`` sibling."""
    if not path.exists():
        return path
    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find a free filename near {path}")
