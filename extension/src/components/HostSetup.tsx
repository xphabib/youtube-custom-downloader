import { useState } from "react";

const INSTALL_COMMAND = "./scripts/install.sh";

interface Props {
  message: string | null;
  outboxCount: number;
  onRetry: () => void;
}

/** Shown when Chrome cannot reach the native host. Drafts and saved clips are kept. */
export function HostSetup({ message, outboxCount, onRetry }: Props) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(INSTALL_COMMAND);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section className="card notice warning" aria-labelledby="host-heading" role="status">
      <h2 id="host-heading">ytclip is not connected</h2>
      <p className="small">
        The extension talks to the local <code>ytclip</code> program through Chrome native messaging.
        Install it from the project folder, then reload this popup:
      </p>
      <div className="command">
        <code>{INSTALL_COMMAND}</code>
        <button type="button" className="button secondary small" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
      </div>
      <p className="small muted">
        Already installed? Run <code>ytclip native install</code> and <code>ytclip install-check</code>.
      </p>
      {outboxCount > 0 && (
        <p className="small">
          {outboxCount} clip{outboxCount === 1 ? " is" : "s are"} saved in the browser and will be sent once
          ytclip is reachable.
        </p>
      )}
      {message && <p className="small muted">Chrome said: {message}</p>}
      <button type="button" className="button secondary" onClick={onRetry}>Try again</button>
    </section>
  );
}
