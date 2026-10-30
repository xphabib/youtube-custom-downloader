// Timestamp parsing for instant form feedback. Same rules as ytclip/timeutil.py.

const SECONDS_RE = /^(\d+)(?:\.(\d{1,3}))?$/;
const FIELD_RE = /^\d+$/;

/** Parse `SS`, `MM:SS` or `HH:MM:SS` (optional `.mmm`) into milliseconds, or null if invalid. */
export function parseTimestamp(value: string): number | null {
  const text = value.trim();
  if (!text) return null;
  const parts = text.split(":");
  const tail = parts.pop() ?? "";
  const match = SECONDS_RE.exec(tail);
  if (parts.length > 2 || !match || !parts.every((p) => FIELD_RE.test(p))) return null;
  const [hours, minutes] = [...Array(2 - parts.length).fill(0), ...parts.map(Number)] as [number, number];
  const seconds = Number(match[1]);
  if (parts.length > 0 && seconds > 59) return null;
  if (parts.length === 2 && minutes > 59) return null;
  const millis = Number((match[2] ?? "0").padEnd(3, "0"));
  return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis;
}

export function formatTimestamp(ms: number): string {
  const total = Math.floor(ms / 1000);
  const millis = Math.round(ms % 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  const base = `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
  return millis ? `${base}.${String(millis).padStart(3, "0")}` : base;
}

/** Human duration such as `1m 25s` or `8.5s`. */
export function formatDuration(ms: number): string {
  if (ms < 60_000) return `${Number((ms / 1000).toFixed(1))}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

/** Snap a video `currentTime` (seconds) to whole seconds for tidy timestamps. */
export function secondsToTimestamp(seconds: number): string {
  return formatTimestamp(Math.max(0, Math.floor(seconds)) * 1000);
}
