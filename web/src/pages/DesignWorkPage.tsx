import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import useSWR from "swr";
import { ApiError } from "../api/client";
import { cancelDesignWork, getDesignWork, retryDesignWork, tickDesignWork } from "../api/designWorks";
import { getDesignDocContent } from "../api/designDocs";
import { listReviews } from "../api/reviews";
import { listWorkspaceEvents } from "../api/workspaceEvents";
import { DesignWorkStateProgress } from "../components/DesignWorkStateProgress";
import { MarkdownPanel } from "../components/MarkdownPanel";
import { MetricCard, SectionPanel } from "../components/SectionPanel";
import { StatusBadge } from "../components/StatusBadge";
import {
  useWorkspaceActivePolling,
  useWorkspaceDetailPolling,
  useWorkspacePolling,
} from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type { DesignWork, Review, WorkspaceEvent } from "../types";

const DESIGN_WORK_EVENT_NAMES = [
  "design_work.started",
  "design_work.llm_completed",
  "design_work.round_completed",
  "design_work.mockup_recorded",
  "design_work.completed",
  "design_work.escalated",
] as const;
const DESIGN_WORK_EVENT_LIMIT = 20;

function formatDateTime(value: string | null | undefined) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
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

function ReviewRow({ review }: { review: Review }) {
  return (
    <article className="rounded-2xl border border-border bg-panel-strong/80 p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-copy">
          第 {review.round} 轮 · 评分 {review.score ?? "-"}
        </p>
        {review.problem_category ? <StatusBadge status={review.problem_category} /> : null}
      </div>
      {review.reviewer ? <p className="mt-2 text-xs text-muted">审核者 {review.reviewer}</p> : null}
      {review.issues && review.issues.length > 0 ? (
        <details className="mt-3 text-xs text-muted">
          <summary className="cursor-pointer">问题 ({review.issues.length})</summary>
          <pre className="mt-2 overflow-x-auto rounded-2xl bg-panel-deep p-3 text-[11px] text-copy whitespace-pre-wrap">
            {JSON.stringify(review.issues, null, 2)}
          </pre>
        </details>
      ) : null}
      {review.findings && review.findings.length > 0 ? (
        <details className="mt-2 text-xs text-muted">
          <summary className="cursor-pointer">发现项 ({review.findings.length})</summary>
          <pre className="mt-2 overflow-x-auto rounded-2xl bg-panel-deep p-3 text-[11px] text-copy whitespace-pre-wrap">
            {JSON.stringify(review.findings, null, 2)}
          </pre>
        </details>
      ) : null}
    </article>
  );
}

type DesignDocContentState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; content: string }
  | { kind: "missing" }
  | { kind: "error"; message: string };

export function DesignWorkPage() {
  const { wsId, dwId } = useParams();
  if (!wsId || !dwId) {
    return (
      <section className="rounded-[32px] border border-danger/15 bg-danger/8 p-6 shadow-panel">
        <h2 className="font-serif text-xl font-medium text-copy">路径参数缺失</h2>
      </section>
    );
  }
  return <DesignWorkContent wsId={wsId} dwId={dwId} />;
}

function DesignWorkContent({ wsId, dwId }: { wsId: string; dwId: string }) {
  const navigate = useNavigate();
  const polling = useWorkspacePolling();
  const detailPolling = useWorkspaceDetailPolling<DesignWork>((latest) => Boolean(latest?.is_running));
  const [actionPending, setActionPending] = useState<"tick" | "cancel" | "retry" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const dwQuery = useSWR(["design-work", dwId], () => getDesignWork(dwId), detailPolling);
  const reviewsQuery = useSWR(
    ["reviews", "design", dwId],
    () => listReviews({ design_work_id: dwId }),
    polling,
  );

  const designWork = dwQuery.data;
  const outputDocId = designWork?.output_design_doc_id ?? null;
  const escalated = designWork?.current_state === "ESCALATED";
  const cancelled = designWork?.current_state === "CANCELLED";
  const terminal =
    escalated || cancelled || designWork?.current_state === "COMPLETED";
  const activityPolling = useWorkspaceActivePolling(Boolean(designWork?.is_running && !terminal));
  const workspaceEventsQuery = useSWR(
    ["workspace-events", "design-work", wsId, dwId],
    () =>
      listWorkspaceEvents(wsId, {
        limit: DESIGN_WORK_EVENT_LIMIT,
        event_name: [...DESIGN_WORK_EVENT_NAMES],
        correlation_id: dwId,
      }),
    activityPolling,
  );

  const docContentQuery = useSWR(
    outputDocId ? ["design-doc-content", outputDocId] : null,
    () => getDesignDocContent(outputDocId as string),
    { shouldRetryOnError: false, revalidateOnFocus: false },
  );

  const docState = useMemo<DesignDocContentState>(() => {
    if (!outputDocId) return { kind: "idle" };
    if (docContentQuery.error) {
      const err = docContentQuery.error;
      if (err instanceof ApiError) {
        if (err.status === 410) return { kind: "missing" };
        return { kind: "error", message: err.message };
      }
      return { kind: "error", message: "设计文档加载失败" };
    }
    if (!docContentQuery.data) return { kind: "loading" };
    return { kind: "ok", content: docContentQuery.data };
  }, [outputDocId, docContentQuery.error, docContentQuery.data]);

  const reviewsDesc = useMemo(
    () => (reviewsQuery.data ? [...reviewsQuery.data].reverse() : []),
    [reviewsQuery.data],
  );

  if (dwQuery.error) {
    return (
      <section className="rounded-[32px] border border-danger/15 bg-danger/8 p-6 shadow-panel">
        <h2 className="font-serif text-xl font-medium text-copy">DesignWork 加载失败</h2>
        <p className="mt-2 text-sm text-muted">{extractError(dwQuery.error, "")}</p>
        <button
          className="mt-4 rounded-xl bg-copy px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
          onClick={() => void dwQuery.mutate()}
          type="button"
        >
          重试
        </button>
      </section>
    );
  }

  if (!designWork) {
    return <div className="h-[240px] animate-pulse rounded-[32px] border border-border bg-panel" />;
  }

  const activityEvents = workspaceEventsQuery.data?.events ?? [];

  async function runAction(action: "tick" | "cancel" | "retry") {
    setActionPending(action);
    setActionError(null);
    try {
      if (action === "tick") {
        await tickDesignWork(dwId);
        await dwQuery.mutate();
      } else if (action === "cancel") {
        await cancelDesignWork(dwId);
        await dwQuery.mutate();
      } else {
        const created = await retryDesignWork(dwId);
        navigate(`/workspaces/${wsId}/design-works/${created.id}`);
      }
    } catch (err) {
      setActionError(extractError(err, "操作失败"));
    } finally {
      setActionPending(null);
    }
  }

  return (
    <div className="space-y-6">
      <SectionPanel
        actions={
          <Link className="text-xs text-muted hover:text-copy" to={`/workspaces/${wsId}`}>
            ← 返回 Workspace
          </Link>
        }
        kicker="设计工作"
        title={designWork.title ?? designWork.sub_slug ?? designWork.id}
      >
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge status={designWork.current_state} />
          {designWork.is_running ? (
            <StatusBadge status="running" label="自动推进中" />
          ) : null}
          <span className="text-sm text-muted">循环 {designWork.loop}</span>
          <span className="text-sm text-muted">模式 {designWork.mode}</span>
          <span className="text-sm text-muted">
            更新时间 {formatDateTime(designWork.updated_at)}
          </span>
          {designWork.version ? (
            <span className="font-mono text-xs text-muted">{designWork.version}</span>
          ) : null}
        </div>

        <div className="mt-5">
          <DesignWorkStateProgress current={designWork.current_state} />
        </div>

        {escalated ? (
          <p className="mt-5 rounded-2xl border border-warning/25 bg-warning/10 p-4 text-sm text-warning">
            DesignWork 已升级，需人工介入；tick 已禁用。
          </p>
        ) : null}

        {escalated ? (
          <div className="mt-3 rounded-2xl border border-warning/25 bg-panel-strong/60 p-4 text-sm text-warning">
            <p className="text-xs text-warning/90">
              Reason: {designWork.escalation_reason ?? "No escalation reason recorded"}
            </p>
            <button
              className="mt-3 rounded-lg bg-copy px-3 py-1.5 text-xs font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] disabled:opacity-50"
              disabled={actionPending !== null}
              onClick={() => void runAction("retry")}
              type="button"
            >
              {actionPending === "retry" ? "Retrying..." : "Retry as new DesignWork"}
            </button>
          </div>
        ) : null}

        {designWork.is_running ? (
          <p className="mt-5 rounded-2xl border border-success/25 bg-success/10 p-4 text-sm text-success">
            后台驱动正在推进此 DesignWork，手动推进会暂时锁定，页面会自动刷新最新状态。
          </p>
        ) : null}

        {designWork.missing_sections && designWork.missing_sections.length > 0 ? (
          <div className="mt-5 space-y-2">
            <p className="text-xs uppercase tracking-[0.24em] text-muted-soft">缺失章节</p>
            <div className="flex flex-wrap gap-2">
              {designWork.missing_sections.map((section) => (
                <span
                  className="rounded-full border border-warning/25 bg-warning/10 px-3 py-1 text-[11px] text-warning"
                  key={section}
                >
                  {section}
                </span>
              ))}
            </div>
          </div>
        ) : null}

        <div className="mt-5 flex flex-wrap gap-2">
          <button
            className="rounded-lg bg-copy px-3 py-1.5 text-xs font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] disabled:opacity-50"
            disabled={actionPending !== null || designWork.is_running || terminal}
            onClick={() => void runAction("tick")}
            type="button"
          >
            {actionPending === "tick" ? "推进中..." : "推进"}
          </button>
          <button
            className="rounded-lg bg-danger px-3 py-1.5 text-xs font-medium text-ink-invert disabled:opacity-50"
            disabled={actionPending !== null || terminal}
            onClick={() => void runAction("cancel")}
            type="button"
          >
            {actionPending === "cancel" ? "取消中..." : "取消"}
          </button>
        </div>
        {actionError ? <p className="mt-3 text-xs text-danger">{actionError}</p> : null}
      </SectionPanel>

      <SectionPanel kicker="摘要" title="状态与产物">
        <div className="grid gap-3 md:grid-cols-4">
          <MetricCard label="状态" value={designWork.current_state} />
          <MetricCard label="循环轮次" value={String(designWork.loop)} />
          <MetricCard label="DesignDoc" value={designWork.output_design_doc_id ?? "-"} />
          <MetricCard label="更新时间" value={formatDateTime(designWork.updated_at)} />
        </div>
      </SectionPanel>

      <SectionPanel kicker="最近活动" title="DesignWork 活动">
        {workspaceEventsQuery.error ? (
          <p className="text-xs text-danger">
            {extractError(workspaceEventsQuery.error, "活动加载失败")}
          </p>
        ) : activityEvents.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
            暂无 DesignWork 活动。
          </p>
        ) : (
          <>
            <div className="max-h-[26rem] overflow-y-auto pr-1" data-testid="designwork-activity-feed">
              <div className="space-y-3">
                {activityEvents.map((event) => (
                  <ActivityRow event={event} key={event.event_id} />
                ))}
              </div>
            </div>
            {(workspaceEventsQuery.data?.pagination.total ?? 0) > activityEvents.length ? (
              <p className="mt-2 text-xs text-muted">Showing latest {activityEvents.length} events</p>
            ) : null}
          </>
        )}
      </SectionPanel>

      <SectionPanel kicker="设计文档" title="最终交付">
        {docState.kind === "idle" ? (
          <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
            尚未产出 DesignDoc。
          </p>
        ) : docState.kind === "loading" ? (
          <div className="h-40 animate-pulse rounded-2xl border border-border bg-panel-strong/70" />
        ) : docState.kind === "missing" ? (
          <p className="rounded-2xl border border-warning/25 bg-warning/10 px-4 py-4 text-sm text-warning">
            源文件已缺失，请运行 <code className="font-mono">POST /workspaces/sync</code> 后刷新。
          </p>
        ) : docState.kind === "error" ? (
          <p className="rounded-2xl border border-danger/25 bg-danger/10 px-4 py-4 text-sm text-danger">
            {docState.message}
          </p>
        ) : (
          <MarkdownPanel content={docState.content} />
        )}
      </SectionPanel>

      <SectionPanel kicker="审核历史" title="审核记录">
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
      </SectionPanel>
    </div>
  );
}
