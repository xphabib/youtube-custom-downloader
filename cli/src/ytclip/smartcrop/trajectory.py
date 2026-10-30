"""Turn noisy per-sample targets into a smooth, bounded crop-centre path (one axis).

Pipeline per axis, in normalised source units:

1. ``fill_gaps``      – interpolate samples with no detection; hold at the ends.
2. ``apply_deadzone`` – ignore small subject motion so the virtual camera stays still.
3. ``gaussian_smooth``– zero-phase smoothing (no lag, unlike a one-sided EMA).
4. ``limit_velocity`` – cap pan speed so sudden subject jumps become gentle pans.
5. ``enforce_keep``   – nudge the window so the face/group stays inside when it fits.
6. ``clamp_center``   – keep the window inside the frame.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def fill_gaps(values: Sequence[float | None]) -> np.ndarray | None:
    known = [i for i, v in enumerate(values) if v is not None]
    if not known:
        return None
    xs = np.arange(len(values), dtype=float)
    return np.interp(xs, known, [values[i] for i in known])


def apply_deadzone(values: np.ndarray, deadzone: float) -> np.ndarray:
    """Hysteresis: the anchor only moves once the target leaves ``anchor ± deadzone``."""
    if len(values) == 0 or deadzone <= 0:
        return values.copy()
    out = np.empty_like(values)
    anchor = values[0]
    for i, v in enumerate(values):
        if v > anchor + deadzone:
            anchor = v - deadzone
        elif v < anchor - deadzone:
            anchor = v + deadzone
        out[i] = anchor
    return out


def gaussian_smooth(values: np.ndarray, sigma: float) -> np.ndarray:
    if len(values) < 3 or sigma <= 0:
        return values.copy()
    radius = min(max(1, int(round(sigma * 3))), len(values) - 1)
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    kernel /= kernel.sum()
    # Odd reflection extrapolates the local trend, so smoothing doesn't flatten the ends.
    padded = np.pad(values, radius, mode="reflect", reflect_type="odd")
    return np.convolve(padded, kernel, mode="valid")


def limit_velocity(values: np.ndarray, max_step: float) -> np.ndarray:
    """Bound |v[i+1]-v[i]| by ``max_step``.

    A single forward pass lags behind jumps. Averaging a forward and a backward pass
    instead yields a pan centred on the jump (starting slightly early, ending slightly
    late, at up to ``max_step``). Near the clip edges a pan may begin "before" the clip,
    so the first frames can be marginally off-target; ``enforce_keep`` still applies.
    """
    if len(values) < 2 or max_step <= 0:
        return values.copy()

    def one_pass(seq: np.ndarray) -> np.ndarray:
        out = seq.copy()
        for i in range(1, len(out)):
            out[i] = out[i - 1] + np.clip(seq[i] - out[i - 1], -max_step, max_step)
        return out

    return (one_pass(values) + one_pass(values[::-1])[::-1]) / 2


def enforce_keep(centers: np.ndarray, keep_ranges: Sequence[tuple[float, float] | None],
                 half_extent: float, margin: float = 0.01) -> np.ndarray:
    """Shift centres so each ``(lo, hi)`` interval lies within ``center ± half_extent``."""
    out = centers.copy()
    for i, span in enumerate(keep_ranges):
        if span is None:
            continue
        lo_edge, hi_edge = span
        lowest = hi_edge - half_extent + margin
        highest = lo_edge + half_extent - margin
        if lowest <= highest:  # the region fits inside the window
            out[i] = min(max(out[i], lowest), highest)
    return out


def clamp_center(values: np.ndarray, half_extent: float) -> np.ndarray:
    return np.clip(values, half_extent, 1 - half_extent)


def build_axis(targets: Sequence[float | None], keep_ranges: Sequence[tuple[float, float] | None],
               extent: float, deadzone: float, sigma: float, max_step: float) -> np.ndarray | None:
    """Full per-axis pipeline. ``extent`` is the crop size as a fraction of the frame."""
    half = extent / 2
    if extent >= 0.999:
        return np.full(len(targets), 0.5)
    raw = fill_gaps(targets)
    if raw is None:
        return None
    path = apply_deadzone(raw, deadzone * extent)
    path = gaussian_smooth(path, sigma)
    path = limit_velocity(path, max_step)
    path = enforce_keep(path, keep_ranges, half)
    return clamp_center(path, half)
