import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App, type AppDeps } from "./App";
import { HostUnavailableError, NativeClient, type Transport } from "./lib/native";
import { memoryStore } from "./test/memoryStore";
import { ClipStorage, type PageMark } from "./lib/storage";
import type { Job } from "./lib/types";

const VIDEO = {
  videoId: "dQw4w9WgXcQ",
  url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  title: "Test video",
  isShort: false,
  tabId: 1,
};

/** In-memory stand-in for the native host that keeps a job list. */
function fakeHost() {
  const jobs: Job[] = [];
  const runs: number[] = [];
  const transport: Transport = async (raw) => {
    const message = raw as { type: string; payload: Record<string, unknown> };
    switch (message.type) {
      case "ping":
        return { id: null, ok: true, data: { version: "0.1.0", outputDir: "/out", smartCrop: "dnn" } };
      case "list_jobs":
        return { id: null, ok: true, data: { jobs, counts: { pending: jobs.length } } };
      case "add_job": {
        const clip = message.payload.clip as Record<string, string>;
        const dup = jobs.find((j) => j.outputName === `${clip.start}-${clip.end}`);
        if (dup && !message.payload.allowDuplicate) {
          return { id: null, ok: false, error: { code: "duplicate", message: "dup", existingJob: dup } };
        }
        const job = {
          id: jobs.length + 1, videoId: VIDEO.videoId, url: VIDEO.url, title: clip.title ?? null,
          startMs: 0, endMs: 1000, aspectRatio: clip.aspectRatio, cropMode: clip.cropMode,
          resolution: clip.resolution, outputName: `${clip.start}-${clip.end}`, status: "pending",
          progress: 0, stage: null, outputPath: null, error: null, attempts: 0,
          createdAt: "", updatedAt: "", completedAt: null,
        } as Job;
        jobs.push(job);
        return { id: null, ok: true, data: { job } };
      }
      case "clear_jobs": {
        const removed = jobs.splice(0).length;
        return { id: null, ok: true, data: { removed } };
      }
      case "start_run":
        runs.push(jobs.length);
        return { id: null, ok: true, data: { started: true, alreadyRunning: false } };
      default:
        return { id: null, ok: false, error: { code: "unknown_type", message: message.type } };
    }
  };
  return { jobs, runs, transport };
}

function deps(transport: Transport, store = memoryStore()): AppDeps & { store: ReturnType<typeof memoryStore> } {
  return {
    client: new NativeClient(transport),
    storage: new ClipStorage(store),
    getActiveVideo: async () => VIDEO,
    readPlayerState: async () => ({ currentTime: 83.4, duration: 600, title: "Player title" }),
    store,
  };
}

describe("App", () => {
  it("prefills the start time from the player and queues a clip", async () => {
    const host = fakeHost();
    const user = userEvent.setup();
    render(<App {...deps(host.transport)} />);

    const start = await screen.findByLabelText("Start");
    expect(start).toHaveProperty("value", "00:01:23");
    expect(screen.getByText("Player title")).toBeTruthy();

    await user.type(screen.getByLabelText("End"), "00:01:40");
    await user.click(screen.getByRole("radio", { name: "1:1" }));
    await user.click(screen.getByRole("button", { name: "Add to queue" }));

    await screen.findByText(/Queued as job #1/);
    expect(host.jobs[0]).toMatchObject({ aspectRatio: "1:1", cropMode: "smart", title: "Player title" });
    // The next clip starts where the previous one ended.
    expect(screen.getByLabelText("Start")).toHaveProperty("value", "00:01:40");
  });

  it("shows validation errors and does not send invalid clips", async () => {
    const host = fakeHost();
    const user = userEvent.setup();
    render(<App {...deps(host.transport)} />);
    await user.type(await screen.findByLabelText("End"), "00:00:10");
    await user.click(screen.getByRole("button", { name: "Add to queue" }));
    expect(await screen.findByText("End time must be after the start time")).toBeTruthy();
    expect(host.jobs).toHaveLength(0);
  });

  it("offers 'Add anyway' for duplicates", async () => {
    const host = fakeHost();
    const user = userEvent.setup();
    render(<App {...deps(host.transport)} />);
    const end = await screen.findByLabelText("End");
    await user.type(end, "00:01:40");
    await user.click(screen.getByRole("button", { name: "Add to queue" }));
    await screen.findByText(/Queued as job #1/);

    await user.clear(screen.getByLabelText("Start"));
    await user.type(screen.getByLabelText("Start"), "00:01:23");
    await user.type(screen.getByLabelText("End"), "00:01:40");
    await user.click(screen.getByRole("button", { name: "Add to queue" }));
    await screen.findByText(/already queued as job #1/);
    await user.click(screen.getByRole("button", { name: "Add anyway" }));
    await waitFor(() => expect(host.jobs).toHaveLength(2));
  });

  it("keeps clips in the browser outbox and shows setup help when the host is missing", async () => {
    const user = userEvent.setup();
    const offline: Transport = async () => {
      throw new HostUnavailableError("Specified native messaging host not found.");
    };
    const d = deps(offline);
    render(<App {...d} />);
    expect(await screen.findByText("ytclip is not connected")).toBeTruthy();
    await user.type(screen.getByLabelText("End"), "00:01:40");
    await user.click(screen.getByRole("button", { name: "Save for later" }));
    await screen.findByText(/Saved in the browser/);
    expect((d.store.data.get("outbox") as unknown[]).length).toBe(1);
    expect(screen.getByText("Not sent")).toBeTruthy();
  });

  it("restores an unsent draft for the same video", async () => {
    const host = fakeHost();
    const store = memoryStore();
    store.data.set("draft", {
      videoId: VIDEO.videoId,
      draft: { start: "00:00:05", end: "00:00:09", aspectRatio: "16:9", cropMode: "blur", resolution: "720p", outputName: "kept" },
      editingJobId: null,
    });
    render(<App {...deps(host.transport, store)} />);
    expect(await screen.findByLabelText("End")).toHaveProperty("value", "00:00:09");
    expect(screen.getByRole("radio", { name: "Blur" })).toHaveProperty("checked", true);
    expect(screen.getByLabelText(/Filename/)).toHaveProperty("value", "kept");
  });

  it("starts a download and clears the queue", async () => {
    const host = fakeHost();
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<App {...deps(host.transport)} />);
    await user.type(await screen.findByLabelText("End"), "00:01:40");
    await user.click(screen.getByRole("button", { name: "Add to queue" }));

    await user.click(await screen.findByRole("button", { name: "Download 1 clip" }));
    await screen.findByText("Download started.");
    expect(host.runs).toEqual([1]);

    await user.click(screen.getByRole("button", { name: "Clear" }));
    await screen.findByText(/Cleared 1 clip/);
    expect(host.jobs).toHaveLength(0);
    expect(screen.getByText("No clips queued yet.")).toBeTruthy();
  });

  it("follows the active tab without losing the draft for the same video", async () => {
    const host = fakeHost();
    const user = userEvent.setup();
    let notify = () => {};
    const d = deps(host.transport);
    let active: typeof VIDEO | null = VIDEO;
    render(<App {...d} getActiveVideo={async () => active}
      watchActiveTab={(cb) => { notify = cb; return () => {}; }} />);
    await user.type(await screen.findByLabelText("End"), "00:01:40");

    notify();
    await waitFor(() => expect(screen.getByLabelText("End")).toHaveProperty("value", "00:01:40"));

    active = null;
    notify();
    expect(await screen.findByText(/Open a YouTube video/i)).toBeTruthy();
  });

  it("applies Start/End times marked on the YouTube page", async () => {
    const host = fakeHost();
    let emit: (mark: PageMark) => void = () => {};
    render(<App {...deps(host.transport)} watchMarks={(cb) => { emit = cb; return () => {}; }} />);
    await screen.findByLabelText("End");

    act(() => emit({ videoId: VIDEO.videoId, patch: { end: "00:02:00" }, at: 1 }));
    expect(screen.getByLabelText("End")).toHaveProperty("value", "00:02:00");
    expect(screen.getByText(/End set to 00:02:00/)).toBeTruthy();

    act(() => emit({ videoId: "anotherVid1", patch: { start: "00:00:05" }, at: 2 }));
    expect(screen.getByLabelText("Start")).toHaveProperty("value", "00:01:23");

    act(() => emit({ videoId: VIDEO.videoId, patch: { cropMode: "blur" }, at: 3 }));
    expect(screen.getByRole("radio", { name: "Blur" })).toHaveProperty("checked", true);
  });
});
