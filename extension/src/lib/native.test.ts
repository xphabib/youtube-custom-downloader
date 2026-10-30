import { describe, expect, it, vi } from "vitest";
import { HostError, HostUnavailableError, NativeClient, type Transport } from "./native";
import { flushOutbox } from "./outbox";
import { ClipStorage } from "./storage";
import { memoryStore } from "../test/memoryStore";
import type { ClipRequest, Job } from "./types";

const clip: ClipRequest = {
  url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  start: "00:00:10",
  end: "00:00:20",
  aspectRatio: "9:16",
  cropMode: "smart",
  resolution: "1080p",
};

describe("NativeClient", () => {
  it("sends typed requests and unwraps data", async () => {
    const transport = vi.fn<Transport>(async (message) => ({
      id: (message as { id: string }).id,
      ok: true,
      data: { version: "0.1.0", outputDir: "/out", smartCrop: "dnn" },
    }));
    const info = await new NativeClient(transport).ping();
    expect(info.version).toBe("0.1.0");
    expect(transport.mock.calls[0]?.[0]).toMatchObject({ type: "ping", payload: {} });
  });

  it("passes allowDuplicate and surfaces duplicate errors with the existing job", async () => {
    const existing = { id: 7, status: "pending" } as Job;
    const transport: Transport = async (message) => {
      expect((message as { payload: { allowDuplicate: boolean } }).payload.allowDuplicate).toBe(false);
      return { id: null, ok: false, error: { code: "duplicate", message: "dup", existingJob: existing } };
    };
    const error = await new NativeClient(transport).addJob(clip).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(HostError);
    expect((error as HostError).code).toBe("duplicate");
    expect((error as HostError).existingJob?.id).toBe(7);
  });

  it("maps validation errors to their field", async () => {
    const transport: Transport = async () => ({
      id: null,
      ok: false,
      error: { code: "validation", message: "End time must be greater", field: "end" },
    });
    const error = (await new NativeClient(transport).addJob(clip).catch((e: unknown) => e)) as HostError;
    expect(error.field).toBe("end");
  });

  it("treats a missing reply as an unavailable host", async () => {
    const error = await new NativeClient(async () => undefined).ping().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(HostUnavailableError);
  });

  it("rejects malformed responses", async () => {
    const error = (await new NativeClient(async () => "nope").ping().catch((e: unknown) => e)) as HostError;
    expect(error.code).toBe("protocol");
  });
});

describe("outbox", () => {
  it("delivers saved clips, drops duplicates, keeps rejected ones", async () => {
    const storage = new ClipStorage(memoryStore());
    for (const [localId, start] of [["a", "1"], ["b", "2"], ["c", "3"]] as const) {
      await storage.addToOutbox({ localId, clip: { ...clip, start }, createdAt: "" });
    }
    const transport: Transport = async (message) => {
      const start = (message as { payload: { clip: ClipRequest } }).payload.clip.start;
      if (start === "2") return { id: null, ok: false, error: { code: "duplicate", message: "dup" } };
      if (start === "3") return { id: null, ok: false, error: { code: "validation", message: "bad", field: "end" } };
      return { id: null, ok: true, data: { job: { id: 1 } } };
    };
    const result = await flushOutbox(new NativeClient(transport), storage);
    expect(result.sent).toBe(1);
    expect(result.rejected.map((r) => r.item.localId)).toEqual(["c"]);
    expect(result.remaining.map((i) => i.localId)).toEqual(["c"]);
  });

  it("stops and keeps everything when the host is unavailable", async () => {
    const storage = new ClipStorage(memoryStore());
    await storage.addToOutbox({ localId: "a", clip, createdAt: "" });
    const transport: Transport = async () => {
      throw new HostUnavailableError("Specified native messaging host not found.");
    };
    const result = await flushOutbox(new NativeClient(transport), storage);
    expect(result.sent).toBe(0);
    expect(result.remaining).toHaveLength(1);
  });

  it("persists preferences with defaults", async () => {
    const storage = new ClipStorage(memoryStore());
    expect(await storage.preferences()).toEqual({ aspectRatio: "9:16", cropMode: "smart", resolution: "1080p" });
    await storage.savePreferences({ aspectRatio: "1:1", cropMode: "blur", resolution: "720p" });
    expect((await storage.preferences()).cropMode).toBe("blur");
  });
});
