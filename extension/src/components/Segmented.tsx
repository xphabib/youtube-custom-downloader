import { useId } from "react";

interface Option<T extends string> {
  value: T;
  label: string;
  hint?: string;
}

interface Props<T extends string> {
  label: string;
  value: T;
  options: readonly Option<T>[];
  onChange: (value: T) => void;
  disabled?: boolean;
}

/** Radio group styled as a segmented control; native radios give keyboard support for free. */
export function Segmented<T extends string>({ label, value, options, onChange, disabled }: Props<T>) {
  const name = useId();
  return (
    <fieldset className="field segmented" disabled={disabled}>
      <legend className="field-label">{label}</legend>
      <div className="segmented-options">
        {options.map((option) => (
          <label key={option.value} className="segment" title={option.hint}>
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
