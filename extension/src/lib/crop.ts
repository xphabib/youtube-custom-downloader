// Geometry for the custom crop box drawn on the YouTube player. Rects are fractions (0-1)
// of the video frame. The box keeps the output aspect ratio in real pixels, so in
// fractions its height is width * frameAspect / targetAspect.

import type { AspectRatio, CropRect } from "./types";

export type Corner = "nw" | "ne" | "sw" | "se";

/** Smallest box, as a fraction of the frame's width or height. */
export const MIN_FRACTION = 0.05;

export function aspectValue(aspect: AspectRatio): number {
  const [w, h] = aspect.split(":").map(Number) as [number, number];
  return w / h;
}

const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
const round = (value: number) => Math.round(value * 10_000) / 10_000;

function tidy(rect: CropRect): CropRect {
  const width = round(clamp(rect.width, 0, 1));
  const height = round(clamp(rect.height, 0, 1));
  return { x: round(clamp(rect.x, 0, 1 - width)), y: round(clamp(rect.y, 0, 1 - height)), width, height };
}

/** Largest box of the target aspect that fits the frame, centred (same as center crop). */
export function defaultRect(frameAspect: number, target: number): CropRect {
  const width = Math.min(1, target / frameAspect);
  const height = Math.min(1, frameAspect / target);
  return tidy({ x: (1 - width) / 2, y: (1 - height) / 2, width, height });
}

/** Reshape a box to a new target aspect around its centre, keeping its height where possible. */
export function fitAspect(rect: CropRect, frameAspect: number, target: number): CropRect {
  let height = rect.height;
  let width = (height * target) / frameAspect;
  if (width > 1) {
    width = 1;
    height = frameAspect / target;
  }
  if (height > 1) {
    height = 1;
    width = target / frameAspect;
  }
  const cx = rect.x + rect.width / 2;
  const cy = rect.y + rect.height / 2;
  return tidy({ x: cx - width / 2, y: cy - height / 2, width, height });
}

/** Move a box by a delta, keeping it inside the frame. */
export function moveRect(rect: CropRect, dx: number, dy: number): CropRect {
  return tidy({ ...rect, x: rect.x + dx, y: rect.y + dy });
}

/**
 * Resize by dragging `corner` to the pointer (fractions of the frame). The opposite corner
 * stays put, the aspect stays locked, and the box never leaves the frame.
 */
export function resizeRect(
  rect: CropRect, corner: Corner, px: number, py: number, frameAspect: number, target: number,
): CropRect {
  const east = corner === "ne" || corner === "se";
  const south = corner === "sw" || corner === "se";
  const ax = east ? rect.x : rect.x + rect.width;
  const ay = south ? rect.y : rect.y + rect.height;
  const ratio = frameAspect / target; // height per unit of width, in fractions
  const wanted = Math.max(Math.abs(px - ax), Math.abs(py - ay) / ratio);
  const maxWidth = Math.min(east ? 1 - ax : ax, (south ? 1 - ay : ay) / ratio);
  const minWidth = Math.min(maxWidth, Math.max(MIN_FRACTION, MIN_FRACTION / ratio));
  const width = clamp(wanted, minWidth, maxWidth);
  const height = width * ratio;
  return tidy({ x: east ? ax : ax - width, y: south ? ay : ay - height, width, height });
}

/** Where the picture sits inside a video element (object-fit: contain), in element pixels. */
export function contentBox(boxWidth: number, boxHeight: number, videoWidth: number, videoHeight: number) {
  if (!videoWidth || !videoHeight || !boxWidth || !boxHeight) {
    return { left: 0, top: 0, width: boxWidth, height: boxHeight };
  }
  const scale = Math.min(boxWidth / videoWidth, boxHeight / videoHeight);
  const width = videoWidth * scale;
  const height = videoHeight * scale;
  return { left: (boxWidth - width) / 2, top: (boxHeight - height) / 2, width, height };
}
