// Format choices shown in the side panel form and the YouTube page toolbar.

import type { AspectRatio, CropMode, Resolution } from "./types";

export interface FormatOption<T extends string> {
  value: T;
  label: string;
  hint?: string;
}

export const RATIO_OPTIONS = [
  { value: "9:16", label: "9:16", hint: "Vertical – Shorts, Reels, TikTok" },
  { value: "16:9", label: "16:9", hint: "Landscape" },
  { value: "1:1", label: "1:1", hint: "Square" },
] as const satisfies readonly FormatOption<AspectRatio>[];

export const MODE_OPTIONS = [
  { value: "smart", label: "Smart", hint: "Follow the detected subject" },
  { value: "center", label: "Center", hint: "Fixed centered crop" },
  { value: "blur", label: "Blur", hint: "Fit the whole frame over a blurred background" },
  { value: "custom", label: "Custom", hint: "Drag a box on the video" },
] as const satisfies readonly FormatOption<CropMode>[];

export const RESOLUTION_OPTIONS = [
  { value: "1080p", label: "1080p" },
  { value: "720p", label: "720p" },
  { value: "480p", label: "480p" },
] as const satisfies readonly FormatOption<Resolution>[];
