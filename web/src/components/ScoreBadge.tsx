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
      ? "border-[rgba(169,112,45,0.34)] bg-[linear-gradient(180deg,rgba(169,112,45,0.2),rgba(169,112,45,0.08))] text-accent-soft"
      : passed
        ? "border-[rgba(143,164,106,0.34)] bg-[linear-gradient(180deg,rgba(143,164,106,0.18),rgba(143,164,106,0.08))] text-[#c1cb9a]"
        : "border-[rgba(185,130,54,0.34)] bg-[linear-gradient(180deg,rgba(185,130,54,0.18),rgba(185,130,54,0.08))] text-[#d6a461]";

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
