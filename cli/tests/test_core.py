"""Timestamps, validation, filenames, geometry and configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import URL, VIDEO_ID, clip
from ytclip.config import ConfigError, load_config, write_default_config
from ytclip.filenames import (
    UnsafePathError, confined_path, output_filename, sanitize_stem, unique_path,
)
from ytclip.media.geometry import Size, center_offset, crop_size, custom_window, output_size
from ytclip.models import AspectRatio, CropMode, CropRect, Resolution
from ytclip.timeutil import (
    TimestampError, clip_duration_ms, compact_timestamp, format_timestamp, parse_timestamp,
)
from ytclip.validation import ValidationError, validate_clip
from ytclip.youtube import UnsupportedUrlError, extract_video_id


# ---- timestamps -----------------------------------------------------------------------

@pytest.mark.parametrize("text, ms", [
    ("90", 90_000), ("1:20", 80_000), ("00:01:20", 80_000), ("01:02:03", 3_723_000),
    ("75:00", 4_500_000), ("00:01:20.5", 80_500), ("00:00:00.125", 125), (" 5 ", 5_000),
])
def test_parse_timestamp(text: str, ms: int) -> None:
    assert parse_timestamp(text) == ms


@pytest.mark.parametrize("text", ["", "abc", "1:75", "1:60:00", "1:2:3:4", "-5", "1::2", "1:20.1234", "1e3"])
def test_parse_timestamp_rejects(text: str) -> None:
    with pytest.raises(TimestampError):
        parse_timestamp(text)


def test_numeric_timestamps_and_format_roundtrip() -> None:
    assert parse_timestamp(12.5) == 12_500
    with pytest.raises(TimestampError):
        parse_timestamp(-1)
    with pytest.raises(TimestampError):
        parse_timestamp(True)
    for ms in (0, 1_000, 80_500, 3_723_250):
        assert parse_timestamp(format_timestamp(ms)) == ms
    assert compact_timestamp(3_723_000) == "1h02m03s"
    assert compact_timestamp(80_000) == "01m20s"


def test_clip_duration() -> None:
    assert clip_duration_ms(80_000, 165_000) == 85_000
    for start, end in ((10, 10), (20, 10)):
        with pytest.raises(TimestampError):
            clip_duration_ms(start, end)


# ---- URLs and validation ---------------------------------------------------------------

@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/watch?v={VIDEO_ID}&t=42s&list=PL123",
    f"https://m.youtube.com/watch?v={VIDEO_ID}",
    f"https://youtu.be/{VIDEO_ID}?si=x",
    f"https://www.youtube.com/shorts/{VIDEO_ID}",
    f"https://youtube.com/live/{VIDEO_ID}",
])
def test_extract_video_id(url: str) -> None:
    assert extract_video_id(url) == VIDEO_ID


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/", "https://www.youtube.com/@channel", "https://www.youtube.com/watch?v=abc",
    f"https://evil.example/watch?v={VIDEO_ID}", f"https://youtube.com.evil.example/watch?v={VIDEO_ID}",
    f"ftp://youtube.com/watch?v={VIDEO_ID}", "not a url", f"file:///watch?v={VIDEO_ID}",
])
def test_extract_video_id_rejects(url: str) -> None:
    with pytest.raises(UnsupportedUrlError):
        extract_video_id(url)


def test_validate_clip_normalises() -> None:
    spec = validate_clip(clip(url=f"https://youtu.be/{VIDEO_ID}?t=3", title="  A   title ",
                              outputName="My Clip", aspectRatio="1:1", cropMode="blur", resolution="720p"))
    assert spec.url == URL  # query string from the browser is dropped
    assert spec.title == "A title"
    assert (spec.aspect_ratio, spec.crop_mode, spec.resolution) == (AspectRatio.SQUARE, CropMode.BLUR, Resolution.P720)
    assert spec.duration_ms == 2_000


def test_validate_clip_defaults() -> None:
    spec = validate_clip({"url": URL, "start": "0:10", "end": "0:20"})
    assert (spec.aspect_ratio, spec.crop_mode, spec.resolution) == (AspectRatio.PORTRAIT, CropMode.SMART, Resolution.P1080)


@pytest.mark.parametrize("overrides, field", [
    ({"end": "00:00:01"}, "end"),
    ({"end": "00:00:00"}, "end"),
    ({"end": "00:00:01.2"}, "end"),  # shorter than 0.5 s
    ({"start": "1:99"}, "start"),
    ({"start": None}, "start"),
    ({"start": ["00:01"]}, "start"),
    ({"aspectRatio": "4:3"}, "aspectRatio"),
    ({"cropMode": "zoom"}, "cropMode"),
    ({"resolution": "4k"}, "resolution"),
    ({"url": "https://vimeo.com/123"}, "url"),
    ({"url": 5}, "url"),
    ({"outputName": "x" * 151}, "outputName"),
    ({"title": 42}, "title"),
    ({"end": "04:00:02"}, "end"),
    ({"cropMode": "custom"}, "cropRect"),
    ({"cropMode": "custom", "cropRect": {"x": 0, "y": 0, "width": 0.01, "height": 1}}, "cropRect"),
    ({"cropMode": "custom", "cropRect": {"x": 0.8, "y": 0, "width": 0.5, "height": 1}}, "cropRect"),
    ({"cropMode": "custom", "cropRect": {"x": 0, "y": 0, "width": "1", "height": 1}}, "cropRect"),
    ({"cropMode": "custom", "cropRect": {"x": 0, "y": 0, "width": 1}}, "cropRect"),
    ({"cropMode": "center", "cropRect": {"x": 0, "y": 0, "width": 1, "height": 1}}, "cropRect"),
])
def test_validate_clip_errors(overrides: dict, field: str) -> None:
    with pytest.raises(ValidationError) as info:
        validate_clip(clip(**overrides))
    assert info.value.field == field


def test_validate_clip_rejects_unknown_fields_and_non_objects() -> None:
    with pytest.raises(ValidationError, match="Unknown field"):
        validate_clip({**clip(), "command": "rm -rf /"})
    with pytest.raises(ValidationError):
        validate_clip(["not", "a", "dict"])


# ---- filenames --------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("my clip/../../evil", "my_clip_evil"),
    ("../../etc/passwd", "etc_passwd"),
    ("..", ""),
    ("  .hidden ", "hidden"),
    ("a\x00b", "ab"),
    ("Title: part 2?", "Title_part_2"),
    ("clip.MP4", "clip"),
    ("Ünïcode — ok", "Ünïcode_ok"),
])
def test_sanitize_stem(raw: str, expected: str) -> None:
    assert sanitize_stem(raw) == expected


def test_output_filename(make_spec) -> None:
    assert output_filename(make_spec(outputName="Best bit")) == "Best_bit.mp4"
    assert output_filename(make_spec(title="Cool / video", start="1:05", end="1:15")) == "Cool_video_01m05s-01m15s_9x16.mp4"
    assert output_filename(make_spec(outputName="///")).startswith(VIDEO_ID)
    assert len(output_filename(make_spec(title="x" * 300))) <= 125


def test_confined_and_unique_paths(tmp_path: Path) -> None:
    assert confined_path(tmp_path, "a.mp4") == (tmp_path / "a.mp4").resolve()
    for bad in ("../a.mp4", "/etc/passwd", "sub/a.mp4"):
        with pytest.raises(UnsafePathError):
            confined_path(tmp_path, bad)
    target = tmp_path / "a.mp4"
    assert unique_path(target) == target
    target.write_bytes(b"x")
    (tmp_path / "a-1.mp4").write_bytes(b"x")
    assert unique_path(target) == tmp_path / "a-2.mp4"


# ---- geometry ----------------------------------------------------------------------------

@pytest.mark.parametrize("aspect, resolution, expected", [
    (AspectRatio.PORTRAIT, Resolution.P1080, Size(1080, 1920)),
    (AspectRatio.PORTRAIT, Resolution.P720, Size(720, 1280)),
    (AspectRatio.PORTRAIT, Resolution.P480, Size(480, 854)),
    (AspectRatio.LANDSCAPE, Resolution.P1080, Size(1920, 1080)),
    (AspectRatio.LANDSCAPE, Resolution.P480, Size(854, 480)),
    (AspectRatio.SQUARE, Resolution.P720, Size(720, 720)),
])
def test_output_size(aspect: AspectRatio, resolution: Resolution, expected: Size) -> None:
    assert output_size(aspect, resolution) == expected


@pytest.mark.parametrize("source, aspect, expected", [
    (Size(1920, 1080), AspectRatio.PORTRAIT, Size(608, 1080)),
    (Size(1920, 1080), AspectRatio.SQUARE, Size(1080, 1080)),
    (Size(1920, 1080), AspectRatio.LANDSCAPE, Size(1920, 1080)),
    (Size(1080, 1920), AspectRatio.LANDSCAPE, Size(1080, 608)),
    (Size(1080, 1920), AspectRatio.PORTRAIT, Size(1080, 1920)),
    (Size(1080, 1920), AspectRatio.SQUARE, Size(1080, 1080)),
    (Size(640, 480), AspectRatio.PORTRAIT, Size(270, 480)),
    (Size(641, 361), AspectRatio.SQUARE, Size(360, 360)),
])
def test_crop_size(source: Size, aspect: AspectRatio, expected: Size) -> None:
    crop = crop_size(source, aspect)
    assert crop == expected
    assert crop.width % 2 == 0 and crop.height % 2 == 0
    assert crop.width <= source.width and crop.height <= source.height
    rw, rh = aspect.ratio
    assert abs(crop.width / crop.height - rw / rh) < 0.01


def test_custom_crop_rect_is_validated_and_part_of_identity() -> None:
    rect = {"x": 0.25, "y": 0, "width": 0.751, "height": 1.0}
    spec = validate_clip(clip(cropMode="custom", cropRect=rect))
    assert spec.crop_rect == CropRect(0.25, 0, 0.75, 1.0)  # overshoot clamped into the frame
    moved = validate_clip(clip(cropMode="custom", cropRect={**rect, "x": 0.1}))
    assert spec.dedupe_key() != moved.dedupe_key()


def test_custom_window_fits_aspect_inside_the_area() -> None:
    # A 9:16 window inside the middle third of a 1920x1080 frame.
    crop, x, y = custom_window(Size(1920, 1080), CropRect(1 / 3, 0, 1 / 3, 1), AspectRatio.PORTRAIT)
    assert crop == Size(608, 1080) and (x, y) == (656, 0)
    # A wide area gets the 9:16 window centred in it, never outside the frame.
    crop, x, y = custom_window(Size(1920, 1080), CropRect(0.9, 0, 0.1, 1), AspectRatio.SQUARE)
    assert crop.width == crop.height and x + crop.width <= 1920 and y + crop.height <= 1080


def test_center_offset() -> None:
    assert center_offset(Size(1920, 1080), Size(608, 1080)) == (656, 0)


# ---- config ------------------------------------------------------------------------------

def test_config_defaults_and_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    assert load_config(path).overwrite == "rename"
    write_default_config(path)
    assert load_config(path).smart.fallback == "center"  # template is all comments
    path.write_text('output_dir = "~/clips"\noverwrite = "skip"\n[smart]\nfallback = "blur"\nsample_fps = 2\n')
    cfg = load_config(path)
    assert cfg.output_dir == Path.home() / "clips"
    assert (cfg.overwrite, cfg.smart.fallback, cfg.smart.sample_fps) == ("skip", "blur", 2.0)


@pytest.mark.parametrize("content", [
    'overwrite = "maybe"', "unknown_key = 1", '[smart]\nfallback = "zoom"', "max_attempts = 0",
    '[smart]\nuse_motion = "yes"', 'video_crf = "high"', "not toml [",
])
def test_config_errors(tmp_path: Path, content: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(content)
    with pytest.raises(ConfigError):
        load_config(path)
