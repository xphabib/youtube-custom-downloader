"""Validation of untrusted clip requests (from the extension or the CLI).

This module is the single source of truth for what a valid clip is; the extension
performs the same checks only to give instant feedback.
"""

from __future__ import annotations

from typing import Any

from ytclip.models import AspectRatio, ClipSpec, CropMode, CropRect, Resolution
from ytclip.timeutil import TimestampError, parse_timestamp
from ytclip.youtube import UnsupportedUrlError, canonical_url, extract_video_id

MAX_TITLE_LENGTH = 300
MAX_OUTPUT_NAME_LENGTH = 150
MAX_CLIP_MS = 3 * 60 * 60 * 1000
MIN_CLIP_MS = 500
MIN_CROP_FRACTION = 0.02
CLIP_FIELDS = {
    "url", "title", "start", "end", "aspectRatio", "cropMode", "resolution", "outputName", "cropRect",
}
CROP_RECT_FIELDS = ("x", "y", "width", "height")


class ValidationError(ValueError):
    """A request field is invalid. ``field`` names the offending key for the UI."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


def _enum(enum_cls: type, value: Any, field: str, default: Any) -> Any:
    if value is None:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(m.value for m in enum_cls)
        raise ValidationError(f"{field} must be one of: {allowed}", field) from None


def _optional_text(value: Any, field: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text", field)
    text = " ".join(value.split())
    if len(text) > max_length:
        raise ValidationError(f"{field} must be at most {max_length} characters", field)
    return text or None


def _timestamp(value: Any, field: str) -> int:
    if value is None:
        raise ValidationError(f"{field} time is required", field)
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field} time must be a timestamp", field)
    try:
        return parse_timestamp(value)
    except TimestampError as exc:
        raise ValidationError(str(exc), field) from None


def _crop_rect(value: Any) -> CropRect:
    """A window inside the frame, as fractions; tiny rounding overshoots are clamped."""
    field = "cropRect"
    if not isinstance(value, dict) or set(value) != set(CROP_RECT_FIELDS):
        raise ValidationError("Choose the custom crop area on the video", field)
    numbers = []
    for key in CROP_RECT_FIELDS:
        v = value[key]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not -0.01 <= v <= 1.01:
            raise ValidationError(f"cropRect.{key} must be a number between 0 and 1", field)
        numbers.append(min(1.0, max(0.0, float(v))))
    x, y, width, height = numbers
    if width < MIN_CROP_FRACTION or height < MIN_CROP_FRACTION:
        raise ValidationError("The custom crop area is too small", field)
    if x + width > 1.01 or y + height > 1.01:
        raise ValidationError("The custom crop area must be inside the video", field)
    width, height = min(width, 1 - x), min(height, 1 - y)
    return CropRect(*(round(v, 4) for v in (x, y, width, height)))


def validate_clip(data: Any) -> ClipSpec:
    """Validate a raw clip mapping and return a normalised ``ClipSpec``."""
    if not isinstance(data, dict):
        raise ValidationError("Clip must be an object")
    unknown = set(data) - CLIP_FIELDS
    if unknown:
        raise ValidationError(f"Unknown field(s): {', '.join(sorted(unknown))}")
    url = data.get("url")
    if not isinstance(url, str) or len(url) > 2048:
        raise ValidationError("A YouTube video URL is required", "url")
    try:
        video_id = extract_video_id(url)
    except UnsupportedUrlError as exc:
        raise ValidationError(str(exc), "url") from None

    start_ms = _timestamp(data.get("start"), "start")
    end_ms = _timestamp(data.get("end"), "end")
    if end_ms <= start_ms:
        raise ValidationError("End time must be greater than start time", "end")
    if end_ms - start_ms < MIN_CLIP_MS:
        raise ValidationError("Clip must be at least 0.5 seconds long", "end")
    if end_ms - start_ms > MAX_CLIP_MS:
        raise ValidationError("Clip must be at most 3 hours long", "end")

    crop_mode = _enum(CropMode, data.get("cropMode"), "cropMode", CropMode.SMART)
    raw_rect = data.get("cropRect")
    if crop_mode is CropMode.CUSTOM:
        crop_rect = _crop_rect(raw_rect)
    elif raw_rect is not None:
        raise ValidationError("cropRect is only used with the custom crop mode", "cropRect")
    else:
        crop_rect = None

    return ClipSpec(
        url=canonical_url(video_id),
        video_id=video_id,
        start_ms=start_ms,
        end_ms=end_ms,
        aspect_ratio=_enum(AspectRatio, data.get("aspectRatio"), "aspectRatio", AspectRatio.PORTRAIT),
        crop_mode=crop_mode,
        resolution=_enum(Resolution, data.get("resolution"), "resolution", Resolution.P1080),
        title=_optional_text(data.get("title"), "title", MAX_TITLE_LENGTH),
        output_name=_optional_text(data.get("outputName"), "outputName", MAX_OUTPUT_NAME_LENGTH),
        crop_rect=crop_rect,
    )
