"""Subject detectors.

All detectors take a BGR frame and return boxes in *normalised* coordinates (0..1),
so the rest of the pipeline is independent of the analysis resolution.

Two tiers are provided:

* DNN tier (preferred, needs ``ytclip models download``):
  YuNet face detector + YOLOX-S COCO object detector, both run by OpenCV's DNN
  module on the CPU.
* Built-in tier (no download): OpenCV Haar frontal-face cascade + HOG person detector.
  Noticeably less accurate; used automatically when model files are missing. Only
  available with OpenCV 4.x (OpenCV 5 moved both to opencv-contrib).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

log = logging.getLogger(__name__)

# COCO class ids YOLOX reports -> (kind, weight). Unlisted classes are ignored.
YOLOX_CLASSES: dict[int, tuple[str, float]] = {
    0: ("person", 1.0),
    14: ("animal", 0.7), 15: ("animal", 0.7), 16: ("animal", 0.7), 17: ("animal", 0.7),
    18: ("animal", 0.7), 19: ("animal", 0.7), 20: ("animal", 0.7), 21: ("animal", 0.7),
    22: ("animal", 0.7), 23: ("animal", 0.7),
    1: ("object", 0.4), 2: ("object", 0.4), 3: ("object", 0.4), 5: ("object", 0.3),
    32: ("object", 0.5), 37: ("object", 0.4), 62: ("object", 0.3), 63: ("object", 0.3),
}


@dataclass(frozen=True)
class Box:
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def union(self, other: Box) -> Box:
        return Box(min(self.x1, other.x1), min(self.y1, other.y1),
                   max(self.x2, other.x2), max(self.y2, other.y2))

    def iou(self, other: Box) -> float:
        ix = max(0.0, min(self.x2, other.x2) - max(self.x1, other.x1))
        iy = max(0.0, min(self.y2, other.y2) - max(self.y1, other.y1))
        inter = ix * iy
        total = self.area + other.area - inter
        return inter / total if total > 0 else 0.0

    def contains_point(self, x: float, y: float) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2

    def clipped(self) -> Box:
        return Box(*(float(np.clip(v, 0.0, 1.0)) for v in (self.x1, self.y1, self.x2, self.y2)))

    @classmethod
    def from_xywh(cls, x: float, y: float, w: float, h: float, fw: int, fh: int) -> Box:
        return cls(x / fw, y / fh, (x + w) / fw, (y + h) / fh).clipped()


@dataclass(frozen=True)
class Detection:
    box: Box
    score: float
    kind: str  # face | person | animal | object | motion
    weight: float = 1.0


class Detector(Protocol):
    name: str

    def detect(self, frame: np.ndarray) -> list[Detection]: ...


class YuNetFaceDetector:
    name = "yunet"

    def __init__(self, model_path: Path, score_threshold: float = 0.7) -> None:
        self._detector = cv2.FaceDetectorYN.create(str(model_path), "", (320, 320), score_threshold)
        self._size: tuple[int, int] | None = None

    def detect(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        if self._size != (w, h):
            self._detector.setInputSize((w, h))
            self._size = (w, h)
        _, faces = self._detector.detect(frame)
        if faces is None:
            return []
        return [Detection(Box.from_xywh(*f[:4], w, h), float(f[14]), "face") for f in faces]


class YoloxDetector:
    """YOLOX-S (Apache-2.0) exported to ONNX by OpenCV Zoo; fixed 640x640 input."""

    name = "yolox"
    input_size = 640
    strides = (8, 16, 32)

    def __init__(self, model_path: Path, score_threshold: float = 0.45, nms_threshold: float = 0.45) -> None:
        self._net = cv2.dnn.readNet(str(model_path))
        self._score_threshold = score_threshold
        self._nms_threshold = nms_threshold
        self._grids, self._grid_strides = self._make_grids()

    def _make_grids(self) -> tuple[np.ndarray, np.ndarray]:
        grids, strides = [], []
        for stride in self.strides:
            n = self.input_size // stride
            xv, yv = np.meshgrid(np.arange(n), np.arange(n))
            grid = np.stack((xv, yv), axis=2).reshape(-1, 2)
            grids.append(grid)
            strides.append(np.full((grid.shape[0], 1), stride))
        return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)

    def _letterbox(self, frame: np.ndarray) -> tuple[np.ndarray, float]:
        h, w = frame.shape[:2]
        scale = min(self.input_size / h, self.input_size / w)
        resized = cv2.resize(frame, (int(w * scale), int(h * scale)))
        canvas = np.full((self.input_size, self.input_size, 3), 114, np.uint8)
        canvas[: resized.shape[0], : resized.shape[1]] = resized
        return canvas, scale

    def detect(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        canvas, scale = self._letterbox(frame)
        blob = cv2.dnn.blobFromImage(canvas, 1.0, (self.input_size, self.input_size), swapRB=True)
        self._net.setInput(blob)
        out = self._net.forward()[0]
        out[:, :2] = (out[:, :2] + self._grids) * self._grid_strides
        out[:, 2:4] = np.exp(out[:, 2:4]) * self._grid_strides
        scores = out[:, 4:5] * out[:, 5:]
        classes = scores.argmax(axis=1)
        confidences = scores.max(axis=1)
        mask = (confidences >= self._score_threshold) & np.isin(classes, list(YOLOX_CLASSES))
        if not mask.any():
            return []
        boxes = out[mask, :4] / scale
        xywh = [[float(cx - bw / 2), float(cy - bh / 2), float(bw), float(bh)] for cx, cy, bw, bh in boxes]
        confs, cls = confidences[mask], classes[mask]
        keep = cv2.dnn.NMSBoxes(xywh, confs.tolist(), self._score_threshold, self._nms_threshold)
        detections = []
        for i in np.array(keep).flatten():
            kind, weight = YOLOX_CLASSES[int(cls[i])]
            detections.append(Detection(Box.from_xywh(*xywh[i], w, h), float(confs[i]), kind, weight))
        return detections


class HaarFaceDetector:
    name = "haar-face"

    def __init__(self) -> None:
        path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(str(path))
        if self._cascade.empty():
            raise RuntimeError(f"Could not load Haar cascade from {path}")

    def detect(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        min_side = max(24, w // 30)
        faces = self._cascade.detectMultiScale(gray, 1.15, 6, minSize=(min_side, min_side))
        return [Detection(Box.from_xywh(*f, w, h), 0.8, "face") for f in faces]


class HogPersonDetector:
    name = "hog-person"

    def __init__(self) -> None:
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        rects, weights = self._hog.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.06)
        out = []
        for rect, weight in zip(rects, np.ravel(weights)):
            if weight > 0.5:
                out.append(Detection(Box.from_xywh(*rect, w, h), float(min(1.0, weight / 2)), "person"))
        return out


class CompositeDetector:
    def __init__(self, detectors: list[Detector]) -> None:
        self.detectors = detectors
        self.name = "+".join(d.name for d in detectors) or "none"

    def detect(self, frame: np.ndarray) -> list[Detection]:
        return [det for d in self.detectors for det in d.detect(frame)]


class MotionSaliency:
    """Weak fallback signal: the largest region that differs from the scene background.

    The background is the per-pixel median of the scene's samples, so a moving subject
    is located where it *is* (plain frame differencing would also mark where it was).
    """

    def __init__(self, min_area: float = 0.002, max_area: float = 0.6) -> None:
        self._min_area = min_area
        self._max_area = max_area

    def detect(self, background: np.ndarray, current: np.ndarray) -> Detection | None:
        """Both inputs are small blurred grayscale frames of identical size."""
        h, w = current.shape[:2]
        diff = cv2.absdiff(background, current)
        _, mask = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=2)
        moving = cv2.countNonZero(mask) / (w * h)
        if not self._min_area <= moving <= self._max_area:
            return None  # nothing moves, or the whole frame does (camera pan / cut)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        x, y, bw, bh = cv2.boundingRect(max(contours, key=cv2.contourArea))
        return Detection(Box.from_xywh(x, y, bw, bh, w, h), 0.5, "motion", 0.35)


def _quiet_opencv() -> None:
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except AttributeError:
        pass


def builtin_detectors_available() -> bool:
    """Haar/HOG ship with OpenCV 4.x but were moved to opencv-contrib in OpenCV 5."""
    return hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "HOGDescriptor")


def build_detector(models_dir: Path) -> CompositeDetector:
    """Prefer the DNN models when present, fall back to built-in OpenCV detectors.

    With neither available the composite is empty and the planner relies on motion
    saliency alone (if enabled) before applying the configured fallback.
    """
    from ytclip.modelstore import model_path  # local import avoids a cycle at import time

    _quiet_opencv()
    builtin = builtin_detectors_available()
    face, objects = model_path(models_dir, "yunet"), model_path(models_dir, "yolox")
    detectors: list[Detector] = []
    for path, dnn_cls, builtin_cls in ((face, YuNetFaceDetector, HaarFaceDetector),
                                       (objects, YoloxDetector, HogPersonDetector)):
        try:
            if path:
                detectors.append(dnn_cls(path))
                continue
        except cv2.error as exc:
            log.warning("Could not load %s (%s); using built-in detector", path, exc)
        if builtin:
            detectors.append(builtin_cls())
    return CompositeDetector(detectors)
