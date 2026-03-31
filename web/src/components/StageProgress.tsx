import { DASHBOARD_STAGE_FLOW } from "../types";

function resolveStageIndex(stage: string, failedAtStage?: string | null): number {
  if (stage === "INIT") {
    return 0;
  }

  if (stage === "FAILED") {
    const failedIndex = failedAtStage ? DASHBOARD_STAGE_FLOW.indexOf(failedAtStage as (typeof DASHBOARD_STAGE_FLOW)[number]) : -1;
    return failedIndex >= 0 ? failedIndex : DASHBOARD_STAGE_FLOW.length - 1;
  }

  const exactIndex = DASHBOARD_STAGE_FLOW.indexOf(stage as (typeof DASHBOARD_STAGE_FLOW)[number]);
  return exactIndex >= 0 ? exactIndex : 0;
}

export function StageProgress({
  stage,
  failedAtStage,
  className = "",
}: {
  stage: string;
  failedAtStage?: string | null;
  className?: string;
}) {
  const currentIndex = resolveStageIndex(stage, failedAtStage);
  const isFailed = stage === "FAILED";

  return (
    <ol className={`grid grid-cols-14 gap-1.5 ${className}`.trim()} role="list">
      {DASHBOARD_STAGE_FLOW.map((stageKey, index) => {
        const state = isFailed && index === currentIndex
          ? "failed"
          : index < currentIndex
            ? "complete"
            : index === currentIndex
              ? "current"
              : "pending";
        const toneClass = {
          complete: "bg-success",
          current: "bg-accent shadow-[0_0_24px_rgba(168,85,247,0.35)]",
          failed: "bg-danger shadow-[0_0_24px_rgba(239,68,68,0.28)]",
          pending: "bg-white/8",
        }[state];

        return (
          <li
            aria-label={stageKey}
            className={`h-1.5 rounded-full ${toneClass}`}
            data-state={state}
            key={stageKey}
          />
        );
      })}
    </ol>
  );
}
