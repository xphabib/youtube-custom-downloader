import { describe, expect, it } from "vitest";
import { memoryStore } from "../test/memoryStore";
import { ClipStorage } from "./storage";
import { aspectValue, contentBox, defaultRect, fitAspect, moveRect, resizeRect } from "./crop";
import { formatDuration, formatTimestamp, parseTimestamp, secondsToTimestamp } from "./time";
import { clipKey, isValid, toRequest, validateDraft } from "./validation";
import { cleanTitle, parseVideoUrl } from "./youtube";
import type { ClipDraft } from "./types";

describe("parseTimestamp", () => {
  it.each([
    ["90", 90_000],
    ["1:20", 80_000],
    ["00:01:20", 80_000],
    ["01:02:03", 3_723_000],
    ["75:00", 4_500_000],
    ["00:01:20.5", 80_500],
    [" 5 ", 5_000],
  ])("parses %s", (input, expected) => {
    expect(parseTimestamp(input)).toBe(expected);
  });

  it.each(["", "abc", "1:75", "1:60:00", "1:2:3:4", "-5", "1::2", "00:01:20.1234"])("rejects %s", (input) => {
    expect(parseTimestamp(input)).toBeNull();
  });

  it("formats and round-trips", () => {
    expect(formatTimestamp(80_000)).toBe("00:01:20");
    expect(formatTimestamp(80_500)).toBe("00:01:20.500");
    expect(parseTimestamp(formatTimestamp(3_723_250))).toBe(3_723_250);
    expect(secondsToTimestamp(83.9)).toBe("00:01:23");
    expect(formatDuration(8_500)).toBe("8.5s");
    expect(formatDuration(85_000)).toBe("1m 25s");
  });
});

describe("parseVideoUrl", () => {
  it.each([
    ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", false],
    ["https://m.youtube.com/watch?v=dQw4w9WgXcQ", false],
    ["https://youtu.be/dQw4w9WgXcQ?si=abc", false],
    ["https://www.youtube.com/shorts/dQw4w9WgXcQ", true],
    ["https://www.youtube.com/live/dQw4w9WgXcQ", false],
  ])("accepts %s", (url, isShort) => {
    expect(parseVideoUrl(url)).toEqual({
      videoId: "dQw4w9WgXcQ",
      url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
      title: null,
      isShort,
    });
  });

  it.each([
    "https://www.youtube.com/",
    "https://www.youtube.com/@channel",
    "https://www.youtube.com/watch?v=short",
    "https://evil.example/watch?v=dQw4w9WgXcQ",
    "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
    "javascript:alert(1)",
    "chrome://extensions",
    undefined,
  ])("rejects %s", (url) => {
    expect(parseVideoUrl(url)).toBeNull();
  });

  it("cleans tab titles", () => {
    expect(cleanTitle("(3) My Great Video - YouTube")).toBe("My Great Video");
    expect(cleanTitle("YouTube")).toBeNull();
    expect(cleanTitle(undefined)).toBeNull();
  });
});

describe("validateDraft", () => {
  const base: ClipDraft = {
    start: "00:01:20",
    end: "00:02:45",
    aspectRatio: "9:16",
    cropMode: "smart",
    resolution: "1080p",
    outputName: "",
  };

  it("accepts a valid clip and reports its duration", () => {
    const result = validateDraft(base);
    expect(isValid(result)).toBe(true);
    expect(result.durationMs).toBe(85_000);
  });

  it("requires end after start", () => {
    expect(validateDraft({ ...base, end: "00:01:00" }).errors.end).toMatch(/after the start/);
    expect(validateDraft({ ...base, end: base.start }).errors.end).toMatch(/after the start/);
  });

  it("flags malformed and missing times", () => {
    const result = validateDraft({ ...base, start: "1:99", end: "" });
    expect(result.errors.start).toBeDefined();
    expect(result.errors.end).toMatch(/required/);
    expect(result.durationMs).toBeNull();
  });

  it("checks against the known video duration", () => {
    expect(validateDraft(base, 100).errors.end).toMatch(/past the end/);
    expect(isValid(validateDraft(base, 200))).toBe(true);
  });

  it("rejects path separators in the filename", () => {
    expect(validateDraft({ ...base, outputName: "../x" }).errors.outputName).toBeDefined();
  });

  it("builds requests and duplicate keys independent of formatting", () => {
    const video = { videoId: "dQw4w9WgXcQ", url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ", title: "T", isShort: false };
    const request = toRequest({ ...base, outputName: "  name " }, video);
    expect(request).toMatchObject({ title: "T", outputName: "name", start: "00:01:20" });
    expect(clipKey(request)).toBe(clipKey({ ...request, start: "1:20", end: "165" }));
    expect(clipKey(request)).not.toBe(clipKey({ ...request, cropMode: "blur" }));
  });
});

describe("ClipStorage.patchDraft", () => {
  it("starts a fresh draft for a new video, then fills the other field", async () => {
    const store = memoryStore();
    store.data.set("preferences", { aspectRatio: "1:1", cropMode: "blur", resolution: "720p" });
    store.data.set("draft", { videoId: "otherVideo1", draft: { start: "00:00:01" }, editingJobId: 4 });
    const storage = new ClipStorage(store);

    await storage.patchDraft("dQw4w9WgXcQ", { start: "00:01:00" });
    const next = await storage.patchDraft("dQw4w9WgXcQ", { end: "00:01:30" });
    expect(next).toEqual({
      videoId: "dQw4w9WgXcQ",
      draft: { start: "00:01:00", end: "00:01:30", outputName: "", aspectRatio: "1:1", cropMode: "blur", resolution: "720p" },
      editingJobId: null,
    });
    expect(store.data.get("pageMark")).toMatchObject({ videoId: "dQw4w9WgXcQ", patch: { end: "00:01:30" } });
  });

  it("remembers formats picked on the page as preferences", async () => {
    const store = memoryStore();
    const storage = new ClipStorage(store);
    await storage.patchDraft("dQw4w9WgXcQ", { aspectRatio: "16:9" });
    expect(await storage.preferences()).toEqual({ aspectRatio: "16:9", cropMode: "smart", resolution: "1080p" });
  });
});

describe("custom crop geometry", () => {
  const WIDE = 16 / 9;
  const pixelAspect = (r: { width: number; height: number }) => (r.width * 1920) / (r.height * 1080);

  it("starts as the centred largest box", () => {
    const rect = defaultRect(WIDE, 9 / 16);
    expect(rect.height).toBe(1);
    expect(pixelAspect(rect)).toBeCloseTo(9 / 16, 2);
    expect(rect.x + rect.width / 2).toBeCloseTo(0.5, 3);
    expect(defaultRect(WIDE, WIDE)).toEqual({ x: 0, y: 0, width: 1, height: 1 });
  });

  it("resizes from a corner with the aspect locked and stays inside the frame", () => {
    const start = defaultRect(WIDE, 9 / 16);
    const smaller = resizeRect(start, "se", start.x + 0.1, 0.5, WIDE, 9 / 16);
    expect(smaller.x).toBe(start.x);
    expect(smaller.y).toBe(0);
    expect(pixelAspect(smaller)).toBeCloseTo(9 / 16, 2);
    const huge = resizeRect(smaller, "se", 5, 5, WIDE, 9 / 16);
    expect(huge.y + huge.height).toBeLessThanOrEqual(1);
    expect(huge.x + huge.width).toBeLessThanOrEqual(1);
    const tiny = resizeRect(smaller, "nw", 1, 1, WIDE, 9 / 16);
    expect(tiny.width).toBeGreaterThan(0.02);
  });

  it("moves within the frame and refits to a new aspect around the centre", () => {
    const rect = { x: 0.4, y: 0.2, width: 0.2, height: 0.5 };
    expect(moveRect(rect, 1, -1)).toEqual({ x: 0.8, y: 0, width: 0.2, height: 0.5 });
    const square = fitAspect(rect, WIDE, 1);
    expect(pixelAspect(square)).toBeCloseTo(1, 2);
    expect(square.x + square.width / 2).toBeCloseTo(0.5, 3);
  });

  it("finds the picture inside a letterboxed player", () => {
    const box = contentBox(1000, 1000, 1920, 1080);
    expect(box.left).toBeCloseTo(0);
    expect(box.top).toBeCloseTo(218.75);
    expect(box.width).toBeCloseTo(1000);
    expect(box.height).toBeCloseTo(562.5);
    expect(aspectValue("9:16")).toBeCloseTo(0.5625);
  });
});

describe("custom crop validation", () => {
  const draft: ClipDraft = { start: "0", end: "5", aspectRatio: "9:16", cropMode: "custom", resolution: "1080p", outputName: "" };
  const video = { videoId: "dQw4w9WgXcQ", url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ", title: null, isShort: false };

  it("needs an area and sends it only in custom mode", () => {
    expect(validateDraft(draft).errors.cropRect).toMatch(/Drag a box/);
    const rect = { x: 0.1, y: 0, width: 0.3, height: 1 };
    expect(isValid(validateDraft({ ...draft, cropRect: rect }))).toBe(true);
    expect(toRequest({ ...draft, cropRect: rect }, video).cropRect).toEqual(rect);
    expect(toRequest({ ...draft, cropMode: "center", cropRect: rect }, video).cropRect).toBeUndefined();
  });
});
