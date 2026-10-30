// Recognise supported YouTube video pages. Mirrors ytclip/youtube.py.

import type { VideoInfo } from "./types";

const VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;
const YOUTUBE_HOSTS = new Set(["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"]);
const SHORT_HOSTS = new Set(["youtu.be", "www.youtu.be"]);
const PATH_PREFIXES = ["/shorts/", "/live/", "/embed/"];

export function parseVideoUrl(raw: string | undefined | null): VideoInfo | null {
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  const host = url.hostname.toLowerCase();
  let id: string | null = null;
  let isShort = false;
  if (SHORT_HOSTS.has(host)) {
    id = url.pathname.slice(1).split("/")[0] ?? null;
  } else if (YOUTUBE_HOSTS.has(host)) {
    if (url.pathname === "/watch") {
      id = url.searchParams.get("v");
    } else {
      const prefix = PATH_PREFIXES.find((p) => url.pathname.startsWith(p));
      if (prefix) {
        id = url.pathname.slice(prefix.length).split("/")[0] ?? null;
        isShort = prefix === "/shorts/";
      }
    }
  }
  if (!id || !VIDEO_ID_RE.test(id)) return null;
  return { videoId: id, url: `https://www.youtube.com/watch?v=${id}`, title: null, isShort };
}

/** Strip YouTube's tab-title decorations: "(3) Some title - YouTube" -> "Some title". */
export function cleanTitle(tabTitle: string | undefined | null): string | null {
  if (!tabTitle) return null;
  const title = tabTitle
    .replace(/^\(\d+\+?\)\s*/, "")
    .replace(/\s*-\s*YouTube( Music)?$/, "")
    .trim();
  return title && title !== "YouTube" ? title.slice(0, 300) : null;
}

export function thumbnailUrl(videoId: string): string {
  return `https://i.ytimg.com/vi/${videoId}/mqdefault.jpg`;
}
