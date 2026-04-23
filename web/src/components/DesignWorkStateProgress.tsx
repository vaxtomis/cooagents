import { DESIGN_WORK_STATE_ORDER, type DesignWorkState } from "../types";

type Props = {
  current: DesignWorkState;
  escalated?: boolean;
  className?: string;
};

function resolveIndex(current: DesignWorkState): number {
  const order: readonly DesignWorkState[] = DESIGN_WORK_STATE_ORDER;
  const index = order.indexOf(current);
  return index === -1 ? 0 : index;
}

export function DesignWorkStateProgress({ current, escalated = false, className = "" }: Props) {
  const currentIndex = resolveIndex(current);
  const terminal = current === "CANCELLED" || current === "ESCALATED" || escalated;
  const terminalLabel = current === "CANCELLED" ? "已取消" : "已升级";

  return (
    <div className={`flex items-center gap-3 ${className}`.trim()}>
      <ol
        className="grid flex-1 gap-1.5"
        role="list"
        style={{ gridTemplateColumns: `repeat(${DESIGN_WORK_STATE_ORDER.length}, minmax(0, 1fr))` }}
      >
        {DESIGN_WORK_STATE_ORDER.map((state, index) => {
          const tone = terminal
            ? "muted"
            : index < currentIndex
              ? "complete"
              : index === currentIndex
                ? "current"
                : "pending";
          const toneClass = {
            complete: "bg-success",
            current: "bg-accent shadow-[0_0_0_1px_var(--color-accent)]",
            muted: "bg-panel-strong/60",
            pending: "bg-panel-strong",
          }[tone];
          return (
            <li
              key={state}
              aria-label={state}
              className={`h-1.5 rounded-full ${toneClass}`}
              data-state={tone}
            />
          );
        })}
      </ol>
      {terminal ? (
        <span
          className="shrink-0 rounded-full border border-danger/25 bg-danger/10 px-3 py-1 text-xs font-medium text-danger"
          data-state="terminal"
          role="status"
        >
          {terminalLabel}
        </span>
      ) : null}
    </div>
  );
}
