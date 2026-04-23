import { DEV_WORK_STEP_ORDER, type DevWorkStep } from "../types";

type Props = {
  current: DevWorkStep;
  escalated?: boolean;
  className?: string;
};

function resolveIndex(current: DevWorkStep): number {
  const order: readonly DevWorkStep[] = DEV_WORK_STEP_ORDER;
  const index = order.indexOf(current);
  return index === -1 ? 0 : index;
}

const STEP_LABELS: Record<DevWorkStep, string> = {
  INIT: "就绪",
  STEP1_VALIDATE: "Step1 校验",
  STEP2_ITERATION: "Step2 迭代",
  STEP3_CONTEXT: "Step3 上下文",
  STEP4_DEVELOP: "Step4 开发",
  STEP5_REVIEW: "Step5 评审",
  COMPLETED: "完成",
  ESCALATED: "升级",
  CANCELLED: "取消",
};

export function DevWorkStepProgress({ current, escalated = false, className = "" }: Props) {
  const currentIndex = resolveIndex(current);
  const terminal = current === "CANCELLED" || current === "ESCALATED" || escalated;
  const terminalLabel = current === "CANCELLED" ? "已取消" : "已升级";

  return (
    <div className={`flex items-center gap-3 ${className}`.trim()}>
      <ol className="flex flex-1 items-center gap-1.5" role="list">
        {DEV_WORK_STEP_ORDER.map((step, index) => {
          const tone = terminal
            ? "muted"
            : index < currentIndex
              ? "complete"
              : index === currentIndex
                ? "current"
                : "pending";
          const toneClass = {
            complete: "bg-success/15 text-success border-success/30",
            current: "bg-accent/15 text-accent border-accent/40 shadow-[0_0_0_1px_var(--color-accent)]",
            muted: "bg-panel-strong/60 text-muted border-border",
            pending: "bg-panel-strong text-muted border-border",
          }[tone];
          return (
            <li
              key={step}
              aria-label={step}
              className={`flex-1 rounded-full border px-3 py-1.5 text-center text-[11px] font-medium ${toneClass}`}
              data-state={tone}
            >
              {STEP_LABELS[step]}
            </li>
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
