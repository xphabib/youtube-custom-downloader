import { useId } from "react";

interface Props {
  label: string;
  value: string;
  error?: string;
  placeholder?: string;
  onChange: (value: string) => void;
  onUseCurrentTime?: () => void;
  disabled?: boolean;
}

export function TimeField({ label, value, error, placeholder, onChange, onUseCurrentTime, disabled }: Props) {
  const id = useId();
  const errorId = `${id}-error`;
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className="time-input">
        <input
          id={id}
          className={error ? "input invalid" : "input"}
          value={value}
          inputMode="decimal"
          autoComplete="off"
          spellCheck={false}
          placeholder={placeholder ?? "00:00:00"}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : undefined}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
        {onUseCurrentTime && (
          <button
            type="button"
            className="icon-button"
            onClick={onUseCurrentTime}
            disabled={disabled}
            title={`Set ${label.toLowerCase()} to the current playback position`}
            aria-label={`Use current playback time for ${label.toLowerCase()}`}
          >
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" strokeWidth="2" />
              <path d="M12 7.5V12l3 2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        )}
      </div>
      {error && (
        <p className="field-error" id={errorId} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
