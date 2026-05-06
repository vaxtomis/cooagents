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
        className="grid flex-1 gap-2"
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
            complete:
              "border-[rgba(143,164,106,0.22)] bg-[linear-gradient(180deg,rgba(143,164,106,0.22),rgba(143,164,106,0.12))]",
            current:
              "border-[rgba(169,112,45,0.28)] bg-[linear-gradient(180deg,rgba(169,112,45,0.26),rgba(169,112,45,0.14))] shadow-[0_0_0_1px_rgba(169,112,45,0.26)]",
            muted: "border-border bg-panel-strong/60",
            pending: "border-border bg-panel-deep/90",
          }[tone];
          return (
            <li
              aria-label={state}
              className={`h-2.5 rounded-full border ${toneClass}`}
              data-state={tone}
              key={state}
            />
          );
        })}
      </ol>
      {terminal ? (
        <span
          className="shrink-0 rounded-full border border-[rgba(170,80,61,0.34)] bg-[linear-gradient(180deg,rgba(170,80,61,0.18),rgba(170,80,61,0.08))] px-3 py-1 text-xs font-medium text-[#d4876f]"
          data-state="terminal"
          role="status"
        >
          {terminalLabel}
        </span>
      ) : null}
    </div>
  );
}
