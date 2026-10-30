// Read the active tab. Only YouTube pages are touched, and only to read the video
// element's playback position/duration and the page title.

import { cleanTitle, parseVideoUrl } from "./youtube";
import type { VideoInfo } from "./types";

export interface ActiveVideo extends VideoInfo {
  tabId: number;
}

export async function getActiveVideo(): Promise<ActiveVideo | null> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return null;
  const info = parseVideoUrl(tab.url);
  if (!info) return null;
  return { ...info, title: cleanTitle(tab.title), tabId: tab.id };
}

export interface PlayerState {
  currentTime: number;
  duration: number | null;
  title: string | null;
}

/** Runs in the page: returns the main <video> element's position and the page title. */
function readPlayer(): PlayerState | null {
  const videos = Array.from(document.querySelectorAll("video"));
  const video = videos.find((v) => v.offsetWidth > 0 && v.offsetHeight > 0) ?? videos[0];
  if (!video) return null;
  const heading = document.querySelector("h1.ytd-watch-metadata, ytd-watch-metadata h1, h1.title");
  return {
    currentTime: video.currentTime,
    duration: Number.isFinite(video.duration) ? video.duration : null,
    title: heading?.textContent?.trim() || null,
  };
}

export async function readPlayerState(tabId: number): Promise<PlayerState | null> {
  try {
    const [result] = await chrome.scripting.executeScript({ target: { tabId }, func: readPlayer });
    return (result?.result as PlayerState | null | undefined) ?? null;
  } catch {
    return null; // page not ready or not scriptable; the form still works with manual times
  }
}

/** Calls `onChange` when the user switches tabs or the active tab navigates. Returns an unsubscribe. */
export function watchActiveTab(onChange: () => void): () => void {
  const activated = () => onChange();
  const updated = (_id: number, change: chrome.tabs.OnUpdatedInfo, tab: chrome.tabs.Tab) => {
    if (tab.active && (change.url || change.title)) onChange();
  };
  chrome.tabs.onActivated.addListener(activated);
  chrome.tabs.onUpdated.addListener(updated);
  return () => {
    chrome.tabs.onActivated.removeListener(activated);
    chrome.tabs.onUpdated.removeListener(updated);
  };
}
