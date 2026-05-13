import { useState } from "react";
import type { Review } from "../types";
import { StatusBadge } from "./StatusBadge";

type ReviewInsight = Record<string, unknown>;
type ReviewScalar = string | number | boolean;
type ReviewBadge = {
  key: string;
  value: string;
  priority: "primary" | "secondary";
};
type ReviewInsightVariant = "cards" | "table";

const REVIEW_SUMMARY_KEYS = ["message", "title", "summary", "description", "reason"] as const;
const REVIEW_ITEM_KEYS = ["id", "task_id", "task", "name"] as const;
const REVIEW_STATUS_KEYS = ["status", "state"] as const;
const REVIEW_VERIFIED_KEYS = ["verified", "passed"] as const;
const REVIEW_PRIMARY_BADGE_KEYS = ["kind", "severity"] as const;
const REVIEW_SECONDARY_BADGE_KEYS = ["mount", "dimension"] as const;
const REVIEW_LOCATION_KEYS = ["file", "path", "line"] as const;
const REVIEW_PROMOTED_KEYS = new Set<string>([
  ...REVIEW_SUMMARY_KEYS,
  ...REVIEW_PRIMARY_BADGE_KEYS,
  ...REVIEW_SECONDARY_BADGE_KEYS,
  ...REVIEW_LOCATION_KEYS,
]);

function readScoreNumber(review: Review, key: string) {
  const value = review.score_breakdown?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function getReviewScoreParts(review: Review) {
  const planScoreA = readScoreNumber(review, "plan_score_a");
  const actualScoreB = readScoreNumber(review, "actual_score_b");
  const formulaScore =
    planScoreA !== null && actualScoreB !== null
      ? Math.round((planScoreA * actualScoreB) / 100)
      : null;
  const finalScore = readScoreNumber(review, "final_score") ?? formulaScore ?? review.score;

  return { planScoreA, actualScoreB, finalScore };
}

function formatScore(value: number | null) {
  return value === null ? "-" : String(value);
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function isReviewScalar(value: unknown): value is ReviewScalar {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
}

function formatReviewScalar(value: ReviewScalar) {
  return typeof value === "boolean" ? (value ? "true" : "false") : String(value);
}

function readReviewScalar(item: ReviewInsight, key: string) {
  const value = item[key];
  if (!isReviewScalar(value)) return null;
  const rendered = formatReviewScalar(value).trim();
  return rendered || null;
}

function readFirstReviewScalar(
  item: ReviewInsight,
  keys: readonly string[],
) {
  for (const key of keys) {
    const value = readReviewScalar(item, key);
    if (value) return { key, value };
  }
  return null;
}

function getReviewSummary(item: ReviewInsight) {
  for (const key of REVIEW_SUMMARY_KEYS) {
    const value = readReviewScalar(item, key);
    if (value) return { key, value };
  }

  for (const [key, value] of Object.entries(item)) {
    if (!REVIEW_PROMOTED_KEYS.has(key) && isReviewScalar(value)) {
      return { key, value: formatReviewScalar(value) };
    }
  }

  return { key: null, value: "未提供摘要" };
}

function getReviewBadges(item: ReviewInsight) {
  const primaryBadges: ReviewBadge[] = REVIEW_PRIMARY_BADGE_KEYS.flatMap((key) => {
    const value = readReviewScalar(item, key);
    return value ? [{ key, value, priority: "primary" as const }] : [];
  });
  const secondaryBadges: ReviewBadge[] = REVIEW_SECONDARY_BADGE_KEYS.flatMap((key) => {
    const value = readReviewScalar(item, key);
    return value ? [{ key, value, priority: "secondary" as const }] : [];
  });

  return [...primaryBadges, ...secondaryBadges];
}

function getReviewLocation(item: ReviewInsight) {
  const file = readReviewScalar(item, "file") ?? readReviewScalar(item, "path");
  const line = readReviewScalar(item, "line");
  if (file && line) return `${file}:${line}`;
  if (file) return file;
  if (line) return `line ${line}`;
  return null;
}

function getReviewDetails(item: ReviewInsight, summaryKey: string | null) {
  return Object.entries(item).flatMap(([key, value]) => {
    if (key === summaryKey || REVIEW_PROMOTED_KEYS.has(key) || !isReviewScalar(value)) {
      return [];
    }
    return [[key, value] as [string, ReviewScalar]];
  });
}

function getBadgeToneClassName(badge: ReviewBadge) {
  if (badge.key === "severity") {
    const normalized = badge.value.toLowerCase();
    if (["critical", "error", "danger", "high"].includes(normalized)) {
      return "border-danger/45 bg-danger/18 text-[#d4876f]";
    }
    if (["warn", "warning", "medium"].includes(normalized)) {
      return "border-warning/45 bg-warning/18 text-[#d6a461]";
    }
    return "border-border-strong bg-panel-strong/70 text-copy-soft";
  }

  if (badge.key === "kind") {
    return "border-accent/45 bg-accent/20 text-accent-soft";
  }

  return "border-border bg-panel-strong/50 text-muted";
}

function ReviewBadgePill({ badge }: { badge: ReviewBadge }) {
  const baseClassName =
    badge.priority === "primary"
      ? "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-xs font-semibold"
      : "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10px]";

  return (
    <span className={`${baseClassName} ${getBadgeToneClassName(badge)}`}>
      <span className="text-muted-soft">{badge.key}</span>
      <span>{badge.value}</span>
    </span>
  );
}

function ReviewTableBadge({ badge }: { badge: ReviewBadge }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] ${getBadgeToneClassName(badge)}`}
    >
      <span className="text-muted-soft">{badge.key}</span>
      <span>{badge.value}</span>
    </span>
  );
}

function ReviewScalarTone({ value }: { value: string | null }) {
  if (!value) return <span className="text-muted-soft">-</span>;

  const normalized = value.toLowerCase();
  const className =
    normalized === "true" || normalized === "done" || normalized === "passed"
      ? "border-success/35 bg-success/12 text-[#8fcf9f]"
      : normalized === "false" ||
          normalized === "failed" ||
          normalized === "blocked" ||
          normalized === "unverified"
        ? "border-danger/35 bg-danger/12 text-[#d4876f]"
        : normalized === "deferred"
          ? "border-warning/35 bg-warning/12 text-[#d6a461]"
          : "border-border bg-panel-strong/55 text-muted";

  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 font-mono text-[10px] ${className}`}>
      {value}
    </span>
  );
}

function ReviewInsightCard({ item }: { item: ReviewInsight }) {
  const summary = getReviewSummary(item);
  const badges = getReviewBadges(item);
  const location = getReviewLocation(item);
  const details = getReviewDetails(item, summary.key);

  return (
    <li className="rounded-2xl border border-border bg-panel-deep/70 p-3">
      {badges.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          {badges.map((badge) => (
            <ReviewBadgePill badge={badge} key={`${badge.key}:${badge.value}`} />
          ))}
        </div>
      ) : null}
      <p className={badges.length > 0 ? "mt-3 text-sm leading-relaxed text-copy" : "text-sm leading-relaxed text-copy"}>
        {summary.value}
      </p>
      {location ? (
        <p className="mt-2 break-all font-mono text-[11px] text-muted">{location}</p>
      ) : null}
      {details.length > 0 ? (
        <dl className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {details.map(([key, value]) => (
            <div className="rounded-xl border border-border/70 bg-panel-strong/45 px-3 py-2" key={key}>
              <dt className="font-mono text-[10px] text-muted-soft">{key}</dt>
              <dd className="mt-1 break-words text-xs text-muted">
                {formatReviewScalar(value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </li>
  );
}

function ReviewTableDetails({
  details,
}: {
  details: [string, ReviewScalar][];
}) {
  if (details.length === 0) return <span className="text-muted-soft">-</span>;

  return (
    <div className="flex flex-wrap gap-1.5">
      {details.map(([key, value]) => (
        <span
          className="inline-flex max-w-full items-center gap-1 rounded-md border border-border/70 bg-panel-strong/45 px-1.5 py-0.5 font-mono text-[10px] text-muted"
          key={key}
          title={`${key}: ${formatReviewScalar(value)}`}
        >
          <span className="shrink-0 text-muted-soft">{key}</span>
          <span className="truncate">{formatReviewScalar(value)}</span>
        </span>
      ))}
    </div>
  );
}

function getReviewTableRows(items: ReviewInsight[]) {
  return items.map((item, index) => {
    const summary = getReviewSummary(item);
    const itemLabel = readFirstReviewScalar(item, REVIEW_ITEM_KEYS);
    const status = readFirstReviewScalar(item, REVIEW_STATUS_KEYS);
    const verified = readFirstReviewScalar(item, REVIEW_VERIFIED_KEYS);
    const usedKeys = new Set(
      [
        summary.key,
        itemLabel?.key,
        status?.key,
        verified?.key,
      ].filter((key): key is string => Boolean(key)),
    );
    const title = itemLabel?.value ?? summary.value;
    const details = Object.entries(item).flatMap(([key, value]) => {
      if (usedKeys.has(key) || REVIEW_PROMOTED_KEYS.has(key) || !isReviewScalar(value)) {
        return [];
      }
      return [[key, value] as [string, ReviewScalar]];
    });

    return {
      key: `${title}-${index}`,
      title,
      status: status?.value ?? null,
      verified: verified?.value ?? null,
      badges: getReviewBadges(item),
      location: getReviewLocation(item),
      details,
    };
  });
}

function ReviewInsightTable({
  title,
  items,
}: {
  title: string;
  items: ReviewInsight[];
}) {
  const rows = getReviewTableRows(items);

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-panel-deep/70">
      <div className="max-h-[360px] overflow-auto">
        <table aria-label={title} className="min-w-[760px] w-full border-collapse text-left text-xs">
          <thead className="sticky top-0 z-10 bg-panel-strong/95 text-[10px] uppercase text-muted-soft">
            <tr className="border-b border-border/70">
              <th className="w-[18%] px-3 py-2 font-medium">条目</th>
              <th className="w-[10%] px-3 py-2 font-medium">状态</th>
              <th className="w-[10%] px-3 py-2 font-medium">核验</th>
              <th className="w-[18%] px-3 py-2 font-medium">标签</th>
              <th className="w-[20%] px-3 py-2 font-medium">位置</th>
              <th className="px-3 py-2 font-medium">补充</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {rows.map((row) => (
              <tr className="align-top hover:bg-panel-strong/35" key={row.key}>
                <td className="break-words px-3 py-2 font-mono text-[11px] font-semibold text-copy">
                  {row.title}
                </td>
                <td className="px-3 py-2">
                  <ReviewScalarTone value={row.status} />
                </td>
                <td className="px-3 py-2">
                  <ReviewScalarTone value={row.verified} />
                </td>
                <td className="px-3 py-2">
                  {row.badges.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5">
                      {row.badges.map((badge) => (
                        <ReviewTableBadge badge={badge} key={`${badge.key}:${badge.value}`} />
                      ))}
                    </div>
                  ) : (
                    <span className="text-muted-soft">-</span>
                  )}
                </td>
                <td className="break-all px-3 py-2 font-mono text-[11px] text-muted">
                  {row.location ?? <span className="text-muted-soft">-</span>}
                </td>
                <td className="px-3 py-2">
                  <ReviewTableDetails details={row.details} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ReviewInsightSection({
  title,
  items,
  variant = "cards",
}: {
  title: string;
  items: ReviewInsight[] | null;
  variant?: ReviewInsightVariant;
}) {
  if (!items || items.length === 0) return null;

  return (
    <section className="mt-4">
      <div className="mb-2 flex items-center gap-2">
        <h4 className="text-sm font-semibold text-copy">{title}</h4>
        <span className="rounded-full border border-border bg-panel-deep px-2 py-0.5 text-[10px] text-muted">
          {items.length}
        </span>
      </div>
      {variant === "table" ? (
        <ReviewInsightTable items={items} title={title} />
      ) : (
        <ol className="space-y-2">
          {items.map((item, index) => (
            <ReviewInsightCard item={item} key={`${title}-${index}`} />
          ))}
        </ol>
      )}
    </section>
  );
}

function ReviewScoreMetric({
  label,
  value,
  hint,
}: {
  label: string;
  value: number | null;
  hint?: string;
}) {
  return (
    <div className="rounded-2xl border border-border/70 bg-panel-deep/65 px-3 py-2">
      <dt className="text-[11px] text-muted-soft">{label}</dt>
      <dd className="mt-1 font-mono text-lg font-semibold text-copy">{formatScore(value)}</dd>
      {hint ? <p className="mt-1 font-mono text-[10px] text-muted-soft">{hint}</p> : null}
    </div>
  );
}

function ReviewScoreBreakdown({ review }: { review: Review }) {
  const { planScoreA, actualScoreB, finalScore } = getReviewScoreParts(review);

  return (
    <dl className="mt-4 grid gap-2 sm:grid-cols-3">
      <ReviewScoreMetric label="开发计划分 a" value={planScoreA} />
      <ReviewScoreMetric label="实施分 b" value={actualScoreB} />
      <ReviewScoreMetric
        hint="round(a*b / 100)"
        label="最终评分"
        value={finalScore}
      />
    </dl>
  );
}

function ReviewRoundList({
  reviews,
  selectedId,
  onSelect,
}: {
  reviews: Review[];
  selectedId: string | null;
  onSelect: (review: Review) => void;
}) {
  return (
    <ol aria-label="审核轮次列表" className="space-y-2">
      {reviews.map((review) => {
        const selected = selectedId === review.id;
        const { planScoreA, actualScoreB, finalScore } = getReviewScoreParts(review);
        return (
          <li key={review.id}>
            <button
              aria-pressed={selected}
              className={[
                "w-full rounded-2xl border px-3 py-2 text-left text-xs transition",
                selected
                  ? "border-accent/40 bg-accent/10 text-copy"
                  : "border-border bg-panel-strong/60 text-muted hover:border-copy/20 hover:text-copy",
              ].join(" ")}
              onClick={() => onSelect(review)}
              type="button"
            >
              <div className="flex items-center justify-between gap-2">
                <p className="font-medium text-copy">第 {review.round} 轮</p>
                <span className="font-mono text-[11px] text-copy">
                  {formatScore(finalScore)}
                </span>
              </div>
              <p className="mt-1 font-mono text-[11px] text-muted">
                a {formatScore(planScoreA)} / b {formatScore(actualScoreB)}
              </p>
              <p className="mt-1 truncate text-[11px] text-muted-soft">
                {formatDateTime(review.created_at)}
              </p>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

export function ReviewHistory({ reviews }: { reviews: Review[] }) {
  const [selectedReviewId, setSelectedReviewId] = useState<string | null>(null);
  const selectedReview =
    reviews.find((review) => review.id === selectedReviewId) ?? reviews[0] ?? null;
  const effectiveReviewId = selectedReview?.id ?? null;

  if (!selectedReview) {
    return (
      <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
        选择一个审核轮次查看内容。
      </p>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <div className="max-h-[calc(100vh-18rem)] overflow-y-auto pr-1">
        <ReviewRoundList
          onSelect={(review) => setSelectedReviewId(review.id)}
          reviews={reviews}
          selectedId={effectiveReviewId}
        />
      </div>
      <ReviewRow review={selectedReview} />
    </div>
  );
}

export function ReviewRow({ review }: { review: Review }) {
  const { finalScore } = getReviewScoreParts(review);

  return (
    <article className="rounded-2xl border border-border bg-panel-strong/80 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm font-medium text-copy">
          第 {review.round} 轮 · 最终评分 {formatScore(finalScore)}
        </p>
        {review.problem_category ? <StatusBadge status={review.problem_category} /> : null}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        {review.reviewer ? <span>审核者 {review.reviewer}</span> : null}
        <span>创建时间 {formatDateTime(review.created_at)}</span>
      </div>
      <ReviewScoreBreakdown review={review} />
      <ReviewInsightSection items={review.issues} title="问题" />
      <ReviewInsightSection items={review.findings} title="发现项" variant="table" />
      <ReviewInsightSection items={review.next_round_hints} title="下一轮提示" />
    </article>
  );
}
