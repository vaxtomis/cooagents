import type { ChangeEvent } from "react";
import type { RepoBranches } from "../../types";

interface Props {
  branches: RepoBranches | undefined;
  value: string;
  onChange: (ref: string) => void;
  error?: string;
}

const CUSTOM = "__custom__";

export function BranchPicker({ branches, value, onChange, error }: Props) {
  const list = branches?.branches ?? [];
  const isCustom = value !== "" && !list.includes(value);
  const selectValue = isCustom ? CUSTOM : value || (list[0] ?? CUSTOM);

  function handleSelect(event: ChangeEvent<HTMLSelectElement>) {
    const next = event.target.value;
    if (next !== CUSTOM) onChange(next);
  }

  return (
    <div className="grid gap-2 md:grid-cols-[200px_1fr]">
      <label className="space-y-1 text-sm text-muted">
        <span>分支</span>
        <select
          className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={handleSelect}
          value={selectValue}
        >
          {list.map((branch) => (
            <option key={branch} value={branch}>
              {branch}
            </option>
          ))}
          <option value={CUSTOM}>— 自定义 ref/sha —</option>
        </select>
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>ref</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => onChange(event.target.value)}
          placeholder="输入 ref / sha"
          value={value}
        />
      </label>
      {error ? (
        <p className="text-xs text-danger md:col-span-2">{error}</p>
      ) : null}
    </div>
  );
}
