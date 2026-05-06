type Props = {
  score: number | null | undefined;
  threshold?: number | null;
  className?: string;
};

export function ScoreBadge({ score, threshold, className = "" }: Props) {
  if (score === null || score === undefined) {
    return (
      <span
        className={`inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-full border border-border bg-panel-strong/55 px-3 py-1 text-xs font-medium text-muted ${className}`.trim()}
      >
        尚未打分
      </span>
    );
  }

  const passed = typeof threshold === "number" ? score >= threshold : undefined;
  const tone =
    passed === undefined
      ? "border-[rgba(201,154,84,0.34)] bg-[linear-gradient(180deg,rgba(201,154,84,0.2),rgba(201,154,84,0.08))] text-accent-soft"
      : passed
        ? "border-[rgba(125,190,122,0.34)] bg-[linear-gradient(180deg,rgba(125,190,122,0.18),rgba(125,190,122,0.08))] text-[#a9dfa4]"
        : "border-[rgba(213,164,92,0.34)] bg-[linear-gradient(180deg,rgba(213,164,92,0.18),rgba(213,164,92,0.08))] text-[#f0c582]";

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
