import { useMemo, useState, type FormEvent, type KeyboardEvent } from "react";
import { FileText, RotateCw, Trash2, X } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import useSWR from "swr";
import { ApiError } from "../api/client";
import {
  cancelDesignWork,
  deleteDesignWork,
  getDesignWork,
  getDesignWorkRetrySource,
  rerunDesignWork,
  retryDesignWork,
} from "../api/designWorks";
import { getDesignDocContent } from "../api/designDocs";
import { listReviews } from "../api/reviews";
import { listWorkspaceEvents } from "../api/workspaceEvents";
import { AppDialog } from "../components/AppDialog";
import { DesignWorkStateProgress } from "../components/DesignWorkStateProgress";
import { LoopSegmentRing } from "../components/LoopSegmentRing";
import { MarkdownPanel } from "../components/MarkdownPanel";
import { RepoRefsEditor, type RepoRefsEditorRow } from "../components/RepoRefsEditor";
import { ReviewRow } from "../components/ReviewHistory";
import { MetricCard, SectionPanel } from "../components/SectionPanel";
import { StatusBadge } from "../components/StatusBadge";
import {
  useWorkspaceActivePolling,
  useWorkspaceDetailPolling,
  useWorkspacePolling,
} from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type {
  AgentKind,
  DesignWork,
  DesignWorkRetrySource,
  RepoRef,
  RetryDesignWorkPayload,
  WorkspaceEvent,
} from "../types";

const DESIGN_WORK_EVENT_NAMES = [
  "design_work.started",
  "design_work.llm_completed",
  "design_work.round_completed",
  "design_work.mockup_recorded",
  "design_work.completed",
  "design_work.escalated",
] as const;
const DESIGN_WORK_EVENT_LIMIT = 20;
const SLUG_RE = /^[a-z0-9](?:[a-z0-9]|-(?!-)){0,61}[a-z0-9]$|^[a-z0-9]$/;
const FORM_FIELD_CLASSNAME =
  "w-full rounded-2xl border border-border-strong bg-panel px-4 py-3.5 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]";
const FORM_SELECT_CLASSNAME =
  "w-full rounded-2xl border border-border-strong bg-panel-strong px-4 py-3.5 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)] [&_option]:bg-panel-strong";
const DIALOG_FOOTER_CLASSNAME =
  "flex flex-col gap-3 border-t border-border/70 pt-4 sm:flex-row sm:items-center sm:justify-end";
const DESIGN_EXECUTION_STARTED_STATES = new Set<DesignWork["current_state"]>([
  "PROMPT_COMPOSE",
  "LLM_GENERATE",
  "MOCKUP",
  "POST_VALIDATE",
  "PERSIST",
  "COMPLETED",
]);
const EXECUTION_ESCALATION_REASONS = [
  "LLM call failed",
  "output file missing",
  "post-validate failed",
];

const DESIGN_DETAIL_TABS = ["overview", "delivery", "reviews", "activity"] as const;
type DesignDetailTab = (typeof DESIGN_DETAIL_TABS)[number];
const DESIGN_DETAIL_TAB_LABELS: Record<DesignDetailTab, string> = {
  overview: "总览",
  delivery: "最终交付",
  reviews: "审核历史",
  activity: "活动",
};

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

function toEditorRows(refs: RepoRef[]): RepoRefsEditorRow[] {
  return refs.map((ref) => ({
    repo_id: ref.repo_id,
    base_branch: ref.base_branch,
    mount_name: "",
    base_rev_lock: false,
    is_primary: false,
  }));
}

function escalatedAfterExecution(designWork: DesignWork) {
  if (designWork.current_state !== "ESCALATED") return false;
  const reason = designWork.escalation_reason ?? "";
  return EXECUTION_ESCALATION_REASONS.some((token) => reason.includes(token));
}

function executedLoopCount(designWork: DesignWork) {
  if (
    DESIGN_EXECUTION_STARTED_STATES.has(designWork.current_state) ||
    escalatedAfterExecution(designWork)
  ) {
    return designWork.loop + 1;
  }
  return designWork.loop;
}

function DesignWorkRetryForm({
  source,
  submitting,
  onCancel,
  onSubmit,
}: {
  source: DesignWorkRetrySource;
  submitting: boolean;
  onCancel: () => void;
  onSubmit: (payload: RetryDesignWorkPayload) => Promise<void>;
}) {
  const [title, setTitle] = useState(source.title);
  const [slug, setSlug] = useState(source.slug);
  const [userInput, setUserInput] = useState(source.user_input);
  const [needsFrontendMockup, setNeedsFrontendMockup] = useState(
    source.needs_frontend_mockup,
  );
  const [agent, setAgent] = useState<AgentKind | "">(source.agent ?? "");
  const [repoRefs, setRepoRefs] = useState<RepoRefsEditorRow[]>(
    toEditorRows(source.repo_refs),
  );
  const [attachmentPaths, setAttachmentPaths] = useState(source.attachment_paths ?? []);
  const [error, setError] = useState<string | null>(null);

  function removeAttachmentPath(path: string) {
    setAttachmentPaths((current) => current.filter((item) => item !== path));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedTitle = title.trim();
    const trimmedSlug = slug.trim();
    const trimmedInput = userInput.trim();
    if (!trimmedTitle) return setError("Title is required");
    if (!SLUG_RE.test(trimmedSlug)) return setError("Slug must be kebab-case");
    if (!trimmedInput) return setError("Requirement text is required");

    const touchedRows = repoRefs.filter(
      (row) => row.repo_id || row.base_branch,
    );
    for (const row of touchedRows) {
      if (!row.repo_id || !row.base_branch) {
        return setError("Every selected repo needs a repo and base branch");
      }
    }
    const refs = touchedRows.map((row) => ({
      repo_id: row.repo_id,
      base_branch: row.base_branch,
    }));

    setError(null);
    try {
      await onSubmit({
        title: trimmedTitle,
        slug: trimmedSlug,
        user_input: trimmedInput,
        needs_frontend_mockup: needsFrontendMockup,
        agent: agent || null,
        repo_refs: refs,
        attachment_paths: attachmentPaths,
      });
    } catch (err) {
      setError(extractError(err, "Retry failed"));
    }
  }

  return (
    <form className="space-y-5" onSubmit={(event) => void submit(event)}>
      <div className="grid gap-4 lg:grid-cols-2">
        <label className="space-y-1.5 text-sm text-muted">
          <span>Title</span>
          <input
            className={FORM_FIELD_CLASSNAME}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label className="space-y-1.5 text-sm text-muted">
          <span>Slug</span>
          <input
            className={FORM_FIELD_CLASSNAME}
            value={slug}
            onChange={(event) => setSlug(event.target.value)}
          />
        </label>
      </div>

      <label className="block space-y-1.5 text-sm text-muted">
        <span>Requirement</span>
        <textarea
          className={`${FORM_FIELD_CLASSNAME} min-h-[13rem] resize-y`}
          value={userInput}
          onChange={(event) => setUserInput(event.target.value)}
        />
      </label>

      <div className="grid gap-4 lg:grid-cols-2">
        <label className="flex items-center gap-2.5 text-sm text-muted">
          <input
            checked={needsFrontendMockup}
            onChange={(event) => setNeedsFrontendMockup(event.target.checked)}
            type="checkbox"
          />
          <span>Needs frontend mockup</span>
        </label>

        <label className="space-y-1.5 text-sm text-muted">
          <span>Execution Agent</span>
          <select
            className={FORM_SELECT_CLASSNAME}
            value={agent}
            onChange={(event) => setAgent(event.target.value as AgentKind | "")}
          >
            <option value="">Automatic</option>
            <option value="claude">Claude</option>
            <option value="codex">Codex</option>
          </select>
        </label>
      </div>

      <RepoRefsEditor
        minRows={0}
        mode="design"
        onChange={setRepoRefs}
        value={repoRefs}
      />

      {attachmentPaths.length > 0 ? (
        <div className="space-y-2 rounded-2xl border border-border bg-panel-strong/55 p-4">
          <p className="text-sm font-medium text-copy">Supplemental attachments</p>
          {attachmentPaths.map((path) => (
            <div
              className="flex items-center justify-between gap-3 rounded-xl border border-border bg-panel/70 px-3 py-2 text-xs text-muted"
              key={path}
            >
              <span className="flex min-w-0 items-center gap-2">
                <FileText aria-hidden="true" className="h-4 w-4 shrink-0 text-copy-soft" />
                <span className="truncate font-mono text-copy-soft">{path}</span>
              </span>
              <button
                aria-label={`Remove ${path}`}
                className="rounded-lg border border-border px-2 py-1 text-muted transition hover:border-danger/30 hover:text-danger"
                onClick={() => removeAttachmentPath(path)}
                type="button"
              >
                <X aria-hidden="true" className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      ) : null}

      {error ? <p className="text-xs text-danger">{error}</p> : null}

      <div className={DIALOG_FOOTER_CLASSNAME}>
        <button
          type="submit"
          disabled={submitting}
          className="inline-flex w-full items-center justify-center rounded-2xl bg-copy px-5 py-3 text-sm font-semibold text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] disabled:opacity-50 sm:w-auto"
        >
          {submitting ? "Retrying..." : "Create retry"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="inline-flex w-full items-center justify-center rounded-2xl border border-border-dark/60 bg-panel-strong/85 px-4 py-3 text-sm font-medium text-copy-soft transition hover:border-accent/50 hover:bg-panel hover:text-copy sm:w-auto"
        >
          Cancel
        </button>
      </div>
    </form>
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
  const [actionPending, setActionPending] = useState<
    "cancel" | "delete" | "rerun" | "retry" | null
  >(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [cancelConfirmOpen, setCancelConfirmOpen] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [retryOpen, setRetryOpen] = useState(false);
  const [detailTab, setDetailTab] = useState<DesignDetailTab>("overview");

  const dwQuery = useSWR(["design-work", dwId], () => getDesignWork(dwId), detailPolling);
  const retrySourceQuery = useSWR(
    retryOpen ? ["design-work-retry-source", dwId] : null,
    () => getDesignWorkRetrySource(dwId),
    { revalidateOnFocus: false },
  );
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
  const deleteEligible = Boolean(escalated || cancelled);
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

  const maxLoops = designWork.max_loops ?? Math.max(designWork.loop, 1);
  const executedLoops = executedLoopCount(designWork);
  const ringMaxLoops = Math.max(maxLoops, executedLoops, 1);
  const activityEvents = workspaceEventsQuery.data?.events ?? [];

  async function cancelWork() {
    setActionPending("cancel");
    setActionError(null);
    try {
      await cancelDesignWork(dwId);
      setCancelConfirmOpen(false);
      await dwQuery.mutate();
    } catch (err) {
      setActionError(extractError(err, "操作失败"));
    } finally {
      setActionPending(null);
    }
  }

  async function deleteWork() {
    setActionPending("delete");
    setActionError(null);
    try {
      await deleteDesignWork(dwId);
      navigate(`/workspaces/${wsId}`);
    } catch (err) {
      setActionError(extractError(err, "删除失败"));
    } finally {
      setActionPending(null);
    }
  }

  async function rerunWork() {
    setActionPending("rerun");
    setActionError(null);
    try {
      const updated = await rerunDesignWork(dwId);
      await dwQuery.mutate(updated, { revalidate: false });
      await workspaceEventsQuery.mutate();
    } catch (err) {
      setActionError(extractError(err, "重新执行失败"));
    } finally {
      setActionPending(null);
    }
  }

  async function submitRetry(payload: RetryDesignWorkPayload) {
    setActionPending("retry");
    setActionError(null);
    try {
      const created = await retryDesignWork(dwId, payload);
      setRetryOpen(false);
      navigate(`/workspaces/${wsId}/design-works/${created.id}`);
    } catch (err) {
      setActionError(extractError(err, "Retry failed"));
      throw err;
    } finally {
      setActionPending(null);
    }
  }

  function activateDetailTab(id: DesignDetailTab) {
    setDetailTab(id);
    window.setTimeout(() => {
      document.getElementById(`designwork-tab-${id}`)?.focus();
    }, 0);
  }

  function handleDetailTabKey(
    event: KeyboardEvent<HTMLButtonElement>,
    id: DesignDetailTab,
  ) {
    const currentIndex = DESIGN_DETAIL_TABS.indexOf(id);
    const lastIndex = DESIGN_DETAIL_TABS.length - 1;
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
    activateDetailTab(DESIGN_DETAIL_TABS[nextIndex]);
  }

  return (
    <div className="space-y-6">
      <AppDialog
        description="取消后当前 DesignWork 会停止推进，已产生的记录会保留。"
        onClose={() => setCancelConfirmOpen(false)}
        open={cancelConfirmOpen && !terminal}
        title="确认取消 DesignWork"
      >
        <div className="space-y-5">
          <p className="rounded-2xl border border-warning/25 bg-warning/10 p-4 text-sm leading-relaxed text-warning">
            请确认这是一次有意操作，避免误触中断后台流程。
          </p>
          <div className="flex flex-col gap-3 border-t border-border/70 pt-4 sm:flex-row sm:items-center sm:justify-end">
            <button
              className="inline-flex w-full items-center justify-center rounded-2xl border border-border-dark/60 bg-panel-strong/85 px-4 py-3 text-sm font-medium text-copy-soft transition hover:border-accent/50 hover:bg-panel hover:text-copy sm:w-auto"
              disabled={actionPending === "cancel"}
              onClick={() => setCancelConfirmOpen(false)}
              type="button"
            >
              返回
            </button>
            <button
              className="inline-flex w-full items-center justify-center rounded-2xl bg-danger px-5 py-3 text-sm font-semibold text-ink-invert disabled:opacity-50 sm:w-auto"
              disabled={actionPending === "cancel"}
              onClick={() => void cancelWork()}
              type="button"
            >
              {actionPending === "cancel" ? "取消中..." : "确认取消"}
            </button>
          </div>
        </div>
      </AppDialog>

      <AppDialog
        description="删除后会移除当前 DesignWork 记录，并清理它产生的草稿、提示词和未发布输出文件。"
        onClose={() => setDeleteConfirmOpen(false)}
        open={deleteConfirmOpen && deleteEligible}
        title="删除并清理 DesignWork"
      >
        <div className="space-y-5">
          <p className="rounded-2xl border border-danger/25 bg-danger/10 p-4 text-sm leading-relaxed text-danger">
            该操作不可恢复。仅取消或升级的 DesignWork 可以删除。
          </p>
          <div className="flex flex-col gap-3 border-t border-border/70 pt-4 sm:flex-row sm:items-center sm:justify-end">
            <button
              className="inline-flex w-full items-center justify-center rounded-2xl border border-border-dark/60 bg-panel-strong/85 px-4 py-3 text-sm font-medium text-copy-soft transition hover:border-accent/50 hover:bg-panel hover:text-copy sm:w-auto"
              disabled={actionPending === "delete"}
              onClick={() => setDeleteConfirmOpen(false)}
              type="button"
            >
              返回
            </button>
            <button
              className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-danger px-5 py-3 text-sm font-semibold text-ink-invert disabled:opacity-50 sm:w-auto"
              disabled={actionPending === "delete"}
              onClick={() => void deleteWork()}
              type="button"
            >
              <Trash2 aria-hidden="true" className="h-4 w-4" />
              {actionPending === "delete" ? "删除中..." : "确认删除"}
            </button>
          </div>
        </div>
      </AppDialog>

      <SectionPanel
        actions={
          <>
            {cancelled ? (
              <button
                className="inline-flex items-center gap-1.5 rounded-lg border border-accent/35 bg-accent/15 px-3 py-1.5 text-xs font-medium text-copy transition hover:bg-accent/20 disabled:opacity-50"
                disabled={actionPending !== null}
                onClick={() => void rerunWork()}
                type="button"
              >
                <RotateCw aria-hidden="true" className="h-3.5 w-3.5" />
                {actionPending === "rerun" ? "重新执行中..." : "重新执行"}
              </button>
            ) : null}
            {deleteEligible ? (
              <button
                className="inline-flex items-center gap-1.5 rounded-lg border border-danger/35 bg-danger/10 px-3 py-1.5 text-xs font-medium text-danger transition hover:bg-danger/15 disabled:opacity-50"
                disabled={actionPending !== null}
                onClick={() => setDeleteConfirmOpen(true)}
                type="button"
              >
                <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                {actionPending === "delete" ? "删除中..." : "删除"}
              </button>
            ) : null}
            <button
              className="rounded-lg bg-danger px-3 py-1.5 text-xs font-medium text-ink-invert disabled:opacity-50"
              disabled={actionPending !== null || terminal}
              onClick={() => setCancelConfirmOpen(true)}
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
        kicker="设计工作"
        title={designWork.title ?? designWork.sub_slug ?? designWork.id}
        titleAccessory={
          <LoopSegmentRing
            active={designWork.is_running && !terminal}
            completed={terminal ? executedLoops : Math.max(executedLoops - 1, 0)}
            label="DesignWork 实际执行轮次"
            max={ringMaxLoops}
            maxReached={designWork.loop >= maxLoops}
            value={executedLoops}
          />
        }
      >
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge status={designWork.current_state} />
          {designWork.is_running ? (
            <StatusBadge status="running" label="自动推进中" />
          ) : null}
          <span className="text-sm text-muted">模式 {designWork.mode}</span>
          <span className="text-sm text-muted">
            更新时间 {formatDateTime(designWork.updated_at)}
          </span>
          {designWork.version ? (
            <span className="font-mono text-xs text-muted">{designWork.version}</span>
          ) : null}
        </div>

        <div className="mt-4">
          <DesignWorkStateProgress
            active={designWork.is_running && !terminal}
            current={designWork.current_state}
          />
        </div>

        {escalated ? (
          <p className="mt-4 rounded-2xl border border-warning/25 bg-warning/10 p-4 text-sm text-warning">
            DesignWork 已升级，需人工介入。
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
              onClick={() => setRetryOpen(true)}
              type="button"
            >
              Retry as new DesignWork
            </button>
          </div>
        ) : null}

        {designWork.is_running ? (
          <p className="mt-4 rounded-2xl border border-success/25 bg-success/10 p-4 text-sm text-success">
            后台驱动正在推进此 DesignWork，页面会自动刷新最新状态。
          </p>
        ) : null}

        {actionError ? <p className="mt-3 text-xs text-danger">{actionError}</p> : null}
      </SectionPanel>

      <SectionPanel
        actions={
          <div
            className="flex max-w-full gap-2 overflow-x-auto pb-1"
            role="tablist"
            aria-label="DesignWork 详情切换"
          >
            {DESIGN_DETAIL_TABS.map((id) => {
              const selected = detailTab === id;
              return (
                <button
                  aria-controls={`designwork-tabpanel-${id}`}
                  aria-selected={selected}
                  className={[
                    "shrink-0 rounded-full border px-3 py-1.5 text-xs font-medium transition",
                    selected
                      ? "border-accent/30 bg-accent/15 text-copy"
                      : "border-border-strong bg-panel-strong/50 text-muted hover:border-copy/20 hover:text-copy",
                  ].join(" ")}
                  id={`designwork-tab-${id}`}
                  key={id}
                  onClick={() => activateDetailTab(id)}
                  onKeyDown={(event) => handleDetailTabKey(event, id)}
                  role="tab"
                  tabIndex={selected ? 0 : -1}
                  type="button"
                >
                  {DESIGN_DETAIL_TAB_LABELS[id]}
                </button>
              );
            })}
          </div>
        }
        kicker="详情面板"
        title={DESIGN_DETAIL_TAB_LABELS[detailTab]}
      >
        {detailTab === "overview" ? (
          <div
            aria-labelledby="designwork-tab-overview"
            className="space-y-5"
            id="designwork-tabpanel-overview"
            role="tabpanel"
          >
            <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
              <MetricCard label="状态" value={designWork.current_state} />
              <MetricCard label="循环轮次" value={String(designWork.loop)} />
              <MetricCard label="模式" value={designWork.mode} />
              <MetricCard label="DesignDoc" value={designWork.output_design_doc_id ?? "-"} />
              <MetricCard label="版本" value={designWork.version ?? "-"} />
              <MetricCard label="更新时间" value={formatDateTime(designWork.updated_at)} />
            </div>

            {designWork.missing_sections && designWork.missing_sections.length > 0 ? (
              <div className="rounded-2xl border border-warning/25 bg-warning/10 p-4">
                <p className="text-xs uppercase tracking-[0.24em] text-warning/90">
                  缺失章节
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {designWork.missing_sections.map((section) => (
                    <span
                      className="rounded-full border border-warning/25 bg-panel-deep/40 px-3 py-1 text-[11px] text-warning"
                      key={section}
                    >
                      {section}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {detailTab === "delivery" ? (
          <div
            aria-labelledby="designwork-tab-delivery"
            id="designwork-tabpanel-delivery"
            role="tabpanel"
          >
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
              <MarkdownPanel content={docState.content} reader />
            )}
          </div>
        ) : null}

        {detailTab === "reviews" ? (
          <div
            aria-labelledby="designwork-tab-reviews"
            id="designwork-tabpanel-reviews"
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

        {detailTab === "activity" ? (
          <div
            aria-labelledby="designwork-tab-activity"
            id="designwork-tabpanel-activity"
            role="tabpanel"
          >
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
                <div className="max-h-[calc(100vh-18rem)] overflow-y-auto pr-1" data-testid="designwork-activity-feed">
                  <div className="space-y-3">
                    {activityEvents.map((event) => (
                      <ActivityRow event={event} key={event.event_id} />
                    ))}
                  </div>
                </div>
                {(workspaceEventsQuery.data?.pagination.total ?? 0) > activityEvents.length ? (
                  <p className="mt-2 text-xs text-muted">
                    Showing latest {activityEvents.length} events
                  </p>
                ) : null}
              </>
            )}
          </div>
        ) : null}
      </SectionPanel>

      {retryOpen ? (
        <AppDialog
          description="Edit the copied source values before creating a new DesignWork."
          onClose={() => setRetryOpen(false)}
          open={retryOpen}
          size="wide"
          title="Retry DesignWork"
        >
          {retrySourceQuery.error ? (
            <div className="space-y-4">
              <p className="rounded-2xl border border-danger/25 bg-danger/10 p-4 text-sm text-danger">
                {extractError(retrySourceQuery.error, "Retry source failed to load")}
              </p>
              <button
                className="rounded-lg bg-copy px-3 py-1.5 text-xs font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)]"
                onClick={() => void retrySourceQuery.mutate()}
                type="button"
              >
                Reload
              </button>
            </div>
          ) : retrySourceQuery.data ? (
            <DesignWorkRetryForm
              key={`${dwId}:${retrySourceQuery.data.slug}`}
              onCancel={() => setRetryOpen(false)}
              onSubmit={submitRetry}
              source={retrySourceQuery.data}
              submitting={actionPending === "retry"}
            />
          ) : (
            <div className="h-48 animate-pulse rounded-2xl border border-border bg-panel-strong/70" />
          )}
        </AppDialog>
      ) : null}
    </div>
  );
}
