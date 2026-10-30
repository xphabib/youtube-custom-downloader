// Form validation for instant feedback. The native host re-validates everything.

import { parseTimestamp } from "./time";
import type { ClipDraft, ClipRequest, FieldErrors, VideoInfo } from "./types";
import { ASPECT_RATIOS, CROP_MODES, RESOLUTIONS } from "./types";

export const MIN_CLIP_MS = 500;
export const MAX_CLIP_MS = 3 * 60 * 60 * 1000;
export const MAX_OUTPUT_NAME = 150;

export interface ValidationResult {
  errors: FieldErrors;
  durationMs: number | null;
}

export function validateDraft(draft: ClipDraft, videoDurationS?: number | null): ValidationResult {
  const errors: FieldErrors = {};
  const start = parseTimestamp(draft.start);
  const end = parseTimestamp(draft.end);
  if (!draft.start.trim()) errors.start = "Start time is required";
  else if (start === null) errors.start = "Use HH:MM:SS, MM:SS or seconds";
  if (!draft.end.trim()) errors.end = "End time is required";
  else if (end === null) errors.end = "Use HH:MM:SS, MM:SS or seconds";

  let durationMs: number | null = null;
  if (start !== null && end !== null) {
    durationMs = end - start;
    if (end <= start) errors.end = "End time must be after the start time";
    else if (durationMs < MIN_CLIP_MS) errors.end = "Clip must be at least 0.5 seconds";
    else if (durationMs > MAX_CLIP_MS) errors.end = "Clip must be at most 3 hours";
    else if (videoDurationS && end > videoDurationS * 1000 + 500) {
      errors.end = "End time is past the end of the video";
    }
  }
  if (!ASPECT_RATIOS.includes(draft.aspectRatio)) errors.aspectRatio = "Choose an aspect ratio";
  if (!CROP_MODES.includes(draft.cropMode)) errors.cropMode = "Choose a crop mode";
  if (!RESOLUTIONS.includes(draft.resolution)) errors.resolution = "Choose a resolution";
  if (draft.cropMode === "custom" && !draft.cropRect) {
    errors.cropRect = "Drag a box on the video to choose the custom crop area";
  }
  if (draft.outputName.length > MAX_OUTPUT_NAME) {
    errors.outputName = `At most ${MAX_OUTPUT_NAME} characters`;
  } else if (/[/\\]/.test(draft.outputName)) {
    errors.outputName = "A filename cannot contain / or \\";
  }
  return { errors, durationMs: errors.start || errors.end ? null : durationMs };
}

export function isValid(result: ValidationResult): boolean {
  return Object.keys(result.errors).length === 0;
}

export function toRequest(draft: ClipDraft, video: VideoInfo): ClipRequest {
  const request: ClipRequest = {
    url: video.url,
    start: draft.start.trim(),
    end: draft.end.trim(),
    aspectRatio: draft.aspectRatio,
    cropMode: draft.cropMode,
    resolution: draft.resolution,
  };
  if (video.title) request.title = video.title;
  if (draft.cropMode === "custom" && draft.cropRect) request.cropRect = draft.cropRect;
  const name = draft.outputName.trim();
  if (name) request.outputName = name;
  return request;
}

/** Same identity the host uses for duplicate detection (filename excluded). */
export function clipKey(
  clip: Pick<ClipRequest, "url" | "aspectRatio" | "cropMode" | "resolution" | "start" | "end" | "cropRect">,
): string {
  const rect = clip.cropRect ? [clip.cropRect.x, clip.cropRect.y, clip.cropRect.width, clip.cropRect.height].map((v) => v.toFixed(4)) : [];
  return [clip.url, parseTimestamp(clip.start), parseTimestamp(clip.end), clip.aspectRatio, clip.cropMode, clip.resolution, ...rect].join("|");
}
