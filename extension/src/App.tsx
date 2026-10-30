import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ClipForm, type DuplicateNotice } from "./components/ClipForm";
import { HostSetup } from "./components/HostSetup";
import { QueuePanel } from "./components/QueuePanel";
import { NotAVideo, VideoHeader } from "./components/VideoHeader";
import { HostError, HostUnavailableError, type NativeClient } from "./lib/native";
import { newLocalId, type ClipStorage, type PageMark, type Preferences } from "./lib/storage";
import type { ActiveVideo, PlayerState } from "./lib/tab";
import { formatTimestamp, secondsToTimestamp } from "./lib/time";
import type { ClipDraft, FieldErrors, Job, VideoInfo } from "./lib/types";
import { useQueue } from "./lib/useQueue";
import { clipKey, isValid, toRequest, validateDraft } from "./lib/validation";

export interface AppDeps {
  client: NativeClient;
  storage: ClipStorage;
  getActiveVideo: () => Promise<ActiveVideo | null>;
  readPlayerState: (tabId: number) => Promise<PlayerState | null>;
  /** Subscribes to active-tab changes (the side panel outlives tab switches). Returns an unsubscribe. */
  watchActiveTab?: (onChange: () => void) => () => void;
  /** Subscribes to Start/End times marked with the buttons on the YouTube page. */
  watchMarks?: (onMark: (mark: PageMark) => void) => () => void;
}

interface Flash {
  kind: "success" | "error" | "info";
  text: string;
}

function emptyDraft(prefs: Preferences): ClipDraft {
  return { start: "", end: "", outputName: "", ...prefs };
}

function draftFromJob(job: Job): ClipDraft {
  return {
    start: formatTimestamp(job.startMs),
    end: formatTimestamp(job.endMs),
    aspectRatio: job.aspectRatio,
    cropMode: job.cropMode,
    resolution: job.resolution,
    outputName: job.outputName ?? "",
    cropRect: job.cropRect,
  };
}

export function App({ client, storage, getActiveVideo, readPlayerState, watchActiveTab, watchMarks }: AppDeps) {
  const queue = useQueue(client, storage);
  const connected = queue.host === "ready";
  const [video, setVideo] = useState<ActiveVideo | null>(null);
  const [loading, setLoading] = useState(true);
  const [videoDuration, setVideoDuration] = useState<number | null>(null);
  const [draft, setDraft] = useState<ClipDraft | null>(null);
  const [editingJob, setEditingJob] = useState<Job | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const [serverErrors, setServerErrors] = useState<FieldErrors>({});
  const [duplicate, setDuplicate] = useState<DuplicateNotice | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [busyJobId, setBusyJobId] = useState<number | null>(null);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [queueBusy, setQueueBusy] = useState(false);
  const [tabVersion, setTabVersion] = useState(0);
  // undefined until the first load; then the video id the form was last set up for.
  const loadedVideoId = useRef<string | null | undefined>(undefined);
  const editingRef = useRef<Job | null>(null);
  editingRef.current = editingJob;

  useEffect(() => watchActiveTab?.(() => setTabVersion((v) => v + 1)), [watchActiveTab]);

  // Load the active tab, saved draft for this video and player position; again on tab changes.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [active, prefs, stored] = await Promise.all([getActiveVideo(), storage.preferences(), storage.draft()]);
      const player = active ? await readPlayerState(active.tabId) : null;
      if (cancelled) return;
      const withTitle = active && player?.title ? { ...active, title: player.title } : active;
      setVideo(withTitle);
      setVideoDuration(player?.duration ?? null);
      const videoId = withTitle?.videoId ?? null;
      const videoChanged = loadedVideoId.current !== videoId;
      loadedVideoId.current = videoId;
      // Keep what the user is typing or editing unless they moved to a different video.
      if (videoChanged && !editingRef.current) {
        if (stored && withTitle && stored.videoId === withTitle.videoId) {
          setDraft(stored.draft);
        } else {
          const fresh = emptyDraft(prefs);
          if (player && player.currentTime > 0) fresh.start = secondsToTimestamp(player.currentTime);
          setDraft(fresh);
        }
      }
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [getActiveVideo, readPlayerState, storage, tabVersion]);

  const target: VideoInfo | null = useMemo(() => {
    if (editingJob) return { videoId: editingJob.videoId, url: editingJob.url, title: editingJob.title, isShort: false };
    return video;
  }, [editingJob, video]);

  const validation = useMemo(
    () => (draft ? validateDraft(draft, editingJob ? null : videoDuration) : null),
    [draft, editingJob, videoDuration],
  );
  const errors: FieldErrors = { ...(validation?.errors ?? {}), ...serverErrors };

  const updateDraft = useCallback(
    (patch: Partial<ClipDraft>) => {
      setDraft((current) => {
        if (!current) return current;
        const next = { ...current, ...patch };
        if (target) void storage.saveDraft({ videoId: target.videoId, draft: next, editingJobId: editingJob?.id ?? null });
        if ("aspectRatio" in patch || "cropMode" in patch || "resolution" in patch) {
          void storage.savePreferences({ aspectRatio: next.aspectRatio, cropMode: next.cropMode, resolution: next.resolution });
        }
        return next;
      });
      setServerErrors({});
      setDuplicate(null);
    },
    [editingJob, storage, target],
  );

  async function fillCurrentTime(field: "start" | "end") {
    if (!video) return;
    const player = await readPlayerState(video.tabId);
    if (!player) {
      setFlash({ kind: "error", text: "Could not read the player position. Is the video loaded?" });
      return;
    }
    if (player.duration) setVideoDuration(player.duration);
    updateDraft({ [field]: secondsToTimestamp(player.currentTime) });
  }

  const applyMark = useRef<(mark: PageMark) => void>(() => {});
  applyMark.current = (mark) => {
    if (mark.videoId !== target?.videoId) return;
    updateDraft(mark.patch);
    const { start, end } = mark.patch;
    const times = [start && `Start set to ${start}`, end && `End set to ${end}`].filter(Boolean);
    if (times.length === 1) setFlash({ kind: "info", text: `${times[0]} from the page.` });
  };
  useEffect(() => watchMarks?.((mark) => applyMark.current(mark)), [watchMarks]);

  function afterSaved(message: string) {
    setFlash({ kind: "success", text: message });
    setShowErrors(false);
    setDuplicate(null);
    setEditingJob(null);
    setDraft((current) => (current ? { ...current, start: current.end, end: "", outputName: "" } : current));
    void storage.saveDraft(null);
  }

  async function saveOffline(allowDuplicate: boolean) {
    if (!draft || !target) return;
    const clip = toRequest(draft, target);
    if (!allowDuplicate && queue.outbox.some((item) => clipKey(item.clip) === clipKey(clip))) {
      setFlash({ kind: "info", text: "This clip is already saved and waiting to be sent." });
      return;
    }
    queue.setOutbox(await storage.addToOutbox({ localId: newLocalId(), clip, createdAt: new Date().toISOString() }));
    afterSaved("Saved in the browser. It will be queued when ytclip is connected.");
  }

  async function submit(allowDuplicate: boolean) {
    if (!draft || !target || !validation) return;
    setShowErrors(true);
    setFlash(null);
    if (!isValid(validation)) return;
    if (queue.host !== "ready" && !editingJob) {
      await saveOffline(allowDuplicate);
      return;
    }
    setSubmitting(true);
    try {
      const clip = toRequest(draft, target);
      const { job } = editingJob
        ? await client.updateJob(editingJob.id, clip, allowDuplicate)
        : await client.addJob(clip, allowDuplicate);
      afterSaved(editingJob ? `Updated job #${job.id}.` : `Queued as job #${job.id}. Press Download to process it.`);
      await queue.refresh();
    } catch (error) {
      if (error instanceof HostError && error.code === "duplicate" && error.existingJob) {
        setDuplicate({ job: error.existingJob });
      } else if (error instanceof HostError && error.code === "validation" && error.field) {
        setServerErrors({ [error.field]: error.message });
      } else if (error instanceof HostUnavailableError && !editingJob) {
        await saveOffline(allowDuplicate);
        void queue.refresh();
      } else {
        setFlash({ kind: "error", text: error instanceof Error ? error.message : String(error) });
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function jobAction(job: Job, action: () => Promise<unknown>, message: string) {
    setBusyJobId(job.id);
    try {
      await action();
      setFlash({ kind: "success", text: message });
      await queue.refresh();
    } catch (error) {
      setFlash({ kind: "error", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setBusyJobId(null);
    }
  }

  async function clearQueue() {
    const removable = queue.jobs.filter((j) => j.status !== "downloading" && j.status !== "processing").length;
    const total = removable + queue.outbox.length;
    if (total === 0 || !confirm(`Remove ${total} clip${total === 1 ? "" : "s"} from the queue? Downloaded files are kept.`)) {
      return;
    }
    setQueueBusy(true);
    try {
      queue.setOutbox(await storage.clearOutbox());
      if (connected && removable > 0) {
        const { removed } = await client.clearJobs();
        setFlash({ kind: "success", text: `Cleared ${removed} clip${removed === 1 ? "" : "s"} from the queue.` });
      } else {
        setFlash({ kind: "success", text: "Cleared the clips saved in the browser." });
      }
      await queue.refresh();
    } catch (error) {
      setFlash({ kind: "error", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setQueueBusy(false);
    }
  }

  async function startDownload() {
    setQueueBusy(true);
    try {
      const { alreadyRunning } = await client.startRun();
      setFlash({
        kind: alreadyRunning ? "info" : "success",
        text: alreadyRunning ? "Already downloading; new clips are picked up automatically." : "Download started.",
      });
      await queue.refresh();
    } catch (error) {
      setFlash({ kind: "error", text: error instanceof Error ? error.message : String(error) });
    } finally {
      setQueueBusy(false);
    }
  }

  function startEdit(job: Job) {
    setEditingJob(job);
    setDraft(draftFromJob(job));
    setShowErrors(false);
    setServerErrors({});
    setDuplicate(null);
  }

  async function cancelEdit() {
    setEditingJob(null);
    setDraft(emptyDraft(await storage.preferences()));
  }

  const pending = queue.counts?.pending ?? 0;
  const running = queue.jobs.some((j) => j.status === "downloading" || j.status === "processing");

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <img src="icons/icon32.png" alt="" width={20} height={20} />
          <h1>ytclip</h1>
        </div>
        <span className={`connection ${queue.host}`} role="status">
          {queue.host === "checking" ? "Connecting…" : connected ? "Connected" : "Offline"}
        </span>
      </header>

      {flash && (
        <div className={`flash ${flash.kind}`} role={flash.kind === "error" ? "alert" : "status"}>
          <span>{flash.text}</span>
          <button type="button" className="link-button" onClick={() => setFlash(null)} aria-label="Dismiss message">×</button>
        </div>
      )}

      {(queue.host === "unavailable" || queue.host === "error") && (
        <HostSetup message={queue.hostMessage} outboxCount={queue.outbox.length} onRetry={() => void queue.refresh()} />
      )}

      {loading || !draft ? (
        <p className="muted">Loading…</p>
      ) : target ? (
        <>
          <VideoHeader video={target} label={editingJob ? `Editing job #${editingJob.id}` : undefined} />
          <ClipForm
            draft={draft}
            errors={errors}
            showErrors={showErrors || Object.keys(serverErrors).length > 0}
            durationMs={validation?.durationMs ?? null}
            disabled={submitting}
            submitting={submitting}
            editingJob={editingJob}
            duplicate={duplicate}
            submitLabel={editingJob ? "Save changes" : connected ? "Add to queue" : "Save for later"}
            canReadPlayer={Boolean(video) && !editingJob}
            onChange={updateDraft}
            onUseCurrentTime={(field) => void fillCurrentTime(field)}
            onSubmit={(allow) => void submit(allow)}
            onCancelEdit={() => void cancelEdit()}
          />
        </>
      ) : (
        <NotAVideo />
      )}

      <QueuePanel
        jobs={queue.jobs}
        counts={queue.counts}
        outbox={queue.outbox}
        currentVideoId={video?.videoId ?? null}
        busyJobId={busyJobId}
        clearing={queueBusy}
        stale={queue.stale}
        onEdit={startEdit}
        onRemove={(job) => {
          if (confirm(`Remove job #${job.id} from the queue?`)) {
            void jobAction(job, () => client.removeJob(job.id), `Removed job #${job.id}.`);
          }
        }}
        onRetry={(job) => void jobAction(job, () => client.retryJob(job.id), `Job #${job.id} queued for retry.`)}
        onDiscardOutbox={async (item) => queue.setOutbox(await storage.removeFromOutbox(item.localId))}
        onClear={() => void clearQueue()}
      />

      {connected && (pending > 0 || running) && (
        <footer className="app-footer">
          <button type="button" className="button primary download-button" disabled={queueBusy || running}
            onClick={() => void startDownload()}>
            {running ? "Downloading…" : `Download ${pending} clip${pending === 1 ? "" : "s"}`}
          </button>
          {running && pending > 0 && <span className="muted small">{pending} more waiting</span>}
        </footer>
      )}
    </div>
  );
}
