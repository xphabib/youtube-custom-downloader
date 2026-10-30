import { formatDuration, formatTimestamp } from "../lib/time";
import type { Job } from "../lib/types";

const STATUS_LABEL: Record<Job["status"], string> = {
  pending: "Pending",
  downloading: "Downloading",
  processing: "Processing",
  completed: "Completed",
  failed: "Failed",
};

interface Props {
  job: Job;
  busy: boolean;
  onEdit: (job: Job) => void;
  onRemove: (job: Job) => void;
  onRetry: (job: Job) => void;
}

export function StatusBadge({ status }: { status: Job["status"] }) {
  return <span className={`badge badge-${status}`}>{STATUS_LABEL[status]}</span>;
}

export function JobItem({ job, busy, onEdit, onRemove, onRetry }: Props) {
  const active = job.status === "downloading" || job.status === "processing";
  const editable = job.status === "pending" || job.status === "failed";
  const title = job.title ?? job.videoId;
  return (
    <li className="job">
      <div className="job-head">
        <span className="job-title" title={title}>{title}</span>
        <StatusBadge status={job.status} />
      </div>
      <div className="job-meta">
        <span>#{job.id}</span>
        <span>
          {formatTimestamp(job.startMs)} → {formatTimestamp(job.endMs)} ({formatDuration(job.endMs - job.startMs)})
        </span>
        <span>{job.aspectRatio} · {job.cropMode} · {job.resolution}</span>
      </div>
      {active && (
        <div className="progress" role="progressbar" aria-label={`Job ${job.id} progress`}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(job.progress * 100)}>
          <div className="progress-fill" style={{ width: `${Math.round(job.progress * 100)}%` }} />
          <span className="progress-label">{job.stage ?? job.status} {Math.round(job.progress * 100)}%</span>
        </div>
      )}
      {job.status === "failed" && job.error && <p className="job-error">{job.error}</p>}
      {job.status === "completed" && job.outputPath && (
        <p className="job-output" title={job.outputPath}>{job.outputPath}</p>
      )}
      <div className="job-actions">
        {job.status === "failed" && (
          <button type="button" className="link-button" disabled={busy} onClick={() => onRetry(job)}>Retry</button>
        )}
        {editable && (
          <button type="button" className="link-button" disabled={busy} onClick={() => onEdit(job)}>Edit</button>
        )}
        {!active && (
          <button type="button" className="link-button danger" disabled={busy}
            onClick={() => onRemove(job)} aria-label={`Remove job ${job.id}`}>
            Remove
          </button>
        )}
      </div>
    </li>
  );
}
