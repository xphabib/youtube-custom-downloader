"""Timestamp parsing and formatting.

Accepted inputs: ``SS``, ``MM:SS``, ``HH:MM:SS``, each optionally with a fractional
part (``01:20.5``). Values are stored internally as integer milliseconds so that
equality checks (duplicate detection) are exact.
"""

from __future__ import annotations

import re

_SECONDS_RE = re.compile(r"^(\d+)(?:\.(\d{1,3}))?$")
_FIELD_RE = re.compile(r"^\d+$")


class TimestampError(ValueError):
    """Raised when a timestamp string cannot be parsed."""


def parse_timestamp(value: str | int | float) -> int:
    """Parse a timestamp into milliseconds."""
    if isinstance(value, bool):
        raise TimestampError("Timestamp must be a string or number")
    if isinstance(value, (int, float)):
        if value < 0:
            raise TimestampError("Timestamp cannot be negative")
        return int(round(value * 1000))
    text = value.strip()
    *head, tail = text.split(":")
    seconds_match = _SECONDS_RE.match(tail)
    if len(head) > 2 or not seconds_match or not all(_FIELD_RE.match(f) for f in head):
        raise TimestampError(f"Invalid timestamp '{value}'. Use HH:MM:SS, MM:SS or seconds.")
    seconds, fraction = seconds_match.groups()
    hours, minutes = ([0] * (2 - len(head)) + [int(f) for f in head])
    sec = int(seconds)
    if head and sec > 59:
        raise TimestampError(f"Invalid timestamp '{value}': seconds must be below 60.")
    if len(head) == 2 and minutes > 59:
        raise TimestampError(f"Invalid timestamp '{value}': minutes must be below 60.")
    ms = int((fraction or "0").ljust(3, "0"))
    return ((hours * 60 + minutes) * 60 + sec) * 1000 + ms


def format_timestamp(ms: int) -> str:
    """Format milliseconds as ``HH:MM:SS`` (with ``.mmm`` only when non-zero)."""
    if ms < 0:
        raise ValueError("Timestamp cannot be negative")
    total_seconds, millis = divmod(ms, 1000)
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    base = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{base}.{millis:03d}" if millis else base


def clip_duration_ms(start_ms: int, end_ms: int) -> int:
    """Duration of a clip; raises if the range is empty or reversed."""
    if end_ms <= start_ms:
        raise TimestampError("End time must be greater than start time.")
    return end_ms - start_ms


def compact_timestamp(ms: int) -> str:
    """Filename-friendly timestamp such as ``1h02m03s`` or ``01m20s``."""
    total_seconds = ms // 1000
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    return f"{minutes:02d}m{seconds:02d}s"
