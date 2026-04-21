import { useEffect, useMemo, useState, type ReactNode } from "react";
import useSWR from "swr";
import { ApiError } from "../api/client";
import {
  getRunConflicts,
  listMergeQueue,
  mergeRun,
  resolveRunConflict,
  skipMergeRun,
} from "../api/repos";
import { getRun } from "../api/runs";
import { StatusBadge } from "../components/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import type { MergeQueueItem, RunRecord } from "../types";

type EnrichedQueueItem = MergeQueueItem & {
  run: RunRecord | null;
};

function SectionPanel({
  title,
  kicker,
  children,
}: {
  title: string;
  kicker: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-[28px] border border-border bg-panel p-6 shadow-panel">
      <p className="text-[11px] uppercase tracking-[0.3em] text-muted-soft">{kicker}</p>
      <h2 className="mt-2 text-lg font-semibold text-copy">{title}</h2>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function EmptyState({ copy }: { copy: string }) {
  return (
    <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
      {copy}
    </p>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 3 }, (_, index) => (
        <div
          key={index}
          className="h-32 animate-pulse rounded-[24px] border border-border bg-panel-strong/70"
        />
      ))}
    </div>
  );
}

function formatTimestamp(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
  }).format(date);
}

async function enrichQueue(items: MergeQueueItem[]) {
  const runs = await Promise.all(
    items.map(async (item) => {
      try {
        return await getRun(item.run_id);
      } catch {
        return null;
      }
    }),
  );

  return items.map((item, index) => ({ ...item, run: runs[index] }));
}

function formatActionError(error: unknown) {
  if (
    error instanceof ApiError &&
    error.status === 409 &&
    typeof error.data === "object" &&
    error.data !== null &&
    "current_stage" in error.data
  ) {
    return `${error.message} (current stage: ${String((error.data as { current_stage?: unknown }).current_stage)})`;
  }

  return error instanceof Error ? error.message : "Queue action failed";
}

export function MergeQueuePage() {
  const polling = usePolling(15_000);
  const queueQuery = useSWR(["merge-queue"], listMergeQueue, polling);
  const enrichedQuery = useSWR(
    queueQuery.data
      ? ["merge-queue-enriched", queueQuery.data.map((item) => item.run_id).join(",")]
      : null,
    () => enrichQueue(queueQuery.data ?? []),
    { keepPreviousData: true, revalidateOnFocus: false },
  );
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [mergePriority, setMergePriority] = useState("0");
  const [rowPending, setRowPending] = useState<Record<string, "merge" | "skip" | "resolve" | null>>(
    {},
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const queue = enrichedQuery.data ?? [];
  const selected = useMemo(
    () => queue.find((item) => item.run_id === selectedRunId) ?? queue[0] ?? null,
    [queue, selectedRunId],
  );
  const conflictsQuery = useSWR(
    selected?.status === "conflict" ? ["run-conflicts", selected.run_id] : null,
    () => getRunConflicts(selected!.run_id),
    { revalidateOnFocus: false },
  );

  useEffect(() => {
    if (!queue.length) {
      setSelectedRunId(null);
      setMergePriority("0");
      return;
    }

    if (!selectedRunId || !queue.some((item) => item.run_id === selectedRunId)) {
      setSelectedRunId(queue[0].run_id);
      setMergePriority(String(queue[0].priority));
    }
  }, [queue, selectedRunId]);

  useEffect(() => {
    if (selected) {
      setMergePriority(String(selected.priority));
    }
  }, [selected?.run_id]);

  const conflictFiles = useMemo(() => {
    if (!selected || selected.status !== "conflict") {
      return [];
    }

    const live = conflictsQuery.data?.conflicts ?? [];
    return live.length > 0 ? live : selected.conflict_files;
  }, [conflictsQuery.data?.conflicts, selected]);

  async function refreshAll() {
    await queueQuery.mutate();
    await enrichedQuery.mutate();
  }

  async function handleMerge(runId: string, defaultPriority: number) {
    setRowPending((current) => ({ ...current, [runId]: "merge" }));
    setActionError(null);
    setActionMessage(null);
    try {
      const resolvedPriority =
        selected?.run_id === runId ? Number(mergePriority) || defaultPriority : defaultPriority;
      await mergeRun(runId, resolvedPriority);
      await refreshAll();
    } catch (error) {
      setActionError(formatActionError(error));
    } finally {
      setRowPending((current) => ({ ...current, [runId]: null }));
    }
  }

  async function handleSkip(runId: string) {
    setRowPending((current) => ({ ...current, [runId]: "skip" }));
    setActionError(null);
    setActionMessage(null);
    try {
      await skipMergeRun(runId);
      await refreshAll();
    } catch (error) {
      setActionError(formatActionError(error));
    } finally {
      setRowPending((current) => ({ ...current, [runId]: null }));
    }
  }

  async function handleResolve(runId: string) {
    setRowPending((current) => ({ ...current, [runId]: "resolve" }));
    setActionError(null);
    setActionMessage(null);
    try {
      await resolveRunConflict(runId, "dashboard");
      setActionMessage(`Requeued ${runId}`);
      await refreshAll();
    } catch (error) {
      setActionError(formatActionError(error));
    } finally {
      setRowPending((current) => ({ ...current, [runId]: null }));
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <SectionPanel kicker="队列清单" title="合并队列">
        {queueQuery.error || enrichedQuery.error ? (
          <div className="rounded-[24px] border border-danger/15 bg-danger/8 p-5">
            <h3 className="text-base font-semibold text-copy">合并队列加载失败</h3>
            <p className="mt-2 text-sm text-muted">
              重试查询以恢复合并状态和运行上下文。
            </p>
            <button
              className="mt-4 rounded-full bg-copy px-4 py-2 text-sm font-medium text-ink-invert"
              onClick={() => void refreshAll()}
              type="button"
            >
              重试
            </button>
          </div>
        ) : !queueQuery.data || !enrichedQuery.data ? (
          <LoadingSkeleton />
        ) : queue.length === 0 ? (
          <EmptyState copy="合并队列中暂无等待项。" />
        ) : (
          <div className="space-y-3">
            {queue.map((item) => {
              const pendingState = rowPending[item.run_id];
              const selectedState = selected?.run_id === item.run_id;
              const label = item.run?.ticket ?? item.run_id;

              return (
                <article
                  className={`rounded-[24px] border bg-panel-strong/80 p-4 transition ${
                    selectedState
                      ? "border-accent/30 shadow-[0_0_0_1px_rgba(168,85,247,0.22)]"
                      : "border-border"
                  }`}
                  key={item.id}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="font-mono text-sm text-copy">{label}</p>
                      <p className="mt-1 text-sm text-muted">{item.branch}</p>
                      <p className="mt-2 text-xs text-muted">
                        priority {item.priority} · {item.run?.current_stage ?? "run unavailable"}
                      </p>
                    </div>
                    <StatusBadge status={item.status} />
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      className="rounded-full border border-border-strong bg-panel-strong/50 px-3 py-2 text-xs font-medium text-copy transition hover:border-copy/20 hover:bg-panel-strong/70"
                      onClick={() => setSelectedRunId(item.run_id)}
                      type="button"
                    >
                      {`查看 ${item.run_id}`}
                    </button>
                    <button
                      className="rounded-full border border-border-strong bg-panel-strong/50 px-3 py-2 text-xs font-medium text-copy transition hover:border-copy/20 hover:bg-panel-strong/70 disabled:cursor-not-allowed disabled:opacity-60"
                      disabled={pendingState !== undefined && pendingState !== null}
                      onClick={() => void handleMerge(item.run_id, item.priority)}
                      type="button"
                    >
                      {pendingState === "merge" ? "排队中..." : `合并 ${item.run_id}`}
                    </button>
                    <button
                      className="rounded-full bg-danger px-3 py-2 text-xs font-medium text-copy disabled:cursor-not-allowed disabled:opacity-60"
                      disabled={pendingState !== undefined && pendingState !== null}
                      onClick={() => void handleSkip(item.run_id)}
                      type="button"
                    >
                      {pendingState === "skip" ? "跳过中..." : `跳过 ${item.run_id}`}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </SectionPanel>

      <SectionPanel kicker="选中项" title="队列详情">
        {selected ? (
          <div className="space-y-4">
            <div className="rounded-[24px] border border-border bg-panel-strong/80 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="font-mono text-sm text-copy">
                    {selected.run?.ticket ?? selected.run_id}
                  </p>
                  <p className="mt-1 text-sm text-muted">{selected.branch}</p>
                </div>
                <StatusBadge status={selected.status} />
              </div>

              <div className="mt-4 grid gap-3">
                <DetailLine label="运行 ID" value={selected.run_id} />
                <DetailLine
                  label="仓库"
                  value={selected.run?.repo_path ?? "Run details unavailable"}
                />
                <DetailLine
                  label="阶段"
                  value={selected.run?.current_stage ?? "Run details unavailable"}
                />
                <DetailLine label="更新时间" value={formatTimestamp(selected.updated_at)} />
              </div>
            </div>

            <label className="block space-y-2 text-sm text-muted">
              <span>合并优先级</span>
              <input
                className="w-full rounded-2xl border border-border bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-accent/40"
                min={0}
                onChange={(event) => setMergePriority(event.target.value)}
                type="number"
                value={mergePriority}
              />
            </label>

            {selected.status === "conflict" ? (
              <div className="rounded-[24px] border border-warning/20 bg-warning/10 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium text-copy">检测到冲突</p>
                    <p className="mt-2 text-sm text-muted">
                      查看冲突文件，在外部解决后重新入队。
                    </p>
                  </div>
                  <span className="rounded-full border border-warning/20 bg-warning/10 px-3 py-1 text-xs text-warning">
                    {`${conflictFiles.length} 个文件`}
                  </span>
                </div>

                {conflictsQuery.error ? (
                  <div className="mt-4 rounded-2xl border border-danger/15 bg-danger/8 p-4">
                    <p className="text-sm text-copy">
                      冲突详情刷新失败，显示队列快照。
                    </p>
                    <button
                      className="mt-3 rounded-full border border-border-strong bg-panel-strong/50 px-3 py-2 text-xs font-medium text-copy transition hover:border-copy/20 hover:bg-panel-strong/70"
                      onClick={() => void conflictsQuery.mutate()}
                      type="button"
                    >
                      重试冲突详情
                    </button>
                  </div>
                ) : null}

                <div className="mt-4 rounded-[24px] border border-border bg-panel-strong/80 p-4">
                  <p className="text-sm font-medium text-copy">冲突文件</p>
                  {selected.status === "conflict" && conflictsQuery.isLoading && !conflictsQuery.data ? (
                    <p className="mt-3 text-sm text-muted">加载冲突详情...</p>
                  ) : conflictFiles.length === 0 ? (
                    <p className="mt-3 text-sm text-muted">未报告冲突文件</p>
                  ) : (
                    <ul className="mt-3 space-y-2 text-sm text-muted">
                      {conflictFiles.map((file) => (
                        <li key={file}>{file}</li>
                      ))}
                    </ul>
                  )}
                </div>

                <button
                  className="mt-4 rounded-full bg-copy px-4 py-3 text-sm font-medium text-ink-invert transition hover:bg-copy/90 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={rowPending[selected.run_id] !== undefined && rowPending[selected.run_id] !== null}
                  onClick={() => void handleResolve(selected.run_id)}
                  type="button"
                >
                  {rowPending[selected.run_id] === "resolve"
                    ? "重新入队中..."
                    : `解决冲突并重新入队 ${selected.run_id}`}
                </button>
              </div>
            ) : (
              <div className="rounded-[24px] border border-border bg-panel-strong/80 p-4">
                <p className="text-sm font-medium text-copy">冲突文件</p>
                {selected.conflict_files.length === 0 ? (
                  <p className="mt-3 text-sm text-muted">未报告冲突文件</p>
                ) : (
                  <ul className="mt-3 space-y-2 text-sm text-muted">
                    {selected.conflict_files.map((file) => (
                      <li key={file}>{file}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {actionMessage ? <p className="text-sm text-success">{actionMessage}</p> : null}
            {actionError ? <p className="text-sm text-danger">{actionError}</p> : null}
          </div>
        ) : (
          <EmptyState copy="选择一个队列项以查看合并上下文。" />
        )}
      </SectionPanel>
    </div>
  );
}

function DetailLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-border bg-panel px-3 py-3">
      <p className="text-[11px] uppercase tracking-[0.24em] text-muted-soft">{label}</p>
      <p className="mt-2 break-all text-sm text-copy">{value}</p>
    </div>
  );
}
