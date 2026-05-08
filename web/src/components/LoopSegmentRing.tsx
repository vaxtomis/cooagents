type LoopSegmentRingProps = {
  label: string;
  value: number;
  completed: number;
  max: number;
  active?: boolean;
  maxReached?: boolean;
  className?: string;
};

const SIZE = 42;
const CENTER = SIZE / 2;
const RADIUS = 17;
const STROKE_WIDTH = 4;
const MAX_SEGMENTS = 50;

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function normalizeCount(value: number) {
  return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
}

function polarToCartesian(angle: number) {
  const radians = ((angle - 90) * Math.PI) / 180;
  return {
    x: CENTER + RADIUS * Math.cos(radians),
    y: CENTER + RADIUS * Math.sin(radians),
  };
}

function describeArc(startAngle: number, endAngle: number) {
  const start = polarToCartesian(endAngle);
  const end = polarToCartesian(startAngle);
  const largeArcFlag = endAngle - startAngle <= 180 ? "0" : "1";
  return `M ${start.x.toFixed(3)} ${start.y.toFixed(3)} A ${RADIUS} ${RADIUS} 0 ${largeArcFlag} 0 ${end.x.toFixed(3)} ${end.y.toFixed(3)}`;
}

function segmentTone({
  index,
  active,
  activeIndex,
  completedCount,
  maxReached,
  segmentCount,
}: {
  index: number;
  active: boolean;
  activeIndex: number;
  completedCount: number;
  maxReached: boolean;
  segmentCount: number;
}) {
  if (maxReached && index === segmentCount - 1) return "maxed";
  if (index < completedCount) return "complete";
  if (active && index === activeIndex) return "current";
  return "pending";
}

const SEGMENT_CLASSNAME = {
  complete: "stroke-[#8fa46a]",
  current: "stroke-[#d79a4a]",
  maxed: "stroke-[#d06d53]",
  pending: "stroke-[rgba(215,226,188,0.16)]",
} as const;

export function LoopSegmentRing({
  label,
  value,
  completed,
  max,
  active = false,
  maxReached = false,
  className = "",
}: LoopSegmentRingProps) {
  const safeMax = normalizeCount(max);
  const segmentCount = clamp(safeMax || 1, 1, MAX_SEGMENTS);
  const normalizedCompleted = normalizeCount(completed);
  const completedCount = clamp(normalizedCompleted, 0, segmentCount);
  const displayValue = safeMax > 0 ? clamp(normalizeCount(value), 0, safeMax) : 0;
  const activeIndex = clamp(displayValue - 1, completedCount, segmentCount - 1);
  const reached = maxReached || (safeMax > 0 && normalizedCompleted >= safeMax);
  const sweep = 360 / segmentCount;
  const gap = segmentCount === 1 ? 0 : Math.min(5, Math.max(1.2, 70 / segmentCount));
  const statusText = reached ? "已达最大次数" : active ? "进行中" : "已记录";

  return (
    <div
      aria-label={`${label} ${displayValue}/${safeMax}\uFF0C${statusText}`}
      className={`relative inline-flex size-[42px] shrink-0 items-center justify-center ${className}`.trim()}
      role="img"
      title={`${label} ${displayValue}/${safeMax}\uFF0C${statusText}`}
    >
      <svg
        aria-hidden="true"
        className="absolute inset-0"
        height={SIZE}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        width={SIZE}
      >
        {Array.from({ length: segmentCount }, (_, index) => {
          const startAngle = index * sweep + gap / 2;
          const endAngle = (index + 1) * sweep - gap / 2;
          const tone = segmentTone({
            index,
            active,
            activeIndex,
            completedCount,
            maxReached: reached,
            segmentCount,
          });
          return (
            <path
              className={`transition-colors ${SEGMENT_CLASSNAME[tone]}`}
              d={describeArc(startAngle, endAngle)}
              data-state={tone}
              data-testid="loop-segment"
              fill="none"
              key={index}
              strokeLinecap="round"
              strokeWidth={STROKE_WIDTH}
            />
          );
        })}
      </svg>
      <span className="relative font-mono text-[0.82rem] font-semibold leading-none text-copy">
        {displayValue}
      </span>
    </div>
  );
}
