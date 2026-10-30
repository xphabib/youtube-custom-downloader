// Persistence in chrome.storage.local: form drafts, format preferences, the offline
// outbox and a cached copy of the queue. SQLite in the host stays the source of truth.

import type { AspectRatio, ClipDraft, CropMode, Job, JobCounts, OutboxItem, Resolution } from "./types";

export interface KeyValueStore {
  get(key: string): Promise<unknown>;
  set(key: string, value: unknown): Promise<void>;
}

export function chromeStore(): KeyValueStore {
  return {
    async get(key) {
      const result = await chrome.storage.local.get(key);
      return result[key];
    },
    async set(key, value) {
      await chrome.storage.local.set({ [key]: value });
    },
  };
}

export interface Preferences {
  aspectRatio: AspectRatio;
  cropMode: CropMode;
  resolution: Resolution;
}

export const DEFAULT_PREFERENCES: Preferences = { aspectRatio: "9:16", cropMode: "smart", resolution: "1080p" };

export interface StoredDraft {
  videoId: string;
  draft: ClipDraft;
  editingJobId: number | null;
}

/** A draft change made with the toolbar injected into the YouTube page. */
export interface PageMark {
  videoId: string;
  patch: Partial<ClipDraft>;
  at: number;
}

export interface CachedQueue {
  jobs: Job[];
  counts: JobCounts | null;
  savedAt: string;
}

export const KEYS = {
  draft: "draft",
  prefs: "preferences",
  outbox: "outbox",
  queue: "queueCache",
  mark: "pageMark",
} as const;

export class ClipStorage {
  constructor(private readonly store: KeyValueStore) {}

  async preferences(): Promise<Preferences> {
    const stored = (await this.store.get(KEYS.prefs)) as Partial<Preferences> | undefined;
    return { ...DEFAULT_PREFERENCES, ...stored };
  }

  savePreferences(prefs: Preferences) {
    return this.store.set(KEYS.prefs, prefs);
  }

  async draft(): Promise<StoredDraft | null> {
    return ((await this.store.get(KEYS.draft)) as StoredDraft | undefined) ?? null;
  }

  saveDraft(draft: StoredDraft | null) {
    return this.store.set(KEYS.draft, draft);
  }

  /** The saved draft for this video, or a fresh one from the format preferences. */
  async draftFor(videoId: string): Promise<StoredDraft> {
    const [stored, prefs] = await Promise.all([this.draft(), this.preferences()]);
    if (stored?.videoId === videoId) return stored;
    return { videoId, draft: { start: "", end: "", outputName: "", ...prefs }, editingJobId: null };
  }

  /**
   * Applies a change made on the YouTube page: writes it into the saved draft (starting a
   * fresh one for a different video) and publishes it so an open side panel can apply it.
   */
  async patchDraft(videoId: string, patch: Partial<ClipDraft>): Promise<StoredDraft> {
    const base = await this.draftFor(videoId);
    const next = { ...base, draft: { ...base.draft, ...patch } };
    await this.saveDraft(next);
    if ("aspectRatio" in patch || "cropMode" in patch || "resolution" in patch) {
      const { aspectRatio, cropMode, resolution } = next.draft;
      await this.savePreferences({ aspectRatio, cropMode, resolution });
    }
    await this.store.set(KEYS.mark, { videoId, patch, at: Date.now() } satisfies PageMark);
    return next;
  }

  async outbox(): Promise<OutboxItem[]> {
    return ((await this.store.get(KEYS.outbox)) as OutboxItem[] | undefined) ?? [];
  }

  async addToOutbox(item: OutboxItem): Promise<OutboxItem[]> {
    const items = [...(await this.outbox()), item];
    await this.store.set(KEYS.outbox, items);
    return items;
  }

  async removeFromOutbox(localId: string): Promise<OutboxItem[]> {
    const items = (await this.outbox()).filter((i) => i.localId !== localId);
    await this.store.set(KEYS.outbox, items);
    return items;
  }

  async clearOutbox(): Promise<OutboxItem[]> {
    await this.store.set(KEYS.outbox, []);
    return [];
  }

  async queue(): Promise<CachedQueue | null> {
    return ((await this.store.get(KEYS.queue)) as CachedQueue | undefined) ?? null;
  }

  saveQueue(jobs: Job[], counts: JobCounts | null) {
    return this.store.set(KEYS.queue, { jobs, counts, savedAt: new Date().toISOString() });
  }
}

export function newLocalId(): string {
  return `local-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
