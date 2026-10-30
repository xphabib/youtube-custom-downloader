"""Build a smart-crop plan for one clip.

The plan is a set of per-scene keyframe tracks (sample time -> crop centre). Rendering
queries ``CropPlan.offset_at(t)`` for every output frame; positions are linearly
interpolated inside a scene and switch instantly at detected cuts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ytclip.config import SmartCropConfig
from ytclip.media.geometry import Size
from ytclip.smartcrop.detectors import Detection, Detector, MotionSaliency
from ytclip.smartcrop.sampler import SamplePlan, iter_frames
from ytclip.smartcrop.scenes import frame_signature, is_cut, segment_bounds
from ytclip.smartcrop.tracker import Focus, track_segment
from ytclip.smartcrop.trajectory import build_axis

log = logging.getLogger(__name__)

MOTION_WIDTH = 160


@dataclass
class SceneTrack:
    start_s: float
    times: np.ndarray
    centers_x: np.ndarray  # normalised crop-centre positions
    centers_y: np.ndarray


@dataclass
class CropPlan:
    source: Size
    crop: Size
    scenes: list[SceneTrack]
    subject_found: bool
    stats: dict = field(default_factory=dict)

    def offset_at(self, t: float) -> tuple[int, int]:
        """Top-left crop offset in source pixels at clip time ``t``."""
        scene = self.scenes[0]
        for candidate in self.scenes:
            if candidate.start_s <= t:
                scene = candidate
        cx = float(np.interp(t, scene.times, scene.centers_x))
        cy = float(np.interp(t, scene.times, scene.centers_y))
        max_x, max_y = self.source.width - self.crop.width, self.source.height - self.crop.height
        x = int(round(cx * self.source.width - self.crop.width / 2))
        y = int(round(cy * self.source.height - self.crop.height / 2))
        return min(max(x, 0), max_x), min(max(y, 0), max_y)


@dataclass
class _Samples:
    times: list[float] = field(default_factory=list)
    detections: list[list[Detection]] = field(default_factory=list)
    cuts: list[bool] = field(default_factory=list)
    grays: list[np.ndarray] = field(default_factory=list)


def _motion_frame(frame: np.ndarray) -> np.ndarray:
    h = max(2, int(MOTION_WIDTH * frame.shape[0] / frame.shape[1]))
    small = cv2.resize(frame, (MOTION_WIDTH, h), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)


def analyse_samples(frames, detector: Detector, cfg: SmartCropConfig,
                    on_sample: Callable[[int], None] | None = None) -> _Samples:
    """Run detection and cut detection over sampled frames, then motion saliency."""
    samples = _Samples()
    prev_sig = None
    for index, (t, frame) in enumerate(frames):
        sig = frame_signature(frame)
        samples.times.append(t)
        samples.detections.append(detector.detect(frame))
        samples.cuts.append(prev_sig is not None and is_cut(prev_sig, sig, cfg.scene_threshold))
        samples.grays.append(_motion_frame(frame))
        prev_sig = sig
        if on_sample:
            on_sample(index)
    if cfg.use_motion:
        add_motion_detections(samples)
    return samples


def add_motion_detections(samples: _Samples, min_scene_samples: int = 3) -> None:
    """Fill samples that have no detections with background-subtraction saliency."""
    motion = MotionSaliency()
    for a, b in segment_bounds(samples.cuts):
        if b - a < min_scene_samples or all(samples.detections[a:b]):
            continue
        background = np.median(np.stack(samples.grays[a:b]), axis=0).astype(np.uint8)
        for i in range(a, b):
            if not samples.detections[i] and (found := motion.detect(background, samples.grays[i])):
                samples.detections[i] = [found]


def _keep_spans(foci: list[Focus | None], axis: str) -> list[tuple[float, float] | None]:
    if axis == "x":
        return [(f.keep.x1, f.keep.x2) if f else None for f in foci]
    return [(f.keep.y1, f.keep.y2) if f else None for f in foci]


def build_plan(samples: _Samples, source: Size, crop: Size, fps: float,
               cfg: SmartCropConfig) -> CropPlan:
    """Pure planning step (no I/O), separated so it can be tested with synthetic detections."""
    extent_x, extent_y = crop.width / source.width, crop.height / source.height
    sigma = cfg.smoothing_seconds * fps
    step_x = cfg.max_pan_per_second / fps
    step_y = cfg.max_pan_per_second * source.width / source.height / fps

    scenes, detected, segments = [], 0, segment_bounds(samples.cuts)
    for a, b in segments:
        foci = track_segment(samples.detections[a:b], extent_x, extent_y)
        detected += sum(f is not None for f in foci)
        xs = build_axis([f.x if f else None for f in foci], _keep_spans(foci, "x"),
                        extent_x, cfg.deadzone, sigma, step_x)
        ys = build_axis([f.y if f else None for f in foci], _keep_spans(foci, "y"),
                        extent_y, cfg.deadzone, sigma, step_y)
        n = b - a
        times = np.asarray(samples.times[a:b], dtype=float)
        start = 0.0 if a == 0 else (samples.times[a - 1] + samples.times[a]) / 2
        scenes.append(SceneTrack(
            start_s=start,
            times=times,
            centers_x=xs if xs is not None else np.full(n, 0.5),
            centers_y=ys if ys is not None else np.full(n, 0.5),
        ))
    if not scenes:
        scenes = [SceneTrack(0.0, np.array([0.0]), np.array([0.5]), np.array([0.5]))]
    total = len(samples.times)
    stats = {
        "samples": total,
        "sample_fps": round(fps, 3),
        "scenes": len(segments),
        "detected_ratio": round(detected / total, 3) if total else 0.0,
    }
    return CropPlan(source, crop, scenes, subject_found=detected > 0, stats=stats)


def plan_smart_crop(path: Path, source: Size, crop: Size, start_s: float, duration_s: float,
                    cfg: SmartCropConfig, detector: Detector,
                    on_progress: Callable[[float], None] | None = None) -> CropPlan:
    sample_plan = SamplePlan.for_clip(source, duration_s, cfg.sample_fps, cfg.max_samples,
                                      cfg.analysis_width)
    expected = max(1, int(duration_s * sample_plan.fps))
    frames = iter_frames(path, start_s, duration_s, sample_plan)
    callback = (lambda i: on_progress(min(1.0, (i + 1) / expected))) if on_progress else None
    samples = analyse_samples(frames, detector, cfg, callback)
    plan = build_plan(samples, source, crop, sample_plan.fps, cfg)
    plan.stats["detector"] = getattr(detector, "name", type(detector).__name__)
    log.debug("smart crop plan: %s", plan.stats)
    return plan
