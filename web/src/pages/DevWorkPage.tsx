import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import useSWR from "swr";
import { ApiError } from "../api/client";
import { cancelDevWork, getDevWork, tickDevWork } from "../api/devWorks";
import { getIterationNoteContent, listIterationNotes } from "../api/devIterationNotes";
import { getGate } from "../api/gates";
import { listReviews } from "../api/reviews";
import { DevWorkStepProgress } from "../components/DevWorkStepProgress";
import { GateActionPanel } from "../components/GateActionPanel";
import { MarkdownPanel } from "../components/MarkdownPanel";
import { RepoPushStatusGrid } from "../components/RepoPushStatusGrid";
import { MetricCard, SectionPanel } from "../components/SectionPanel";
import { ScoreBadge } from "../components/ScoreBadge";
import { StatusBadge } from "../components/StatusBadge";
import { useWorkspacePolling } from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type { DevIterationNote, Review } from "../types";

const TAB_IDS = ["notes", "reviews", "gate"] as const;
type TabId = (typeof TAB_IDS)[number];
const TAB_LABELS: Record<TabId, string> = {
  notes: "迭代设计文件",
  reviews: "审核历史",
  gate: "闸门",
};

// Path-segment shape for DevWork ids — the gate_id is composed from this and
// is then sent through encodeURIComponent in the API client. The validation is
// defence-in-depth so a malformed URL never produces a surprising gate key.
const DEV_WORK_ID_RE = /^[a-zA-Z0-9_-]+$/;

function ReviewRow({ review }: { review: Review }) {
  return (
    <article className="rounded-2xl border border-border bg-panel-strong/80 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
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
  const [tab, setTab] = useState<TabId>("notes");
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [actionPending, setActionPending] = useState<"tick" | "cancel" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const dvQuery = useSWR(["dev-work", dvId], () => getDevWork(dvId), polling);
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

  // Missing gate is an expected "no exit gate right now" state, not an error.
  const gateInfo =
    gateQuery.data ??
    (gateQuery.error instanceof ApiError && gateQuery.error.status === 404 ? null : undefined);

  async function runAction(action: "tick" | "cancel") {
    setActionPending(action);
    setActionError(null);
    try {
      if (action === "tick") {
        await tickDevWork(dvId);
      } else {
        await cancelDevWork(dvId);
      }
      await dvQuery.mutate();
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
        kicker="开发工作"
        title={devWork.id}
      >
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge status={devWork.current_step} />
          <span className="font-mono text-xs text-muted">文档：{devWork.design_doc_id}</span>
          <span className="text-sm text-muted">
            轮次 {devWork.iteration_rounds}
          </span>
          <ScoreBadge score={devWork.last_score} />
        </div>

        <div className="mt-5">
          <DevWorkStepProgress current={devWork.current_step} />
        </div>

        {escalated ? (
          <p className="mt-5 rounded-2xl border border-warning/25 bg-warning/10 p-4 text-sm text-warning">
            DevWork 已升级；tick 已禁用，闸门面板已隐藏。
          </p>
        ) : null}

        <div className="mt-5 flex flex-wrap gap-2">
          <button
            className="rounded-lg bg-copy px-3 py-1.5 text-xs font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] disabled:opacity-50"
            disabled={actionPending !== null || escalated || terminal}
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

      <SectionPanel kicker="摘要" title="指标">
        <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-5">
          <MetricCard label="最近分数" value={devWork.last_score?.toString() ?? "-"} />
          <MetricCard label="问题分类" value={devWork.last_problem_category ?? "-"} />
          <MetricCard
            label="一次通过"
            value={
              devWork.first_pass_success === null ? "-" : devWork.first_pass_success ? "是" : "否"
            }
          />
          <MetricCard label="工作分支" value={devWork.worktree_branch ?? "-"} />
          <MetricCard label="工作目录" value={devWork.worktree_path ?? "-"} />
        </div>
      </SectionPanel>

      <SectionPanel kicker="仓库" title="仓库与推送状态">
        <RepoPushStatusGrid repos={devWork.repos ?? []} />
      </SectionPanel>

      <SectionPanel
        actions={
          <div className="flex gap-2" role="tablist" aria-label="DevWork 详情切换">
            {TAB_IDS.map((id) => {
              const selected = tab === id;
              return (
                <button
                  aria-controls={`devwork-tabpanel-${id}`}
                  aria-selected={selected}
                  className={[
                    "rounded-full border px-3 py-1.5 text-xs font-medium transition",
                    selected
                      ? "border-accent/30 bg-accent/15 text-copy"
                      : "border-border-strong bg-panel-strong/50 text-muted hover:border-copy/20 hover:text-copy",
                  ].join(" ")}
                  id={`devwork-tab-${id}`}
                  key={id}
                  onClick={() => setTab(id)}
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
        {tab === "notes" ? (
          <div
            aria-labelledby="devwork-tab-notes"
            className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]"
            id="devwork-tabpanel-notes"
            role="tabpanel"
          >
            <div>
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
                <MarkdownPanel content={noteContent.content} />
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
      </SectionPanel>
    </div>
  );
}
