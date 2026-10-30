"""Real ffmpeg rendering on synthetic footage: dimensions, duration, audio, crop placement."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_moving_box, requires_ffmpeg, subject_center_x
from ytclip.config import SmartCropConfig
from ytclip.media.ffmpeg import FFmpegError, run_ffmpeg
from ytclip.media.geometry import Size, crop_size, output_size
from ytclip.media.probe import ProbeError, probe
from ytclip.media.render import EncodeSettings, RenderRequest, render, write_sendcmd
from ytclip.media.verify import Expectation, VerificationError, verify_output
from ytclip.models import AspectRatio, CropMode, CropRect, Resolution
from ytclip.smartcrop.detectors import CompositeDetector
from ytclip.smartcrop.planner import plan_smart_crop
from ytclip.smartcrop.sampler import SamplePlan, iter_frames

pytestmark = [pytest.mark.media, requires_ffmpeg]
FAST = EncodeSettings(crf=28, preset="ultrafast")


def _render(source: Path, out: Path, aspect: AspectRatio, mode: CropMode, start: float = 0.5,
            duration: float = 3.0, resolution: Resolution = Resolution.P480,
            rect: CropRect = CropRect(0.25, 0.1, 0.5, 0.8)) -> Size:
    info = probe(source)
    size = output_size(aspect, resolution)
    plan = None
    if mode is CropMode.SMART:
        plan = plan_smart_crop(source, info.size, crop_size(info.size, aspect), start, duration,
                               SmartCropConfig(), CompositeDetector([]))
    render(RenderRequest(source, info, start, duration, aspect, size, out, mode, plan,
                         out.with_suffix(".cmd"), rect if mode is CropMode.CUSTOM else None), FAST)
    return size


def test_probe(moving_box: Path, moving_box_silent: Path) -> None:
    info = probe(moving_box)
    assert (info.width, info.height) == (640, 360)
    assert info.fps == pytest.approx(30)
    assert info.duration_s == pytest.approx(4.0, abs=0.1)
    assert info.has_audio and info.video_codec == "h264"
    assert probe(moving_box_silent).has_audio is False


def test_probe_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(ProbeError):
        probe(bad)


@pytest.mark.parametrize("aspect", list(AspectRatio))
@pytest.mark.parametrize("mode", list(CropMode))
def test_every_mode_and_ratio_produces_verified_output(moving_box: Path, tmp_path: Path,
                                                        aspect: AspectRatio, mode: CropMode) -> None:
    out = tmp_path / f"{mode.value}.mp4"
    size = _render(moving_box, out, aspect, mode)
    info = verify_output(out, Expectation(size, 3.0, has_audio=True))
    assert (info.width, info.height) == (size.width, size.height)
    assert info.duration_s == pytest.approx(3.0, abs=0.15)
    assert info.has_audio and info.audio_codec == "aac"


def test_silent_source_stays_silent(moving_box_silent: Path, tmp_path: Path) -> None:
    out = tmp_path / "silent.mp4"
    size = _render(moving_box_silent, out, AspectRatio.PORTRAIT, CropMode.CENTER)
    assert verify_output(out, Expectation(size, 3.0, has_audio=False)).has_audio is False


def test_trim_is_accurate(moving_box: Path, tmp_path: Path) -> None:
    out = tmp_path / "trim.mp4"
    size = _render(moving_box, out, AspectRatio.LANDSCAPE, CropMode.CENTER, start=1.0, duration=1.5)
    assert verify_output(out, Expectation(size, 1.5, True)).duration_s == pytest.approx(1.5, abs=0.1)
    # The box starts at x=20+130t; at clip t=0 (source t=1.0) its centre is at x=182/640.
    assert subject_center_x(out, [0.0])[0] == pytest.approx((150 + 32) / 640, abs=0.03)


def test_smart_crop_follows_the_subject_but_center_does_not(moving_box: Path, tmp_path: Path) -> None:
    times = [0.2, 1.0, 1.8, 2.6]
    smart = tmp_path / "smart.mp4"
    center = tmp_path / "center.mp4"
    _render(moving_box, smart, AspectRatio.PORTRAIT, CropMode.SMART)
    _render(moving_box, center, AspectRatio.PORTRAIT, CropMode.CENTER)
    smart_x = subject_center_x(smart, times)
    center_x = subject_center_x(center, times)
    assert all(x is not None and abs(x - 0.5) < 0.2 for x in smart_x), smart_x
    assert sum(x is None for x in center_x) >= 2, center_x  # box leaves the fixed window


def test_blur_keeps_the_whole_frame(moving_box: Path, tmp_path: Path) -> None:
    out = tmp_path / "blur.mp4"
    _render(moving_box, out, AspectRatio.PORTRAIT, CropMode.BLUR)
    # Fitted 640x360 into 480x854: the box is visible at its scaled source position.
    expected = (20 + 130 * 0.7 + 32) / 640
    assert subject_center_x(out, [0.2])[0] == pytest.approx(expected, abs=0.05)


def test_custom_crop_uses_the_chosen_area(moving_box: Path, tmp_path: Path) -> None:
    out = tmp_path / "custom.mp4"
    # Left half of the 640x360 frame -> a 320x320 square window at x=0, y=20.
    _render(moving_box, out, AspectRatio.SQUARE, CropMode.CUSTOM, rect=CropRect(0, 0, 0.5, 1))
    # At clip t=0.2 (source t=0.7) the box centre is at x=20+130*0.7+32=143 px.
    assert subject_center_x(out, [0.2])[0] == pytest.approx(143 / 320, abs=0.04)


def test_sendcmd_file_contents(moving_box: Path, tmp_path: Path) -> None:
    info = probe(moving_box)
    crop = crop_size(info.size, AspectRatio.PORTRAIT)
    plan = plan_smart_crop(moving_box, info.size, crop, 0.5, 3.0, SmartCropConfig(), CompositeDetector([]))
    path = tmp_path / "crop.cmd"
    count = write_sendcmd(plan, info.fps, 3.0, path)
    lines = path.read_text().splitlines()
    assert count == len(lines) and 10 < count <= 91
    first = lines[0].split()
    assert first[0] == "0.0000" and first[1] == "crop@smart" and first[2] == "x"
    xs = [int(line.split()[3].rstrip(",")) for line in lines]
    assert xs == sorted(xs)  # box moves right, so the window only moves right
    assert all(0 <= x <= info.width - crop.width for x in xs)


def test_sampler_yields_expected_frames(moving_box: Path) -> None:
    plan = SamplePlan.for_clip(Size(640, 360), 3.0, sample_fps=4, max_samples=6, analysis_width=320)
    assert plan.fps == pytest.approx(2.0) and plan.size == Size(320, 180)
    frames = list(iter_frames(moving_box, 0.5, 3.0, plan))
    assert len(frames) == 6
    assert frames[0][1].shape == (180, 320, 3)
    assert [round(t, 2) for t, _ in frames] == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]


def test_portrait_source_to_landscape(tmp_path: Path) -> None:
    source = make_moving_box(tmp_path / "portrait.mp4", duration=2, size="360x640", x_expr="100")
    out = tmp_path / "land.mp4"
    size = _render(source, out, AspectRatio.LANDSCAPE, CropMode.SMART, start=0, duration=2)
    assert verify_output(out, Expectation(size, 2.0, True)).width == 854


def test_verification_catches_wrong_output(moving_box: Path, tmp_path: Path) -> None:
    out = tmp_path / "v.mp4"
    size = _render(moving_box, out, AspectRatio.SQUARE, CropMode.CENTER, duration=2.0)
    with pytest.raises(VerificationError, match="expected 720x720"):
        verify_output(out, Expectation(Size(720, 720), 2.0, True))
    with pytest.raises(VerificationError, match="lasts"):
        verify_output(out, Expectation(size, 3.5, True))
    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes(out.read_bytes()[: out.stat().st_size // 2])
    with pytest.raises(VerificationError):
        verify_output(truncated, Expectation(size, 2.0, True))
    with pytest.raises(VerificationError, match="missing"):
        verify_output(tmp_path / "nope.mp4", Expectation(size, 2.0, True))


def test_ffmpeg_errors_are_readable(tmp_path: Path) -> None:
    with pytest.raises(FFmpegError) as info:
        run_ffmpeg(["-i", str(tmp_path / "missing.mp4"), str(tmp_path / "o.mp4")])
    assert "ffmpeg failed" in str(info.value) and info.value.stderr_tail


def test_scene_cut_is_detected_and_crop_jumps_with_it(scene_cut_video: Path, tmp_path: Path) -> None:
    info = probe(scene_cut_video)
    crop = crop_size(info.size, AspectRatio.PORTRAIT)
    plan = plan_smart_crop(scene_cut_video, info.size, crop, 0, 6, SmartCropConfig(), CompositeDetector([]))
    assert plan.stats["scenes"] == 2
    assert plan.offset_at(1.0)[0] < 100 and plan.offset_at(5.0)[0] > info.width - crop.width - 100
    out = tmp_path / "cut.mp4"
    _render(scene_cut_video, out, AspectRatio.PORTRAIT, CropMode.SMART, start=0, duration=6)
    xs = subject_center_x(out, [1.0, 2.5, 3.5, 5.0])
    assert all(x is not None for x in xs), xs  # subject visible on both sides of the cut
