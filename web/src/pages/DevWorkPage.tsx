import { useMemo, useState, type KeyboardEvent } from "react";
import { RotateCw, Trash2 } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import useSWR from "swr";
import { ApiError } from "../api/client";
import { getDevWorkContextContent } from "../api/devContexts";
import {
  cancelDevWork,
  continueDevWork,
  deleteDevWork,
  getDevWork,
  pushDevWorkBranches,
  rerunDevWork,
  resumeDevWorkStep,
} from "../api/devWorks";
import { getIterationNoteContent, listIterationNotes } from "../api/devIterationNotes";
import { getGate } from "../api/gates";
import { listReviews } from "../api/reviews";
import { listWorkspaceEvents } from "../api/workspaceEvents";
import { AppDialog } from "../components/AppDialog";
import { DevWorkStepProgress } from "../components/DevWorkStepProgress";
import { GateActionPanel } from "../components/GateActionPanel";
import { LoopSegmentRing } from "../components/LoopSegmentRing";
import { MarkdownPanel } from "../components/MarkdownPanel";
import { PlanChecklistPanel } from "../components/PlanChecklistPanel";
import { RepoPushStatusGrid } from "../components/RepoPushStatusGrid";
import { ReviewHistory } from "../components/ReviewHistory";
import { MetricCard, SectionPanel } from "../components/SectionPanel";
import { ScoreBadge } from "../components/ScoreBadge";
import { StatusBadge } from "../components/StatusBadge";
import {
  useWorkspaceActivePolling,
  useWorkspaceDetailPolling,
  useWorkspacePolling,
} from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type { DevIterationNote, DevWork, WorkspaceEvent } from "../types";

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
  "dev_work.continued",
  "dev_work.cancelled",
  "dev_work.completed",
  "dev_work.gate.exit_waiting",
  "dev_work.merge_conflict",
] as const;

const DEV_WORK_ID_RE = /^[a-zA-Z0-9_-]+$/;
const ROUND_STARTED_STEPS = new Set<DevWork["current_step"]>([
  "STEP2_ITERATION",
  "STEP3_CONTEXT",
  "STEP4_DEVELOP",
  "STEP5_REVIEW",
  "COMPLETED",
]);

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

function highestNoteRound(notes: DevIterationNote[] | undefined) {
  return notes?.reduce((highest, note) => Math.max(highest, note.round), 0) ?? 0;
}

function estimatedExecutedRound(devWork: DevWork) {
  if (devWork.progress?.round) return devWork.progress.round;
  if (ROUND_STARTED_STEPS.has(devWork.current_step)) return devWork.iteration_rounds + 1;
  return devWork.iteration_rounds;
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

type RoundArtifactView = "iteration" | "context";

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
  const navigate = useNavigate();
  const polling = useWorkspacePolling();
  const detailPolling = useWorkspaceDetailPolling<DevWork>((latest) => Boolean(latest?.is_running));
  const [tab, setTab] = useState<TabId>("overview");
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [roundArtifactView, setRoundArtifactView] = useState<RoundArtifactView>("iteration");
  const [actionPending, setActionPending] = useState<
    "cancel" | "continue" | "delete" | "push" | "rerun" | "resume" | null
  >(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [cancelConfirmOpen, setCancelConfirmOpen] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [continueRounds, setContinueRounds] = useState("3");
  const [continueThreshold, setContinueThreshold] = useState("");

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
  const selectedPlanVerification = useMemo(() => {
    if (!selectedNote) return null;
    const review =
      reviewsDesc.find((item) => item.dev_iteration_note_id === selectedNote.id) ??
      reviewsDesc.find((item) => item.round === selectedNote.round);
    return review?.findings ?? null;
  }, [reviewsDesc, selectedNote]);

  const noteContentQuery = useSWR(
    effectiveNoteId ? ["iteration-note-content", effectiveNoteId] : null,
    () => getIterationNoteContent(effectiveNoteId!),
    { shouldRetryOnError: false, revalidateOnFocus: false },
  );

  const contextContentQuery = useSWR(
    selectedNote && roundArtifactView === "context"
      ? ["dev-work-context-content", dvId, selectedNote.round]
      : null,
    () => getDevWorkContextContent(dvId, selectedNote!.round),
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

  const contextContent = useMemo<NoteContentState>(() => {
    if (!selectedNote) return { kind: "idle" };
    if (contextContentQuery.error) {
      const err = contextContentQuery.error;
      if (err instanceof ApiError) {
        if (err.status === 404) return { kind: "missing" };
        if (err.status === 410) {
          return { kind: "error", message: "Step3 执行地图源文件已缺失。" };
        }
        return { kind: "error", message: err.message };
      }
      return { kind: "error", message: "Step3 执行地图加载失败" };
    }
    if (!contextContentQuery.data) return { kind: "loading" };
    return { kind: "ok", content: contextContentQuery.data };
  }, [selectedNote, contextContentQuery.error, contextContentQuery.data]);

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
  const deleteEligible = escalated || cancelled;
  const maxRounds = devWork.max_rounds ?? Math.max(devWork.iteration_rounds, 1);
  const executedRounds = Math.max(
    highestNoteRound(notesQuery.data),
    estimatedExecutedRound(devWork),
  );
  const ringMaxRounds = Math.max(maxRounds, executedRounds, 1);
  const activityEvents = workspaceEventsQuery.data?.events ?? [];
  const repoPushRows = devWork.repos ?? [];
  const hasRepoPushRows = repoPushRows.length > 0;
  const hasPendingPush = repoPushRows.some((repo) => repo.push_state === "pending");
  const hasFailedPush = repoPushRows.some((repo) => repo.push_state === "failed");
  const allPushed = hasRepoPushRows && repoPushRows.every((repo) => repo.push_state === "pushed");
  const showPushAction = devWork.current_step === "COMPLETED" && hasRepoPushRows;
  const needsPushAttention = showPushAction && !allPushed;
  const pushActionLabel = allPushed
    ? "已推送"
    : hasPendingPush
      ? "推送分支"
      : hasFailedPush
        ? "重试推送"
        : "推送分支";

  // Missing gate is an expected "no exit gate right now" state, not an error.
  const gateInfo =
    gateQuery.data ??
    (gateQuery.error instanceof ApiError && gateQuery.error.status === 404 ? null : undefined);

  async function cancelWork() {
    setActionPending("cancel");
    setActionError(null);
    try {
      await cancelDevWork(dvId);
      setCancelConfirmOpen(false);
      await dvQuery.mutate();
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
      await deleteDevWork(dvId);
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
      const updated = await rerunDevWork(dvId);
      await dvQuery.mutate(updated, { revalidate: false });
      await Promise.all([
        notesQuery.mutate(),
        reviewsQuery.mutate(),
        workspaceEventsQuery.mutate(),
      ]);
    } catch (err) {
      setActionError(extractError(err, "重新执行失败"));
    } finally {
      setActionPending(null);
    }
  }

  async function pushBranches() {
    setActionPending("push");
    setActionError(null);
    try {
      const updated = await pushDevWorkBranches(dvId);
      await dvQuery.mutate(updated, { revalidate: false });
    } catch (err) {
      setActionError(extractError(err, "推送分支失败"));
    } finally {
      setActionPending(null);
    }
  }

  async function continueWork() {
    const normalized = continueRounds.trim();
    const parsed = Number(normalized);
    if (!/^\d+$/.test(normalized) || !Number.isInteger(parsed) || parsed < 1 || parsed > 50) {
      setActionError("继续循环次数必须是 1-50 的整数。");
      return;
    }
    const normalizedThreshold = continueThreshold.trim();
    const parsedThreshold = Number(normalizedThreshold);
    if (
      normalizedThreshold &&
      (!/^\d+$/.test(normalizedThreshold) ||
        !Number.isInteger(parsedThreshold) ||
        parsedThreshold < 1 ||
        parsedThreshold > 100)
    ) {
      setActionError("准出阈值必须是 1-100 的整数。");
      return;
    }
    setActionPending("continue");
    setActionError(null);
    try {
      const updated = await continueDevWork(
        dvId,
        parsed,
        normalizedThreshold ? parsedThreshold : undefined,
      );
      await dvQuery.mutate(updated, { revalidate: false });
      await workspaceEventsQuery.mutate();
    } catch (err) {
      setActionError(extractError(err, "继续循环失败"));
    } finally {
      setActionPending(null);
    }
  }

  async function resumeStep() {
    setActionPending("resume");
    setActionError(null);
    try {
      const updated = await resumeDevWorkStep(dvId);
      await dvQuery.mutate(updated, { revalidate: false });
      await workspaceEventsQuery.mutate();
    } catch (err) {
      setActionError(extractError(err, "恢复 Step 失败"));
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
      <AppDialog
        description="取消后当前 DevWork 会停止推进，已产生的迭代记录和工作目录信息会保留。"
        onClose={() => setCancelConfirmOpen(false)}
        open={cancelConfirmOpen && !terminal}
        title="确认取消 DevWork"
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
        description="删除后会移除当前 DevWork 记录，并清理它产生的迭代文件、提示词、上下文、artifact 和工作目录。"
        onClose={() => setDeleteConfirmOpen(false)}
        open={deleteConfirmOpen && deleteEligible}
        title="删除并清理 DevWork"
      >
        <div className="space-y-5">
          <p className="rounded-2xl border border-danger/25 bg-danger/10 p-4 text-sm leading-relaxed text-danger">
            该操作不可恢复。仅取消或升级的 DevWork 可以删除。
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
        kicker="开发工作"
        title={devWork.id}
        titleAccessory={
          <LoopSegmentRing
            active={devWork.is_running && !terminal}
            completed={terminal ? executedRounds : Math.max(executedRounds - 1, 0)}
            label="DevWork 实际执行轮次"
            max={ringMaxRounds}
            maxReached={devWork.iteration_rounds >= maxRounds}
            value={executedRounds}
          />
        }
      >
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge status={devWork.current_step} />
          {devWork.is_running ? (
            <StatusBadge status="running" label="自动推进中" />
          ) : null}
          <span className="font-mono text-xs text-muted">文档：{devWork.design_doc_id}</span>
          <span className="text-sm text-muted">
            更新时间 {formatDateTime(devWork.updated_at)}
          </span>
          <ScoreBadge score={devWork.last_score} />
        </div>

        <div className="mt-4">
          <DevWorkStepProgress
            active={devWork.is_running && !terminal}
            current={devWork.current_step}
          />
        </div>

        {escalated ? (
          devWork.continue_available ? (
            <div className="mt-4 rounded-2xl border border-warning/25 bg-warning/10 p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <p className="text-sm text-warning">
                  DevWork 已因达到循环上限升级，可人工追加轮次后继续推进。
                </p>
                <div className="flex flex-wrap items-end gap-2">
                  <label className="flex flex-col gap-1 text-xs text-muted">
                    <span>继续循环次数</span>
                    <input
                      aria-label="继续循环次数"
                      className="w-28 rounded-lg border border-border bg-panel px-3 py-1.5 text-sm text-copy"
                      min={1}
                      max={50}
                      onChange={(event) => setContinueRounds(event.target.value)}
                      type="number"
                      value={continueRounds}
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-xs text-muted">
                    <span>准出阈值</span>
                    <input
                      aria-label="继续循环准出阈值"
                      className="w-28 rounded-lg border border-border bg-panel px-3 py-1.5 text-sm text-copy"
                      min={1}
                      max={100}
                      onChange={(event) => setContinueThreshold(event.target.value)}
                      placeholder="沿用"
                      type="number"
                      value={continueThreshold}
                    />
                  </label>
                  <button
                    className="rounded-lg bg-warning px-3 py-1.5 text-xs font-medium text-ink disabled:opacity-50"
                    disabled={actionPending !== null}
                    onClick={() => void continueWork()}
                    type="button"
                  >
                    {actionPending === "continue" ? "继续中..." : "继续循环"}
                  </button>
                </div>
              </div>
            </div>
          ) : devWork.resume_available ? (
            <div className="mt-4 rounded-2xl border border-warning/25 bg-warning/10 p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm text-warning">
                  DevWork 已升级，可从 {devWork.resume_step ?? "当前 Step"} 重新推进。
                </p>
                <button
                  className="rounded-lg bg-warning px-3 py-1.5 text-xs font-medium text-ink disabled:opacity-50"
                  disabled={actionPending !== null}
                  onClick={() => void resumeStep()}
                  type="button"
                >
                  {actionPending === "resume" ? "恢复中..." : "从当前 Step 重跑"}
                </button>
              </div>
            </div>
          ) : (
            <p className="mt-4 rounded-2xl border border-warning/25 bg-warning/10 p-4 text-sm text-warning">
              DevWork 已升级，需人工介入；闸门面板已隐藏。
            </p>
          )
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
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="text-sm font-semibold text-copy">仓库与推送状态</h3>
                {showPushAction ? (
                  <button
                    className={[
                      "rounded-lg border border-accent/35 bg-accent/15 px-3 py-1.5 text-xs font-medium text-copy transition hover:bg-accent/20 disabled:opacity-50",
                      needsPushAttention ? "devwork-push-attention" : "",
                    ].join(" ")}
                    disabled={actionPending !== null || allPushed}
                    onClick={() => void pushBranches()}
                    type="button"
                  >
                    <span>{actionPending === "push" ? "推送中..." : pushActionLabel}</span>
                  </button>
                ) : null}
              </div>
              <RepoPushStatusGrid repos={repoPushRows} />
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
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div className="inline-flex rounded-xl border border-border bg-panel-strong/70 p-1">
                  <button
                    aria-pressed={roundArtifactView === "iteration"}
                    className={[
                      "rounded-lg px-3 py-1.5 text-xs font-medium transition",
                      roundArtifactView === "iteration"
                        ? "bg-accent/18 text-copy"
                        : "text-muted hover:text-copy",
                    ].join(" ")}
                    onClick={() => setRoundArtifactView("iteration")}
                    type="button"
                  >
                    迭代设计
                  </button>
                  <button
                    aria-pressed={roundArtifactView === "context"}
                    className={[
                      "rounded-lg px-3 py-1.5 text-xs font-medium transition",
                      roundArtifactView === "context"
                        ? "bg-accent/18 text-copy"
                        : "text-muted hover:text-copy",
                    ].join(" ")}
                    onClick={() => setRoundArtifactView("context")}
                    type="button"
                  >
                    Step3 执行地图
                  </button>
                </div>
                {selectedNote ? (
                  <span className="font-mono text-[11px] text-muted">
                    round {selectedNote.round}
                  </span>
                ) : null}
              </div>

              {roundArtifactView === "iteration" ? (
                noteContent.kind === "idle" ? (
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
                  <div className="space-y-4">
                    <PlanChecklistPanel
                      content={noteContent.content}
                      planVerification={selectedPlanVerification}
                    />
                    <MarkdownPanel content={noteContent.content} reader />
                  </div>
                )
              ) : contextContent.kind === "idle" ? (
                <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
                  选择一个迭代轮次查看 Step3 执行地图。
                </p>
              ) : contextContent.kind === "loading" ? (
                <div className="h-40 animate-pulse rounded-2xl border border-border bg-panel-strong/70" />
              ) : contextContent.kind === "missing" ? (
                <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
                  本轮尚无 Step3 执行地图。
                </p>
              ) : contextContent.kind === "error" ? (
                <p className="rounded-2xl border border-danger/25 bg-danger/10 px-4 py-4 text-sm text-danger">
                  {contextContent.message}
                </p>
              ) : (
                <MarkdownPanel
                  content={contextContent.content}
                  emptyText="暂无 Step3 执行地图。"
                  reader
                />
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
              <ReviewHistory reviews={reviewsDesc} />
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
