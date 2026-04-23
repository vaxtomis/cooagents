type Props = {
  score: number | null | undefined;
  threshold?: number | null;
  className?: string;
};

export function ScoreBadge({ score, threshold, className = "" }: Props) {
  if (score === null || score === undefined) {
    return (
      <span
        className={`inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-full border border-border bg-panel-strong/50 px-3 py-1 text-xs font-medium text-muted ${className}`.trim()}
      >
        尚未打分
      </span>
    );
  }

  const passed = typeof threshold === "number" ? score >= threshold : undefined;
  const tone =
    passed === undefined
      ? "border-accent/25 bg-accent/10 text-accent"
      : passed
        ? "border-success/25 bg-success/10 text-success"
        : "border-warning/25 bg-warning/10 text-warning";

  const suffix = typeof threshold === "number" ? ` / ${threshold}` : "";
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium ${tone} ${className}`.trim()}
    >
      评分 {score}
      {suffix}
    </span>
  );
}
