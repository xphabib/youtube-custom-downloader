// Messages between the YouTube page toolbar and the background service worker. Content
// scripts cannot use native messaging, so the worker talks to the ytclip host for them.

import type { ClipRequest, Job } from "./types";

export const ADD_CLIP = "ytclip:add-clip";

export interface AddClipMessage {
  type: typeof ADD_CLIP;
  clip: ClipRequest;
  allowDuplicate: boolean;
}

export type AddClipResult =
  | { status: "queued"; job: Job }
  | { status: "saved-offline" }
  | { status: "duplicate"; existingJob: Job }
  | { status: "error"; message: string };

export function isAddClipMessage(value: unknown): value is AddClipMessage {
  return typeof value === "object" && value !== null && (value as { type?: unknown }).type === ADD_CLIP;
}
