import { useId, type FormEvent } from "react";
import { formatDuration } from "../lib/time";
import { MODE_OPTIONS, RATIO_OPTIONS, RESOLUTION_OPTIONS } from "../lib/options";
import type { ClipDraft, FieldErrors, Job } from "../lib/types";
import { Segmented } from "./Segmented";
import { TimeField } from "./TimeField";

export interface DuplicateNotice {
  job: Job;
}

interface Props {
  draft: ClipDraft;
  errors: FieldErrors;
  showErrors: boolean;
  durationMs: number | null;
  disabled: boolean;
  submitting: boolean;
  editingJob: Job | null;
  duplicate: DuplicateNotice | null;
  submitLabel: string;
  canReadPlayer: boolean;
  onChange: (patch: Partial<ClipDraft>) => void;
  onUseCurrentTime: (field: "start" | "end") => void;
  onSubmit: (allowDuplicate: boolean) => void;
  onCancelEdit: () => void;
}

export function ClipForm(props: Props) {
  const { draft, errors, showErrors, durationMs, disabled, submitting, editingJob, duplicate } = props;
  const nameId = useId();
  const visible = (field: keyof FieldErrors) => (showErrors ? errors[field] : undefined);

  function submit(event: FormEvent) {
    event.preventDefault();
    props.onSubmit(false);
  }

  return (
    <form className="card clip-form" onSubmit={submit} noValidate aria-label="Clip settings">
      {editingJob && (
        <div className="editing-banner">
          <span>Editing job #{editingJob.id}</span>
          <button type="button" className="link-button" onClick={props.onCancelEdit}>
            Cancel
          </button>
        </div>
      )}
      <div className="time-row">
        <TimeField
          label="Start"
          value={draft.start}
          error={visible("start")}
          onChange={(start) => props.onChange({ start })}
          onUseCurrentTime={props.canReadPlayer ? () => props.onUseCurrentTime("start") : undefined}
          disabled={disabled}
        />
        <TimeField
          label="End"
          value={draft.end}
          error={visible("end")}
          onChange={(end) => props.onChange({ end })}
          onUseCurrentTime={props.canReadPlayer ? () => props.onUseCurrentTime("end") : undefined}
          disabled={disabled}
        />
      </div>
      <p className="duration" aria-live="polite">
        {durationMs !== null && !errors.end ? `Clip length ${formatDuration(durationMs)}` : " "}
      </p>

      <Segmented label="Aspect ratio" value={draft.aspectRatio} options={RATIO_OPTIONS} disabled={disabled}
        onChange={(aspectRatio) => props.onChange({ aspectRatio })} />
      <Segmented label="Crop" value={draft.cropMode} options={MODE_OPTIONS} disabled={disabled}
        onChange={(cropMode) => props.onChange({ cropMode })} />
      {draft.cropMode === "custom" &&
        (visible("cropRect") ? (
          <p className="field-error" role="alert">{errors.cropRect}</p>
        ) : (
          <p className="muted small">
            {draft.cropRect
              ? `Custom area ${Math.round(draft.cropRect.width * 100)}% × ${Math.round(draft.cropRect.height * 100)}% of the frame. Change it with Area on the YouTube page.`
              : "Drag a box on the video in the YouTube tab to choose the area."}
          </p>
        ))}
      <Segmented label="Resolution" value={draft.resolution} options={RESOLUTION_OPTIONS} disabled={disabled}
        onChange={(resolution) => props.onChange({ resolution })} />

      <div className="field">
        <label className="field-label" htmlFor={nameId}>
          Filename <span className="optional">optional</span>
        </label>
        <input
          id={nameId}
          className={visible("outputName") ? "input invalid" : "input"}
          value={draft.outputName}
          placeholder="Automatic from title and times"
          maxLength={160}
          disabled={disabled}
          aria-invalid={Boolean(visible("outputName"))}
          onChange={(e) => props.onChange({ outputName: e.target.value })}
        />
        {visible("outputName") && <p className="field-error" role="alert">{errors.outputName}</p>}
      </div>

      {duplicate && (
        <div className="notice warning" role="status">
          <p>
            This exact clip is already queued as job #{duplicate.job.id} ({duplicate.job.status}).
          </p>
          <button type="button" className="button secondary small" onClick={() => props.onSubmit(true)}>
            Add anyway
          </button>
        </div>
      )}

      <button type="submit" className="button primary" disabled={disabled || submitting}>
        {submitting ? "Saving…" : props.submitLabel}
      </button>
    </form>
  );
}
