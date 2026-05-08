import { useMemo, useState, type KeyboardEvent } from "react";
import { Link, useParams } from "react-router-dom";
import useSWR from "swr";
import { ApiError } from "../api/client";
import { cancelDevWork, getDevWork } from "../api/devWorks";
import { getIterationNoteContent, listIterationNotes } from "../api/devIterationNotes";
import { getGate } from "../api/gates";
import { listReviews } from "../api/reviews";
import { listWorkspaceEvents } from "../api/workspaceEvents";
import { DevWorkStepProgress } from "../components/DevWorkStepProgress";
import { GateActionPanel } from "../components/GateActionPanel";
import { MarkdownPanel } from "../components/MarkdownPanel";
import { RepoPushStatusGrid } from "../components/RepoPushStatusGrid";
import { MetricCard, SectionPanel } from "../components/SectionPanel";
import { ScoreBadge } from "../components/ScoreBadge";
import { StatusBadge } from "../components/StatusBadge";
import {
  useWorkspaceActivePolling,
  useWorkspaceDetailPolling,
  useWorkspacePolling,
} from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type { DevIterationNote, DevWork, Review, WorkspaceEvent } from "../types";

const TAB_IDS = ["overview", "notes", "reviews", "gate", "activity"] as const;
type TabId = (typeof TAB_IDS)[number];
const TAB_LABELS: Record<TabId, string> = {
  overview: "总览",
  activity: "Activity",
  notes: "迭代设计文件",
  reviews: "审核历史",
  gate: "闸门",
};

// Path-segment shape for DevWork ids — the gate_id is composed from this and
// is then sent through encodeURIComponent in the API client. The validation is
// defence-in-depth so a malformed URL never produces a surprising gate key.
function formatDateTime(value: string | null | undefined) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

const DEV_WORK_EVENT_NAMES = [
  "dev_work.started",
  "dev_work.progress",
  "dev_work.step_completed",
  "dev_work.round_completed",
  "dev_work.score_passed",
  "dev_work.escalated",
  "dev_work.cancelled",
  "dev_work.completed",
  "dev_work.gate.exit_waiting",
  "dev_work.merge_conflict",
] as const;

const DEV_WORK_ID_RE = /^[a-zA-Z0-9_-]+$/;

type ReviewInsight = Record<string, unknown>;

const REVIEW_SUMMARY_KEYS = ["message", "title", "summary", "description", "reason"] as const;
const REVIEW_BADGE_KEYS = ["kind", "severity", "mount"] as const;
const REVIEW_LOCATION_KEYS = ["file", "path", "line"] as const;
const REVIEW_PROMOTED_KEYS = new Set<string>([
  ...REVIEW_SUMMARY_KEYS,
  ...REVIEW_BADGE_KEYS,
  ...REVIEW_LOCATION_KEYS,
]);

function isReviewScalar(value: unknown): value is string | number | boolean {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
}

function formatReviewScalar(value: string | number | boolean) {
  return typeof value === "boolean" ? (value ? "true" : "false") : String(value);
}

function readReviewScalar(item: ReviewInsight, key: string) {
  const value = item[key];
  if (!isReviewScalar(value)) return null;
  const rendered = formatReviewScalar(value).trim();
  return rendered || null;
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
  return REVIEW_BADGE_KEYS.map((key) => {
    const value = readReviewScalar(item, key);
    return value ? `${key}: ${value}` : null;
  }).filter((value): value is string => Boolean(value));
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
    return [[key, value] as [string, string | number | boolean]];
  });
}

function ReviewInsightCard({ item }: { item: ReviewInsight }) {
  const summary = getReviewSummary(item);
  const badges = getReviewBadges(item);
  const location = getReviewLocation(item);
  const details = getReviewDetails(item, summary.key);

  return (
    <li className="rounded-2xl border border-border bg-panel-deep/70 p-3">
      <div className="flex flex-wrap items-center gap-2">
        {badges.map((badge) => (
          <span
            className="rounded-full border border-border-strong bg-panel-strong/70 px-2 py-0.5 font-mono text-[10px] text-muted"
            key={badge}
          >
            {badge}
          </span>
        ))}
      </div>
      <p className={badges.length > 0 ? "mt-2 text-sm text-copy" : "text-sm text-copy"}>
        {summary.value}
      </p>
      {location ? (
        <p className="mt-2 break-all font-mono text-[11px] text-muted">{location}</p>
      ) : null}
      {details.length > 0 ? (
        <dl className="mt-3 grid gap-2 sm:grid-cols-2">
          {details.map(([key, value]) => (
            <div className="rounded-xl border border-border/70 bg-panel-strong/45 px-3 py-2" key={key}>
              <dt className="font-mono text-[10px] uppercase text-muted-soft">{key}</dt>
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

function ReviewInsightSection({
  title,
  items,
}: {
  title: string;
  items: ReviewInsight[] | null;
}) {
  if (!items || items.length === 0) return null;

  return (
    <section className="mt-4">
      <div className="mb-2 flex items-center gap-2">
        <h4 className="text-xs font-semibold text-copy">{title}</h4>
        <span className="rounded-full border border-border bg-panel-deep px-2 py-0.5 text-[10px] text-muted">
          {items.length}
        </span>
      </div>
      <ol className="space-y-2">
        {items.map((item, index) => (
          <ReviewInsightCard item={item} key={`${title}-${index}`} />
        ))}
      </ol>
    </section>
  );
}

function ReviewRow({ review }: { review: Review }) {
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
      <ReviewInsightSection items={review.findings} title="发现项" />
      <ReviewInsightSection items={review.next_round_hints} title="下一轮提示" />
    </article>
  );
}

function compactPayload(payload: WorkspaceEvent["payload"]) {
  if (!payload) return null;
  return Object.entries(payload)
    .filter(([, value]) => value !== null && value !== undefined)
    .slice(0, 4)
    .map(([key, value]) => {
      const rendered = typeof value === "object" ? JSON.stringify(value) : String(value);
      return `${key}: ${rendered}`;
    })
    .join(" / ");
}

function ActivityRow({ event }: { event: WorkspaceEvent }) {
  const payload = compactPayload(event.payload);
  return (
    <article className="rounded-2xl border border-border bg-panel-strong/80 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-xs text-copy">{event.event_name}</p>
        <span className="text-[11px] text-muted">{formatDateTime(event.ts)}</span>
      </div>
      {payload ? <p className="mt-2 break-words text-xs text-muted">{payload}</p> : null}
    </article>
  );
}

type NoteContentState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; content: string }
  | { kind: "missing" }
  | { kind: "error"; message: string };

function IterationNoteList({
  notes,
  selectedId,
  onSelect,
}: {
  notes: DevIterationNote[];
  selectedId: string | null;
  onSelect: (note: DevIterationNote) => void;
}) {
  return (
    <ol className="space-y-2">
      {notes.map((note) => {
        const selected = selectedId === note.id;
        return (
          <li key={note.id}>
            <button
              aria-pressed={selected}
              className={[
                "w-full rounded-2xl border px-3 py-2 text-left text-xs transition",
                selected
                  ? "border-accent/40 bg-accent/10 text-copy"
                  : "border-border bg-panel-strong/60 text-muted hover:border-copy/20 hover:text-copy",
              ].join(" ")}
              onClick={() => onSelect(note)}
              type="button"
            >
              <p className="font-medium text-copy">第 {note.round} 轮</p>
              <p className="mt-1 truncate font-mono text-[11px]">{note.markdown_path}</p>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

export function DevWorkPage() {
  const { wsId, dvId } = useParams();
  if (!wsId || !dvId) {
    return (
      <section className="rounded-[32px] border border-danger/15 bg-danger/8 p-6 shadow-panel">
        <h2 className="font-serif text-xl font-medium text-copy">路径参数缺失</h2>
      </section>
    );
  }
  if (!DEV_WORK_ID_RE.test(dvId)) {
    return (
      <section className="rounded-[32px] border border-danger/15 bg-danger/8 p-6 shadow-panel">
        <h2 className="font-serif text-xl font-medium text-copy">非法的 DevWork ID</h2>
      </section>
    );
  }
  return <DevWorkContent wsId={wsId} dvId={dvId} />;
}

function DevWorkContent({ wsId, dvId }: { wsId: string; dvId: string }) {
  const polling = useWorkspacePolling();
  const detailPolling = useWorkspaceDetailPolling<DevWork>((latest) => Boolean(latest?.is_running));
  const [tab, setTab] = useState<TabId>("overview");
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [actionPending, setActionPending] = useState<"cancel" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const dvQuery = useSWR(["dev-work", dvId], () => getDevWork(dvId), detailPolling);
  const notesQuery = useSWR(
    ["iteration-notes", dvId],
    () => listIterationNotes(dvId),
    polling,
  );
  const reviewsQuery = useSWR(
    ["reviews", "dev", dvId],
    () => listReviews({ dev_work_id: dvId }),
    polling,
  );

  const gateId = `dev:${dvId}:exit`;
  const gateQuery = useSWR(
    ["gate", gateId],
    () => getGate(gateId),
    { ...polling, shouldRetryOnError: false },
  );
  const activityPolling = useWorkspaceActivePolling(Boolean(dvQuery.data?.is_running));
  const workspaceEventsQuery = useSWR(
    ["workspace-events", "dev-work", wsId, dvId],
    () =>
      listWorkspaceEvents(wsId, {
        limit: 20,
        event_name: [...DEV_WORK_EVENT_NAMES],
        correlation_id: dvId,
      }),
    activityPolling,
  );

  const notesDesc = useMemo(
    () => (notesQuery.data ? [...notesQuery.data].reverse() : []),
    [notesQuery.data],
  );
  const reviewsDesc = useMemo(
    () => (reviewsQuery.data ? [...reviewsQuery.data].reverse() : []),
    [reviewsQuery.data],
  );

  const selectedNote =
    notesDesc.find((note) => note.id === selectedNoteId) ?? notesDesc[0] ?? null;
  const effectiveNoteId = selectedNote?.id ?? null;

  const noteContentQuery = useSWR(
    effectiveNoteId ? ["iteration-note-content", effectiveNoteId] : null,
    () => getIterationNoteContent(effectiveNoteId!),
    { shouldRetryOnError: false, revalidateOnFocus: false },
  );

  const noteContent = useMemo<NoteContentState>(() => {
    if (!effectiveNoteId) return { kind: "idle" };
    if (noteContentQuery.error) {
      const err = noteContentQuery.error;
      if (err instanceof ApiError) {
        if (err.status === 410) return { kind: "missing" };
        return { kind: "error", message: err.message };
      }
      return { kind: "error", message: "迭代设计加载失败" };
    }
    if (!noteContentQuery.data) return { kind: "loading" };
    return { kind: "ok", content: noteContentQuery.data };
  }, [effectiveNoteId, noteContentQuery.error, noteContentQuery.data]);

  if (dvQuery.error) {
    return (
      <section className="rounded-[32px] border border-danger/15 bg-danger/8 p-6 shadow-panel">
        <h2 className="font-serif text-xl font-medium text-copy">DevWork 加载失败</h2>
        <p className="mt-2 text-sm text-muted">{extractError(dvQuery.error, "")}</p>
      </section>
    );
  }

  const devWork = dvQuery.data;
  if (!devWork) {
    return <div className="h-[240px] animate-pulse rounded-[32px] border border-border bg-panel" />;
  }

  const escalated = devWork.current_step === "ESCALATED";
  const cancelled = devWork.current_step === "CANCELLED";
  const terminal = escalated || cancelled || devWork.current_step === "COMPLETED";
  const activityEvents = workspaceEventsQuery.data?.events ?? [];

  // Missing gate is an expected "no exit gate right now" state, not an error.
  const gateInfo =
    gateQuery.data ??
    (gateQuery.error instanceof ApiError && gateQuery.error.status === 404 ? null : undefined);

  async function cancelWork() {
    setActionPending("cancel");
    setActionError(null);
    try {
      await cancelDevWork(dvId);
      await dvQuery.mutate();
    } catch (err) {
      setActionError(extractError(err, "操作失败"));
    } finally {
      setActionPending(null);
    }
  }

  function activateTab(id: TabId) {
    setTab(id);
    window.setTimeout(() => {
      document.getElementById(`devwork-tab-${id}`)?.focus();
    }, 0);
  }

  function handleTabKey(event: KeyboardEvent<HTMLButtonElement>, id: TabId) {
    const currentIndex = TAB_IDS.indexOf(id);
    const lastIndex = TAB_IDS.length - 1;
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = currentIndex === lastIndex ? 0 : currentIndex + 1;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = currentIndex === 0 ? lastIndex : currentIndex - 1;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = lastIndex;
    }
    if (nextIndex === null) return;
    event.preventDefault();
    activateTab(TAB_IDS[nextIndex]);
  }

  return (
    <div className="space-y-6">
      <SectionPanel
        actions={
          <>
            <button
              className="rounded-lg bg-danger px-3 py-1.5 text-xs font-medium text-ink-invert disabled:opacity-50"
              disabled={actionPending !== null || terminal}
              onClick={() => void cancelWork()}
              type="button"
            >
              {actionPending === "cancel" ? "取消中..." : "取消"}
            </button>
            <Link className="text-xs text-muted hover:text-copy" to={`/workspaces/${wsId}`}>
              ← 返回 Workspace
            </Link>
          </>
        }
        density="compact"
        kicker="开发工作"
        title={devWork.id}
      >
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge status={devWork.current_step} />
          {devWork.is_running ? (
            <StatusBadge status="running" label="自动推进中" />
          ) : null}
          <span className="font-mono text-xs text-muted">文档：{devWork.design_doc_id}</span>
          <span className="text-sm text-muted">
            轮次 {devWork.iteration_rounds}
          </span>
          <span className="text-sm text-muted">
            更新时间 {formatDateTime(devWork.updated_at)}
          </span>
          <ScoreBadge score={devWork.last_score} />
        </div>

        <div className="mt-4">
          <DevWorkStepProgress current={devWork.current_step} />
        </div>

        {escalated ? (
          <p className="mt-4 rounded-2xl border border-warning/25 bg-warning/10 p-4 text-sm text-warning">
            DevWork 已升级，需人工介入；闸门面板已隐藏。
          </p>
        ) : null}

        {devWork.is_running ? (
          <p className="mt-4 rounded-2xl border border-success/25 bg-success/10 p-4 text-sm text-success">
            后台驱动正在推进此 DevWork，心跳进度会随轮询刷新。
          </p>
        ) : null}

        {actionError ? <p className="mt-3 text-xs text-danger">{actionError}</p> : null}
      </SectionPanel>

      <SectionPanel
        actions={
          <div
            className="flex max-w-full gap-2 overflow-x-auto pb-1"
            role="tablist"
            aria-label="DevWork 详情切换"
          >
            {TAB_IDS.map((id) => {
              const selected = tab === id;
              return (
                <button
                  aria-controls={`devwork-tabpanel-${id}`}
                  aria-selected={selected}
                  className={[
                    "shrink-0 rounded-full border px-3 py-1.5 text-xs font-medium transition",
                    selected
                      ? "border-accent/30 bg-accent/15 text-copy"
                      : "border-border-strong bg-panel-strong/50 text-muted hover:border-copy/20 hover:text-copy",
                  ].join(" ")}
                  id={`devwork-tab-${id}`}
                  key={id}
                  onClick={() => activateTab(id)}
                  onKeyDown={(event) => handleTabKey(event, id)}
                  role="tab"
                  tabIndex={selected ? 0 : -1}
                  type="button"
                >
                  {TAB_LABELS[id]}
                </button>
              );
            })}
          </div>
        }
        kicker="详情面板"
        title={TAB_LABELS[tab]}
      >
        {tab === "overview" ? (
          <div
            aria-labelledby="devwork-tab-overview"
            className="space-y-6"
            id="devwork-tabpanel-overview"
            role="tabpanel"
          >
            <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
              <MetricCard label="最近分数" value={devWork.last_score?.toString() ?? "-"} />
              <MetricCard label="问题分类" value={devWork.last_problem_category ?? "-"} />
              <MetricCard
                label="一次通过"
                value={
                  devWork.first_pass_success === null
                    ? "-"
                    : devWork.first_pass_success
                      ? "是"
                      : "否"
                }
              />
              <MetricCard label="工作分支" value={devWork.worktree_branch ?? "-"} />
              <MetricCard label="工作目录" value={devWork.worktree_path ?? "-"} />
              <MetricCard label="更新时间" value={formatDateTime(devWork.updated_at)} />
            </div>

            <div className="space-y-3">
              <h3 className="text-sm font-semibold text-copy">运行心跳</h3>
              {devWork.progress ? (
                <div className="grid gap-3 md:grid-cols-4">
                  <MetricCard label="Step" value={devWork.progress.step} />
                  <MetricCard label="Round" value={String(devWork.progress.round)} />
                  <MetricCard label="Elapsed" value={`${devWork.progress.elapsed_s}s`} />
                  <MetricCard
                    label="Heartbeat"
                    value={formatDateTime(devWork.progress.last_heartbeat_at)}
                  />
                </div>
              ) : (
                <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
                  暂无心跳快照；后台驱动可能处于非 LLM 子步骤或已经空闲。
                </p>
              )}
            </div>

            <div className="space-y-3">
              <h3 className="text-sm font-semibold text-copy">仓库与推送状态</h3>
              <RepoPushStatusGrid repos={devWork.repos ?? []} />
            </div>
          </div>
        ) : null}

        {tab === "notes" ? (
          <div
            aria-labelledby="devwork-tab-notes"
            className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]"
            id="devwork-tabpanel-notes"
            role="tabpanel"
          >
            <div className="max-h-[calc(100vh-18rem)] overflow-y-auto pr-1">
              {notesDesc.length === 0 ? (
                <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
                  暂无迭代设计文件。
                </p>
              ) : (
                <IterationNoteList
                  notes={notesDesc}
                  onSelect={(note) => setSelectedNoteId(note.id)}
                  selectedId={effectiveNoteId}
                />
              )}
            </div>
            <div>
              {noteContent.kind === "idle" ? (
                <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
                  选择一个迭代设计文件查看内容。
                </p>
              ) : noteContent.kind === "loading" ? (
                <div className="h-40 animate-pulse rounded-2xl border border-border bg-panel-strong/70" />
              ) : noteContent.kind === "missing" ? (
                <p className="rounded-2xl border border-warning/25 bg-warning/10 px-4 py-4 text-sm text-warning">
                  源文件已缺失，请运行 <code className="font-mono">POST /workspaces/sync</code> 后刷新。
                </p>
              ) : noteContent.kind === "error" ? (
                <p className="rounded-2xl border border-danger/25 bg-danger/10 px-4 py-4 text-sm text-danger">
                  {noteContent.message}
                </p>
              ) : (
                <MarkdownPanel content={noteContent.content} reader />
              )}
            </div>
          </div>
        ) : null}

        {tab === "reviews" ? (
          <div
            aria-labelledby="devwork-tab-reviews"
            id="devwork-tabpanel-reviews"
            role="tabpanel"
          >
            {reviewsQuery.error ? (
              <p className="text-xs text-danger">{extractError(reviewsQuery.error, "加载失败")}</p>
            ) : reviewsDesc.length === 0 ? (
              <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
                暂无审核记录。
              </p>
            ) : (
              <div className="space-y-3">
                {reviewsDesc.map((review) => (
                  <ReviewRow key={review.id} review={review} />
                ))}
              </div>
            )}
          </div>
        ) : null}

        {tab === "gate" ? (
          <div
            aria-labelledby="devwork-tab-gate"
            id="devwork-tabpanel-gate"
            role="tabpanel"
          >
            {escalated ? (
              <p className="rounded-2xl border border-warning/25 bg-warning/10 p-4 text-sm text-warning">
                已升级状态下不展示闸门。
              </p>
            ) : (
              <GateActionPanel
                gateId={gateId}
                gateInfo={gateInfo}
                onAction={async () => {
                  await Promise.all([gateQuery.mutate(), dvQuery.mutate()]);
                }}
              />
            )}
          </div>
        ) : null}

        {tab === "activity" ? (
          <div
            aria-labelledby="devwork-tab-activity"
            id="devwork-tabpanel-activity"
            role="tabpanel"
          >
            {workspaceEventsQuery.error ? (
              <p className="text-xs text-danger">
                {extractError(workspaceEventsQuery.error, "DevWork activity failed")}
              </p>
            ) : activityEvents.length === 0 ? (
              <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
                No DevWork activity yet.
              </p>
            ) : (
              <div className="max-h-[26rem] overflow-y-auto pr-1" data-testid="devwork-activity-feed">
                <div className="space-y-3">
                  {activityEvents.map((event) => (
                    <ActivityRow event={event} key={event.event_id} />
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : null}
      </SectionPanel>
    </div>
  );
}
