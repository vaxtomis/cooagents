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

export function ReviewRow({ review }: { review: Review }) {
  return (
    <article className="rounded-2xl border border-border bg-panel-strong/80 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm font-medium text-copy">
          第 {review.round} 轮 · 评分 {review.score ?? "-"}
        </p>
        {review.problem_category ? <StatusBadge status={review.problem_category} /> : null}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        {review.reviewer ? <span>审核者 {review.reviewer}</span> : null}
        <span>创建时间 {formatDateTime(review.created_at)}</span>
      </div>
      <ReviewInsightSection items={review.issues} title="问题" />
      <ReviewInsightSection items={review.findings} title="发现项" variant="table" />
      <ReviewInsightSection items={review.next_round_hints} title="下一轮提示" />
    </article>
  );
}
