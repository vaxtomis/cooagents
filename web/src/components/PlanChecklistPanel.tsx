import { CheckCircle2, Circle, CircleSlash2, CornerDownRight, ListChecks } from "lucide-react";

export type PlanChecklistItem = {
  id: string;
  label: string;
  importance: PlanImportance | null;
  checked: boolean;
  cancelled: boolean;
  children: PlanChecklistItem[];
  review: PlanReviewState | null;
};

export type PlanChecklist = {
  items: PlanChecklistItem[];
  total: number;
  completed: number;
  cancelled: number;
  exitBlocking: number;
};

type DraftPlanItem = PlanChecklistItem & {
  depth: number;
};

export type PlanImportance = "P0" | "P1" | "P2";

export type PlanReviewState = {
  status: string | null;
  implemented: boolean | null;
  verified: boolean | null;
  requiredForExit: boolean | null;
  missingEvidence: string[];
};

const PLAN_HEADING = "开发计划";
const H2_RE = /^##\s+(.+?)\s*$/;
const TASK_RE = /^(\s*)[-*]\s+\[([ xX])\]\s+(.+?)\s*$/;
const PLAN_ID_RE = /^([A-Za-z][A-Za-z0-9_-]*-\d+(?:\.\d+)*)(?:[:：]\s*)?(.*)$/;
const PLAN_IMPORTANCE_RE = /^\[(P[0-2])\]\s*(.*)$/;

function stripInlineMarkdown(value: string) {
  return value
    .replace(/~~/g, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .trim();
}

function extractPlanLines(content: string) {
  const lines = content.split(/\r?\n/);
  const start = lines.findIndex((line) => H2_RE.exec(line)?.[1].trim() === PLAN_HEADING);
  if (start < 0) return [];
  const bodyStart = start + 1;
  const nextHeading = lines.findIndex((line, index) => index > start && H2_RE.test(line));
  return lines.slice(bodyStart, nextHeading < 0 ? undefined : nextHeading);
}

function parentIdFor(id: string) {
  const dot = id.lastIndexOf(".");
  return dot > 0 ? id.slice(0, dot) : null;
}

function readString(item: Record<string, unknown>, key: string) {
  const value = item[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function readBoolean(item: Record<string, unknown>, key: string) {
  const value = item[key];
  return typeof value === "boolean" ? value : null;
}

function readMissingEvidence(item: Record<string, unknown>) {
  const value = item.missing_evidence ?? item.missingEvidence;
  if (!Array.isArray(value)) return [];
  return value
    .filter((entry): entry is string => typeof entry === "string")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function normalizeImportance(value: string | null): PlanImportance | null {
  return value === "P0" || value === "P1" || value === "P2" ? value : null;
}

function buildReviewMap(planVerification: Record<string, unknown>[] | null | undefined) {
  const map = new Map<string, PlanReviewState>();
  for (const item of planVerification ?? []) {
    const id = readString(item, "id");
    if (!id) continue;
    map.set(id, {
      status: readString(item, "status"),
      implemented: readBoolean(item, "implemented"),
      verified: readBoolean(item, "verified"),
      requiredForExit: readBoolean(item, "required_for_exit") ?? readBoolean(item, "requiredForExit"),
      missingEvidence: readMissingEvidence(item),
    });
  }
  return map;
}

function parseImportance(label: string) {
  const match = PLAN_IMPORTANCE_RE.exec(label);
  if (!match) return { importance: null, label };
  return {
    importance: normalizeImportance(match[1]),
    label: match[2].trim() || label,
  };
}

function isExitBlocking(item: PlanChecklistItem) {
  if (item.cancelled || item.review?.requiredForExit !== true) return false;
  const statusDone = item.review.status === "done";
  const delivered = item.review.implemented !== false;
  return !(statusDone && delivered);
}

function countExitBlocking(items: PlanChecklistItem[]): number {
  return items.reduce(
    (count, item) =>
      count + (isExitBlocking(item) ? 1 : 0) + countExitBlocking(item.children),
    0,
  );
}

export function extractPlanChecklist(
  content: string | null | undefined,
  planVerification?: Record<string, unknown>[] | null,
): PlanChecklist | null {
  if (!content) return null;
  const lines = extractPlanLines(content);
  if (lines.length === 0) return null;

  const reviewById = buildReviewMap(planVerification);
  const roots: DraftPlanItem[] = [];
  const byId = new Map<string, DraftPlanItem>();
  const stack: DraftPlanItem[] = [];
  let total = 0;
  let completed = 0;
  let cancelled = 0;

  for (const line of lines) {
    const task = TASK_RE.exec(line);
    if (!task) continue;
    const indent = task[1].replace(/\t/g, "  ").length;
    const rawBody = task[3].trim();
    const cleanedBody = stripInlineMarkdown(rawBody);
    const idMatch = PLAN_ID_RE.exec(cleanedBody);
    if (!idMatch) continue;

    const id = idMatch[1];
    const parsed = parseImportance(idMatch[2].trim() || id);
    const item: DraftPlanItem = {
      id,
      label: parsed.label,
      importance: parsed.importance,
      checked: task[2].toLowerCase() === "x",
      cancelled: rawBody.includes("~~"),
      children: [],
      review: reviewById.get(id) ?? null,
      depth: Math.max(Math.floor(indent / 2), id.includes(".") ? 1 : 0),
    };
    total += 1;
    if (item.checked && !item.cancelled) completed += 1;
    if (item.cancelled) cancelled += 1;

    const explicitParent = parentIdFor(id);
    const parent =
      (explicitParent ? byId.get(explicitParent) : undefined) ??
      [...stack].reverse().find((candidate) => candidate.depth < item.depth);
    if (parent) {
      parent.children.push(item);
    } else {
      roots.push(item);
    }
    byId.set(id, item);
    stack[item.depth] = item;
    stack.length = item.depth + 1;
  }

  if (total === 0) return null;
  return { items: roots, total, completed, cancelled, exitBlocking: countExitBlocking(roots) };
}

function PlanStatusIcon({ item }: { item: PlanChecklistItem }) {
  if (item.cancelled) {
    return <CircleSlash2 aria-hidden className="mt-0.5 size-4 text-muted-soft" />;
  }
  if (item.checked) {
    return <CheckCircle2 aria-hidden className="mt-0.5 size-4 text-success" />;
  }
  return <Circle aria-hidden className="mt-0.5 size-4 text-muted" />;
}

function statusLabel(item: PlanChecklistItem) {
  if (item.cancelled) return "取消";
  if (item.checked) return "完成";
  return "待执行";
}

function importanceLabel(importance: PlanImportance) {
  if (importance === "P0") return "P0 准出必需";
  if (importance === "P1") return "P1 常规";
  return "P2 可延期";
}

function importanceClassName(importance: PlanImportance) {
  if (importance === "P0") return "border-danger/35 bg-danger/12 text-[#d4876f]";
  if (importance === "P1") return "border-accent/35 bg-accent/12 text-accent-soft";
  return "border-border-dark/45 bg-panel-deep/60 text-muted";
}

function reviewStatusLabel(item: PlanChecklistItem) {
  const review = item.review;
  if (!review) return null;
  if (review.status === "deferred" && review.requiredForExit === false) {
    return "已延期，不阻断";
  }
  if (review.status === "done" && review.implemented !== false && review.verified === false) {
    return "已交付 / 证据不足";
  }
  if (review.status === "done" && review.implemented !== false && review.verified === true) {
    return "已交付 / 已验证";
  }
  if (review.status === "partial") return "部分完成";
  if (review.status === "blocked") return "阻塞";
  if (review.status === "failed") return "失败";
  if (review.status === "unverified") return "未验证";
  return review.status;
}

function ReviewBadges({ item }: { item: PlanChecklistItem }) {
  const reviewLabel = reviewStatusLabel(item);
  const blocking = isExitBlocking(item);
  if (!reviewLabel && !blocking) return null;

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      {reviewLabel ? (
        <span className="rounded-full border border-border-dark/45 bg-panel-deep/60 px-2 py-0.5 text-[11px] text-muted">
          {reviewLabel}
        </span>
      ) : null}
      {blocking ? (
        <span className="rounded-full border border-danger/35 bg-danger/12 px-2 py-0.5 text-[11px] text-[#d4876f]">
          阻断准出
        </span>
      ) : null}
    </div>
  );
}

function PlanItem({ item, child = false }: { item: PlanChecklistItem; child?: boolean }) {
  return (
    <li>
      <div
        className={[
          "group grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-3 border-b border-border/55 py-3 last:border-b-0",
          child ? "pl-4" : "",
          item.cancelled ? "text-muted-soft" : "text-copy-soft",
        ].join(" ")}
      >
        {child ? (
          <CornerDownRight aria-hidden className="mt-0.5 size-4 text-border-dark" />
        ) : (
          <PlanStatusIcon item={item} />
        )}
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-md border border-border-dark/50 bg-panel-deep/70 px-2 py-0.5 font-mono text-[11px] text-copy">
              {item.id}
            </span>
            {item.importance ? (
              <span
                className={`rounded-full border px-2 py-0.5 text-[10px] ${importanceClassName(item.importance)}`}
              >
                {importanceLabel(item.importance)}
              </span>
            ) : null}
            <span className={item.cancelled ? "line-through decoration-muted-soft" : ""}>
              {item.label}
            </span>
          </div>
          <ReviewBadges item={item} />
          {item.review?.missingEvidence.length ? (
            <ul className="mt-2 space-y-1 text-[11px] text-muted">
              {item.review.missingEvidence.map((evidence) => (
                <li key={evidence}>{evidence}</li>
              ))}
            </ul>
          ) : null}
          {item.children.length > 0 ? (
            <ol className="mt-2 border-l border-border-dark/45 pl-3">
              {item.children.map((childItem) => (
                <PlanItem child item={childItem} key={childItem.id} />
              ))}
            </ol>
          ) : null}
        </div>
        <span
          className={[
            "rounded-full border px-2 py-0.5 text-[11px]",
            item.cancelled
              ? "border-muted-soft/25 text-muted-soft"
              : item.checked
                ? "border-success/25 bg-success/10 text-success"
                : "border-border-dark/45 text-muted",
          ].join(" ")}
        >
          {statusLabel(item)}
        </span>
      </div>
    </li>
  );
}

export function PlanChecklistPanel({
  content,
  planVerification,
  className = "",
}: {
  content: string | null | undefined;
  planVerification?: Record<string, unknown>[] | null;
  className?: string;
}) {
  const plan = extractPlanChecklist(content, planVerification);
  if (!plan) return null;
  const activeTotal = Math.max(plan.total - plan.cancelled, 0);

  return (
    <section
      aria-label="开发计划结构化视图"
      className={[
        "rounded-[24px] border border-border bg-panel-strong/80 p-4 shadow-panel",
        className,
      ].join(" ")}
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 pb-3">
        <div className="flex min-w-0 items-center gap-2">
          <ListChecks aria-hidden className="size-4 text-accent-soft" />
          <h3 className="text-sm font-semibold text-copy">开发计划</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
          <span className="rounded-full border border-border-dark/45 px-2 py-0.5">
            {plan.completed}/{activeTotal} 完成
          </span>
          {plan.cancelled > 0 ? (
            <span className="rounded-full border border-muted-soft/25 px-2 py-0.5">
              {plan.cancelled} 取消
            </span>
          ) : null}
          {plan.exitBlocking > 0 ? (
            <span className="rounded-full border border-danger/35 bg-danger/12 px-2 py-0.5 text-[#d4876f]">
              {plan.exitBlocking} 个准出阻断
            </span>
          ) : null}
        </div>
      </div>
      <ol className="mt-1">
        {plan.items.map((item) => (
          <PlanItem item={item} key={item.id} />
        ))}
      </ol>
    </section>
  );
}
