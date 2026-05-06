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
      aria-label={ariaLabel}
      className="inline-flex flex-wrap gap-2 rounded-[18px] border border-border-strong bg-panel-deep/80 p-1.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]"
      data-segmented-tone="console"
      role="tablist"
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            aria-selected={selected}
            className={[
              "rounded-[14px] border px-3 py-1.5 text-xs font-medium transition",
              selected
                ? "border-[color:var(--color-border-dark)] bg-[linear-gradient(180deg,rgba(201,154,84,0.24),rgba(201,154,84,0.14))] text-copy shadow-[0_8px_18px_rgba(0,0,0,0.28),inset_0_1px_0_rgba(255,255,255,0.06)]"
                : "border-transparent text-muted hover:border-border hover:bg-panel-strong/70 hover:text-copy",
            ].join(" ")}
            data-selected={selected ? "true" : "false"}
            onClick={() => onChange(option.value)}
            role="tab"
            tabIndex={selected ? 0 : -1}
            type="button"
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
