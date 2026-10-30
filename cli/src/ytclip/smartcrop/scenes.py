"""Scene-cut detection on sampled frames using colour histogram distance."""

from __future__ import annotations

import cv2
import numpy as np


def frame_signature(frame: np.ndarray) -> np.ndarray:
    """Normalised hue/saturation histogram used to compare frames."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
    return cv2.normalize(hist, hist).flatten()


def is_cut(previous: np.ndarray, current: np.ndarray, threshold: float) -> bool:
    """Bhattacharyya distance is 0 for identical histograms and 1 for disjoint ones."""
    return cv2.compareHist(previous, current, cv2.HISTCMP_BHATTACHARYYA) > threshold


def segment_bounds(cut_flags: list[bool]) -> list[tuple[int, int]]:
    """Turn per-sample "a cut happens before this sample" flags into [start, end) ranges."""
    if not cut_flags:
        return []
    bounds, start = [], 0
    for index, cut in enumerate(cut_flags):
        if cut and index > start:
            bounds.append((start, index))
            start = index
    bounds.append((start, len(cut_flags)))
    return bounds
