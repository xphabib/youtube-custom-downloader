"""Smart crop algorithm: trajectory smoothing, subject tracking and plan building."""

from __future__ import annotations

import numpy as np
import pytest

from ytclip.config import SmartCropConfig
from ytclip.media.geometry import Size
from ytclip.smartcrop.detectors import Box, Detection, MotionSaliency
from ytclip.smartcrop.planner import _Samples, add_motion_detections, build_plan
from ytclip.smartcrop.scenes import segment_bounds
from ytclip.smartcrop.tracker import build_subjects, track_segment
from ytclip.smartcrop.trajectory import (
    apply_deadzone, build_axis, clamp_center, enforce_keep, fill_gaps, gaussian_smooth, limit_velocity,
)

SOURCE = Size(1920, 1080)
CROP = Size(608, 1080)  # 9:16 window
EXTENT_X = CROP.width / SOURCE.width


def person(cx: float, width: float = 0.12, score: float = 0.9) -> Detection:
    return Detection(Box(cx - width / 2, 0.2, cx + width / 2, 0.95), score, "person")


def face(cx: float, cy: float = 0.3, size: float = 0.05) -> Detection:
    return Detection(Box(cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2), 0.95, "face")


# ---- trajectory --------------------------------------------------------------------------

def test_fill_gaps_interpolates_and_holds() -> None:
    assert fill_gaps([None, None]) is None
    np.testing.assert_allclose(fill_gaps([None, 0.2, None, 0.6, None]), [0.2, 0.2, 0.4, 0.6, 0.6])


def test_deadzone_ignores_jitter_but_follows_real_moves() -> None:
    jitter = np.array([0.5, 0.51, 0.49, 0.505, 0.495])
    np.testing.assert_allclose(apply_deadzone(jitter, 0.02), 0.5)
    moved = apply_deadzone(np.array([0.5, 0.5, 0.7, 0.7]), 0.02)
    assert moved[-1] == pytest.approx(0.68)


def test_gaussian_smooth_preserves_linear_motion_and_removes_noise() -> None:
    ramp = np.linspace(0.2, 0.8, 30)
    np.testing.assert_allclose(gaussian_smooth(ramp, 3), ramp, atol=1e-9)  # no flattening at the ends
    rng = np.random.default_rng(0)
    noisy = 0.5 + rng.normal(0, 0.05, 200)
    assert np.std(gaussian_smooth(noisy, 4)) < np.std(noisy) / 3


def test_limit_velocity_turns_jumps_into_centred_pans() -> None:
    step = np.array([0.2] * 20 + [0.8] * 20)
    limited = limit_velocity(step, 0.05)
    assert np.max(np.abs(np.diff(limited))) <= 0.05 + 1e-9
    assert limited[0] == pytest.approx(0.2) and limited[-1] == pytest.approx(0.8)
    assert (limited[19] + limited[20]) / 2 == pytest.approx(0.5)  # pan is centred on the jump


def test_enforce_keep_and_clamp() -> None:
    centers = np.array([0.5, 0.5, 0.5])
    half = 0.15
    kept = enforce_keep(centers, [(0.70, 0.75), None, (0.1, 0.9)], half)
    assert kept[0] - half <= 0.70 and kept[0] + half >= 0.75  # region pulled into view
    assert kept[1] == 0.5  # nothing to keep
    assert kept[2] == 0.5  # region wider than the window: leave it
    np.testing.assert_allclose(clamp_center(np.array([0.0, 0.5, 1.0]), half), [0.15, 0.5, 0.85])


def test_build_axis_full_extent_and_no_detection() -> None:
    np.testing.assert_allclose(build_axis([0.1, 0.9], [None, None], 1.0, 0.05, 1, 0.1), [0.5, 0.5])
    assert build_axis([None, None, None], [None] * 3, 0.3, 0.05, 1, 0.1) is None


def test_build_axis_output_is_smooth_and_in_bounds() -> None:
    rng = np.random.default_rng(1)
    targets = list(np.clip(np.linspace(0.1, 0.9, 60) + rng.normal(0, 0.03, 60), 0, 1))
    path = build_axis(targets, [None] * 60, EXTENT_X, 0.06, sigma=2.4, max_step=0.35 / 3)
    assert path is not None
    assert path.min() >= EXTENT_X / 2 - 1e-9 and path.max() <= 1 - EXTENT_X / 2 + 1e-9
    assert np.max(np.abs(np.diff(path))) <= 0.35 / 3 + 1e-9
    assert np.std(np.diff(path, 2)) < np.std(np.diff(targets, 2)) / 5  # much less jerk than the input


# ---- tracking ----------------------------------------------------------------------------

def test_faces_attach_to_their_person() -> None:
    subjects = build_subjects([person(0.3), face(0.31), person(0.7)])
    with_face = [s for s in subjects if s.face]
    assert len(subjects) == 2 and len(with_face) == 1
    assert with_face[0].box.cx == pytest.approx(0.3)


def test_loose_face_becomes_subject_and_motion_is_only_a_fallback() -> None:
    motion = Detection(Box(0.8, 0.4, 0.9, 0.6), 0.5, "motion", 0.35)
    subjects = build_subjects([face(0.5), motion])
    assert len(subjects) == 1 and subjects[0].face is not None
    assert len(build_subjects([motion])) == 1


def test_tracking_prefers_continuity_over_a_briefly_stronger_subject() -> None:
    frames = [[person(0.25)], [person(0.27)], [person(0.27), person(0.8, score=1.0, width=0.14)], [person(0.29)]]
    foci = track_segment(frames, EXTENT_X, 1.0)
    assert all(f is not None and f.x < 0.4 for f in foci)


def test_group_is_framed_when_it_fits() -> None:
    focus = track_segment([[person(0.40), person(0.52)]], EXTENT_X, 1.0)[0]
    assert focus is not None
    assert focus.keep.x1 < 0.36 and focus.keep.x2 > 0.56  # both people kept
    apart = track_segment([[person(0.1), person(0.9)]], EXTENT_X, 1.0)[0]
    assert apart is not None and apart.keep.width < EXTENT_X  # too far apart: follow one


def test_no_detections_gives_none() -> None:
    assert track_segment([[], []], EXTENT_X, 1.0) == [None, None]


# ---- plan building -----------------------------------------------------------------------

def _samples(detections: list[list[Detection]], fps: float = 3.0, cuts: list[bool] | None = None) -> _Samples:
    n = len(detections)
    return _Samples(times=[i / fps for i in range(n)], detections=detections, cuts=cuts or [False] * n)


def test_plan_follows_a_moving_subject() -> None:
    xs = np.linspace(0.2, 0.8, 30)
    plan = build_plan(_samples([[person(x)] for x in xs]), SOURCE, CROP, 3.0, SmartCropConfig())
    assert plan.subject_found and plan.stats["detected_ratio"] == 1.0
    first, last = plan.offset_at(0)[0], plan.offset_at(29 / 3)[0]
    assert last - first > 0.4 * SOURCE.width  # the window really pans
    for t, x in zip([i / 3 for i in range(30)], xs):
        left, _ = plan.offset_at(t)
        assert left <= x * SOURCE.width <= left + CROP.width  # subject centre always in frame


def test_plan_stays_still_for_a_static_subject() -> None:
    rng = np.random.default_rng(2)
    detections = [[person(0.7 + rng.normal(0, 0.005))] for _ in range(30)]
    plan = build_plan(_samples(detections), SOURCE, CROP, 3.0, SmartCropConfig())
    offsets = {plan.offset_at(i / 3)[0] for i in range(30)}
    assert max(offsets) - min(offsets) <= 4
    assert abs(plan.offset_at(5)[0] + CROP.width / 2 - 0.7 * SOURCE.width) < 40


def test_plan_without_detections_reports_fallback() -> None:
    plan = build_plan(_samples([[] for _ in range(10)]), SOURCE, CROP, 3.0, SmartCropConfig())
    assert not plan.subject_found
    assert plan.offset_at(1)[0] == (SOURCE.width - CROP.width) // 2 or abs(
        plan.offset_at(1)[0] - (SOURCE.width - CROP.width) / 2) <= 1


def test_plan_switches_instantly_at_scene_cuts() -> None:
    detections = [[person(0.2)]] * 9 + [[person(0.8)]] * 9
    cuts = [False] * 9 + [True] + [False] * 8
    plan = build_plan(_samples(detections, cuts=cuts), SOURCE, CROP, 3.0, SmartCropConfig())
    assert plan.stats["scenes"] == 2
    before, after = plan.offset_at(2.6)[0], plan.offset_at(3.0)[0]
    assert before + CROP.width / 2 < 0.35 * SOURCE.width
    assert after + CROP.width / 2 > 0.65 * SOURCE.width


def test_plan_offsets_are_always_inside_the_frame() -> None:
    plan = build_plan(_samples([[person(0.0, 0.05)], [person(1.0, 0.05)]]), SOURCE, CROP, 3.0, SmartCropConfig())
    for t in np.linspace(-1, 2, 25):
        x, y = plan.offset_at(float(t))
        assert 0 <= x <= SOURCE.width - CROP.width and y == 0


def test_segment_bounds() -> None:
    assert segment_bounds([]) == []
    assert segment_bounds([False, False, True, False, True]) == [(0, 2), (2, 4), (4, 5)]


def test_motion_saliency_locates_subject_against_background() -> None:
    background = np.full((90, 160), 30, np.uint8)
    frames = []
    for x in (20, 60, 100):
        frame = background.copy()
        frame[30:60, x:x + 16] = 250
        frames.append(frame)
    samples = _Samples(times=[0, 1, 2], detections=[[], [], []], cuts=[False] * 3, grays=frames)
    add_motion_detections(samples)
    centres = [d[0].box.cx * 160 for d in samples.detections]
    np.testing.assert_allclose(centres, [28, 68, 108], atol=6)
    assert MotionSaliency().detect(background, background) is None
