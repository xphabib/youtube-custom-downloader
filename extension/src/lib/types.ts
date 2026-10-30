// Shared types. Wire formats mirror ytclip/models.py (Job.to_dict) and native/handlers.py.

export const ASPECT_RATIOS = ["9:16", "16:9", "1:1"] as const;
export const CROP_MODES = ["smart", "center", "blur", "custom"] as const;
export const RESOLUTIONS = ["1080p", "720p", "480p"] as const;

export type AspectRatio = (typeof ASPECT_RATIOS)[number];
export type CropMode = (typeof CROP_MODES)[number];
export type Resolution = (typeof RESOLUTIONS)[number];
export type JobStatus = "pending" | "downloading" | "processing" | "completed" | "failed";

/** Custom crop window as fractions (0-1) of the video frame. Mirrors ytclip.models.CropRect. */
export interface CropRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** What the user edits in the form. Times are kept as typed text. */
export interface ClipDraft {
  start: string;
  end: string;
  aspectRatio: AspectRatio;
  cropMode: CropMode;
  resolution: Resolution;
  outputName: string;
  /** Area dragged on the video; used when cropMode is "custom". */
  cropRect?: CropRect | null;
}

/** Payload accepted by the host's validate_clip(). */
export interface ClipRequest {
  url: string;
  title?: string;
  start: string;
  end: string;
  aspectRatio: AspectRatio;
  cropMode: CropMode;
  resolution: Resolution;
  outputName?: string;
  cropRect?: CropRect;
}

export interface Job {
  id: number;
  url: string;
  videoId: string;
  title: string | null;
  startMs: number;
  endMs: number;
  aspectRatio: AspectRatio;
  cropMode: CropMode;
  resolution: Resolution;
  outputName: string | null;
  cropRect: CropRect | null;
  status: JobStatus;
  progress: number;
  stage: string | null;
  outputPath: string | null;
  error: string | null;
  attempts: number;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
}

export type JobCounts = Record<JobStatus, number>;

export interface HostInfo {
  version: string;
  outputDir: string;
  smartCrop: "dnn" | "basic";
}

/** A clip saved while the native host was unreachable; sent on the next connection. */
export interface OutboxItem {
  localId: string;
  clip: ClipRequest;
  createdAt: string;
}

export interface VideoInfo {
  videoId: string;
  url: string;
  title: string | null;
  isShort: boolean;
}

export type FieldErrors = Partial<Record<keyof ClipDraft | "url", string>>;
