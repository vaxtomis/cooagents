import { DESIGN_WORK_STATE_ORDER, type DesignWorkState } from "../types";

type Props = {
  current: DesignWorkState;
  active?: boolean;
  escalated?: boolean;
  className?: string;
};

function resolveIndex(current: DesignWorkState): number {
  const order: readonly DesignWorkState[] = DESIGN_WORK_STATE_ORDER;
  const index = order.indexOf(current);
  return index === -1 ? 0 : index;
}

const STATE_LABELS: Record<DesignWorkState, string> = {
  INIT: "就绪",
  MODE_BRANCH: "模式分支",
  PRE_VALIDATE: "预校验",
  PROMPT_COMPOSE: "Prompt",
  LLM_GENERATE: "生成",
  MOCKUP: "Mockup",
  POST_VALIDATE: "后校验",
  PERSIST: "持久化",
  COMPLETED: "完成",
  ESCALATED: "升级",
  CANCELLED: "取消",
};

export function DesignWorkStateProgress({
  current,
  active = false,
  escalated = false,
  className = "",
}: Props) {
  const currentIndex = resolveIndex(current);
  const completed = current === "COMPLETED";
  const terminal = current === "CANCELLED" || current === "ESCALATED" || escalated;
  const terminalLabel = current === "CANCELLED" ? "已取消" : "已升级";

  return (
    <div className={`flex flex-col gap-3 xl:flex-row xl:items-center ${className}`.trim()}>
      <ol
        className="grid flex-1 grid-cols-2 gap-1.5 sm:grid-cols-3 xl:grid-cols-9"
        role="list"
      >
        {DESIGN_WORK_STATE_ORDER.map((state, index) => {
          const tone = terminal
            ? "muted"
            : completed && index === currentIndex
              ? "done"
              : active && index === currentIndex
                ? "active"
                : index < currentIndex
                  ? "complete"
                  : index === currentIndex
                    ? "current"
                    : "pending";
          const toneClass = {
            complete:
              "border-[rgba(143,164,106,0.24)] bg-[linear-gradient(180deg,rgba(143,164,106,0.18),rgba(143,164,106,0.1))] text-[#c1cb9a]",
            current:
              "border-[rgba(169,112,45,0.28)] bg-[linear-gradient(180deg,rgba(169,112,45,0.22),rgba(169,112,45,0.12))] text-copy shadow-[0_0_0_1px_rgba(169,112,45,0.24)]",
            active:
              "progress-step-active border-[rgba(215,154,74,0.58)] bg-[linear-gradient(180deg,rgba(215,154,74,0.34),rgba(169,112,45,0.18))] text-[#f1d7aa]",
            done:
              "border-[rgba(215,154,74,0.62)] bg-[linear-gradient(180deg,rgba(215,154,74,0.36),rgba(169,112,45,0.2))] text-[#f5d7a1] shadow-[0_0_0_1px_rgba(215,154,74,0.38),0_0_24px_rgba(215,154,74,0.24)]",
            muted: "border-border bg-panel-strong/60 text-muted",
            pending: "border-border bg-panel-deep/90 text-muted",
          }[tone];
          return (
            <li
              aria-label={state}
              className={`rounded-full border px-2.5 py-1.5 text-center text-[11px] font-medium ${toneClass}`}
              data-state={tone}
              key={state}
            >
              <span>{STATE_LABELS[state]}</span>
            </li>
          );
        })}
      </ol>
      {terminal ? (
        <span
          className="w-fit shrink-0 rounded-full border border-[rgba(170,80,61,0.34)] bg-[linear-gradient(180deg,rgba(170,80,61,0.18),rgba(170,80,61,0.08))] px-3 py-1 text-xs font-medium text-[#d4876f]"
          data-state="terminal"
          role="status"
        >
          {terminalLabel}
        </span>
      ) : null}
    </div>
  );
}
