import { useState } from "react";
import { formatTimestamp, parseTimestamp } from "../lib/time";
import type { Job, JobCounts, OutboxItem } from "../lib/types";
import { JobItem } from "./JobItem";

type Filter = "video" | "all";

interface Props {
  jobs: Job[];
  counts: JobCounts | null;
  outbox: OutboxItem[];
  currentVideoId: string | null;
  busyJobId: number | null;
  clearing: boolean;
  stale: boolean;
  onEdit: (job: Job) => void;
  onRemove: (job: Job) => void;
  onRetry: (job: Job) => void;
  onDiscardOutbox: (item: OutboxItem) => void;
  onClear: () => void;
}

export function QueuePanel(props: Props) {
  const { jobs, counts, outbox, currentVideoId } = props;
  const [filter, setFilter] = useState<Filter>("all");
  const shown = filter === "video" && currentVideoId ? jobs.filter((j) => j.videoId === currentVideoId) : jobs;
  const pending = counts?.pending ?? jobs.filter((j) => j.status === "pending").length;
  const clearable = outbox.length > 0 || jobs.some((j) => j.status !== "downloading" && j.status !== "processing");

  return (
    <section className="card queue" aria-labelledby="queue-heading">
      <div className="queue-head">
        <h2 id="queue-heading">
          Queue <span className="count">{pending} pending</span>
        </h2>
        <div className="queue-tools">
          {currentVideoId && (
            <div className="filter" role="group" aria-label="Filter queue">
              <button type="button" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>All</button>
              <button type="button" aria-pressed={filter === "video"} onClick={() => setFilter("video")}>This video</button>
            </div>
          )}
          {clearable && (
            <button type="button" className="link-button danger" disabled={props.clearing} onClick={props.onClear}>
              Clear
            </button>
          )}
        </div>
      </div>
      {props.stale && <p className="muted small">Showing the last known queue; the ytclip host is not reachable.</p>}

      {outbox.length > 0 && (
        <ul className="job-list" aria-label="Clips waiting to be sent">
          {outbox.map((item) => {
            const start = parseTimestamp(item.clip.start) ?? 0;
            const end = parseTimestamp(item.clip.end) ?? 0;
            return (
              <li key={item.localId} className="job outbox">
                <div className="job-head">
                  <span className="job-title">{item.clip.title ?? item.clip.url}</span>
                  <span className="badge badge-unsent">Not sent</span>
                </div>
                <div className="job-meta">
                  <span>{formatTimestamp(start)} → {formatTimestamp(end)}</span>
                  <span>{item.clip.aspectRatio} · {item.clip.cropMode} · {item.clip.resolution}</span>
                </div>
                <div className="job-actions">
                  <button type="button" className="link-button danger" onClick={() => props.onDiscardOutbox(item)}>
                    Discard
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {shown.length === 0 && outbox.length === 0 ? (
        <p className="muted empty">{filter === "video" ? "No clips from this video yet." : "No clips queued yet."}</p>
      ) : (
        <ul className="job-list">
          {shown.map((job) => (
            <JobItem key={job.id} job={job} busy={props.busyJobId === job.id}
              onEdit={props.onEdit} onRemove={props.onRemove} onRetry={props.onRetry} />
          ))}
        </ul>
      )}
    </section>
  );
}
