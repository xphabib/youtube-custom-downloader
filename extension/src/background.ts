// Service worker: opens the UI in the side panel and queues clips for the page toolbar.

import { isAddClipMessage, type AddClipMessage, type AddClipResult } from "./lib/messages";
import { chromeTransport, HostError, HostUnavailableError, NativeClient } from "./lib/native";
import { chromeStore, ClipStorage, newLocalId } from "./lib/storage";
import { clipKey } from "./lib/validation";

// The side panel stays open while you use the page; a popup closes on any outside click.
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(console.error);

const client = new NativeClient(chromeTransport());
const storage = new ClipStorage(chromeStore());

async function addClip({ clip, allowDuplicate }: AddClipMessage): Promise<AddClipResult> {
  try {
    const { job } = await client.addJob(clip, allowDuplicate);
    return { status: "queued", job };
  } catch (error) {
    if (error instanceof HostError && error.code === "duplicate" && error.existingJob) {
      return { status: "duplicate", existingJob: error.existingJob };
    }
    if (error instanceof HostUnavailableError) {
      if (!allowDuplicate && (await storage.outbox()).some((item) => clipKey(item.clip) === clipKey(clip))) {
        return { status: "error", message: "This clip is already saved and waiting to be sent." };
      }
      await storage.addToOutbox({ localId: newLocalId(), clip, createdAt: new Date().toISOString() });
      return { status: "saved-offline" };
    }
    return { status: "error", message: error instanceof Error ? error.message : String(error) };
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || !isAddClipMessage(message)) return false;
  void addClip(message).then(sendResponse);
  return true; // respond asynchronously
});
