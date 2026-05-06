interface SegmentedControlOption<T extends string> {
  value: T;
  label: string;
}

interface SegmentedControlProps<T extends string> {
  ariaLabel: string;
  options: readonly SegmentedControlOption<T>[];
  value: T;
  onChange: (value: T) => void;
}

export function SegmentedControl<T extends string>({
  ariaLabel,
  options,
  value,
  onChange,
}: SegmentedControlProps<T>) {
  return (
    <div
      className="inline-flex flex-wrap gap-2 rounded-full border border-border bg-panel-strong/45 p-1"
      role="tablist"
      aria-label={ariaLabel}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(option.value)}
            className={[
              "rounded-full px-3 py-1.5 text-xs font-medium transition",
              selected
                ? "bg-panel text-copy shadow-[0_0_0_1px_var(--color-ring-warm)]"
                : "text-muted hover:text-copy",
            ].join(" ")}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
