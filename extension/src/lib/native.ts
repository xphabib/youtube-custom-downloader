// Client for the ytclip native messaging host (see cli/src/ytclip/native/handlers.py).

import type { ClipRequest, HostInfo, Job, JobCounts } from "./types";

export const HOST_NAME = "com.ytclip.host";

/** Sends one message and resolves with the raw response. */
export type Transport = (message: object) => Promise<unknown>;

export class HostUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "HostUnavailableError";
  }
}

export class HostError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly field?: string,
    readonly existingJob?: Job,
  ) {
    super(message);
    this.name = "HostError";
  }
}

const UNAVAILABLE_PATTERNS = [
  /native messaging host not found/i,
  /access to the specified native messaging host is forbidden/i,
  /native host has exited/i,
  /error when communicating with the native messaging host/i,
  /failed to start native messaging host/i,
];

export function chromeTransport(): Transport {
  return (message) =>
    new Promise((resolve, reject) => {
      chrome.runtime.sendNativeMessage(HOST_NAME, message, (response: unknown) => {
        const lastError = chrome.runtime.lastError;
        if (lastError) {
          const text = lastError.message ?? "Native host error";
          reject(UNAVAILABLE_PATTERNS.some((p) => p.test(text)) ? new HostUnavailableError(text) : new Error(text));
          return;
        }
        resolve(response);
      });
    });
}

interface WireError {
  code: string;
  message: string;
  field?: string;
  existingJob?: Job;
}

type WireResponse<T> = { id: string | null; ok: true; data: T } | { id: string | null; ok: false; error: WireError };

function isWireResponse(value: unknown): value is WireResponse<unknown> {
  return typeof value === "object" && value !== null && "ok" in value;
}

let counter = 0;

export class NativeClient {
  constructor(private readonly transport: Transport) {}

  async request<T>(type: string, payload: object = {}): Promise<T> {
    const id = `${Date.now().toString(36)}-${++counter}`;
    let response: unknown;
    try {
      response = await this.transport({ id, type, payload });
    } catch (error) {
      if (error instanceof HostUnavailableError || error instanceof Error) throw error;
      throw new Error(String(error));
    }
    if (response === undefined || response === null) {
      throw new HostUnavailableError("The ytclip host closed without replying");
    }
    if (!isWireResponse(response)) throw new HostError("Malformed response from ytclip host", "protocol");
    if (!response.ok) {
      const { message, code, field, existingJob } = response.error;
      throw new HostError(message, code, field, existingJob);
    }
    return response.data as T;
  }

  ping() {
    return this.request<HostInfo>("ping");
  }

  listJobs(limit = 50) {
    return this.request<{ jobs: Job[]; counts: JobCounts }>("list_jobs", { limit });
  }

  addJob(clip: ClipRequest, allowDuplicate = false) {
    return this.request<{ job: Job }>("add_job", { clip, allowDuplicate });
  }

  updateJob(id: number, clip: ClipRequest, allowDuplicate = false) {
    return this.request<{ job: Job }>("update_job", { id, clip, allowDuplicate });
  }

  removeJob(id: number) {
    return this.request<{ removed: number }>("remove_job", { id });
  }

  retryJob(id: number) {
    return this.request<{ job: Job }>("retry_job", { id });
  }

  /** Removes every job that is not currently being processed. */
  clearJobs() {
    return this.request<{ removed: number }>("clear_jobs");
  }

  /** Starts `ytclip run` in the background to download and render pending clips. */
  startRun() {
    return this.request<{ started: boolean; alreadyRunning: boolean }>("start_run");
  }
}
