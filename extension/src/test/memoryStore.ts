import type { KeyValueStore } from "../lib/storage";

/** In-memory chrome.storage stand-in; values are cloned like real storage. */
export function memoryStore(): KeyValueStore & { data: Map<string, unknown> } {
  const data = new Map<string, unknown>();
  return {
    data,
    async get(key) {
      return structuredClone(data.get(key));
    },
    async set(key, value) {
      data.set(key, structuredClone(value));
    },
  };
}
