// Deliver clips that were saved while the native host was unavailable.

import { HostError, HostUnavailableError, type NativeClient } from "./native";
import type { ClipStorage } from "./storage";
import type { OutboxItem } from "./types";

export interface FlushResult {
  sent: number;
  /** Items the host rejected (e.g. validation); kept so the user can see why. */
  rejected: { item: OutboxItem; message: string }[];
  remaining: OutboxItem[];
}

export async function flushOutbox(client: NativeClient, storage: ClipStorage): Promise<FlushResult> {
  const result: FlushResult = { sent: 0, rejected: [], remaining: [] };
  for (const item of await storage.outbox()) {
    try {
      await client.addJob(item.clip);
      await storage.removeFromOutbox(item.localId);
      result.sent += 1;
    } catch (error) {
      if (error instanceof HostUnavailableError) break;
      if (error instanceof HostError && error.code === "duplicate") {
        await storage.removeFromOutbox(item.localId); // already queued: nothing to deliver
        continue;
      }
      result.rejected.push({ item, message: error instanceof Error ? error.message : String(error) });
    }
  }
  result.remaining = await storage.outbox();
  return result;
}
