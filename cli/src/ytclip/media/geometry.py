"""Pure geometry for output canvases and crop windows. All sizes are even (yuv420p)."""

from __future__ import annotations

from dataclasses import dataclass

from ytclip.models import AspectRatio, CropRect, Resolution


def even(value: float) -> int:
    """Round to the nearest even integer, never below 2."""
    return max(2, int(round(value / 2)) * 2)


def even_floor(value: float) -> int:
    return max(2, int(value) // 2 * 2)


@dataclass(frozen=True)
class Size:
    width: int
    height: int

    @property
    def aspect(self) -> float:
        return self.width / self.height


def output_size(aspect: AspectRatio, resolution: Resolution) -> Size:
    """Final canvas size: ``resolution`` is the length of the short side."""
    rw, rh = aspect.ratio
    short = resolution.short_side
    if rw == rh:
        return Size(short, short)
    long_side = even(short * max(rw, rh) / min(rw, rh))
    return Size(short, long_side) if rw < rh else Size(long_side, short)


def crop_size(source: Size, aspect: AspectRatio) -> Size:
    """Largest even window with the target aspect that fits inside ``source``."""
    rw, rh = aspect.ratio
    target = rw / rh
    if source.aspect > target:
        height = even_floor(source.height)
        width = min(even(height * target), even_floor(source.width))
    else:
        width = even_floor(source.width)
        height = min(even(width / target), even_floor(source.height))
    return Size(width, height)


def custom_window(source: Size, rect: CropRect, aspect: AspectRatio) -> tuple[Size, int, int]:
    """The largest even window with the target aspect inside ``rect``, centred on it."""
    left, top = rect.x * source.width, rect.y * source.height
    region = Size(max(2, int(rect.width * source.width)), max(2, int(rect.height * source.height)))
    crop = crop_size(region, aspect)
    x = int(clamp(left + (region.width - crop.width) / 2, 0, source.width - crop.width))
    y = int(clamp(top + (region.height - crop.height) / 2, 0, source.height - crop.height))
    return crop, x, y


def center_offset(source: Size, crop: Size) -> tuple[int, int]:
    return (source.width - crop.width) // 2, (source.height - crop.height) // 2


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
