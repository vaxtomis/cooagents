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
            complete:
              "border-[rgba(143,164,106,0.24)] bg-[linear-gradient(180deg,rgba(143,164,106,0.18),rgba(143,164,106,0.1))] text-[#c1cb9a]",
            current:
              "border-[rgba(169,112,45,0.28)] bg-[linear-gradient(180deg,rgba(169,112,45,0.22),rgba(169,112,45,0.12))] text-copy shadow-[0_0_0_1px_rgba(169,112,45,0.24)]",
            muted: "border-border bg-panel-strong/60 text-muted",
            pending: "border-border bg-panel-deep/90 text-muted",
          }[tone];
          return (
            <li
              aria-label={step}
              className={`flex-1 rounded-full border px-3 py-1.5 text-center text-[11px] font-medium ${toneClass}`}
              data-state={tone}
              key={step}
            >
              {STEP_LABELS[step]}
            </li>
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
