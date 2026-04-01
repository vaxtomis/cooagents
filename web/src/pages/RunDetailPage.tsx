import { useEffect, useRef, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import useSWR from "swr";
import { getRunTrace } from "../api/diagnostics";
import {
  cancelRun,
  getArtifactContent,
  getArtifactDiff,
  getJobOutput,
  getRun,
  getRunBrief,
  getRunEventsStreamUrl,
  listArtifacts,
  listJobs,
} from "../api/runs";
import { ApprovalAction } from "../components/ApprovalAction";
import { StageProgress } from "../components/StageProgress";
import { StatusBadge } from "../components/StatusBadge";
import { useSSE, type SseConnectionState } from "../hooks/useSSE";
import { DASHBOARD_STAGE_FLOW, type ArtifactRecord, type GateName, type JobRecord, type RunTraceResponse, type ApprovalRecord, type StepRecord } from "../types";

const REVIEW_GATE_BY_STAGE: Record<string, GateName> = {
  REQ_REVIEW: "req",
  DESIGN_REVIEW: "design",
  DEV_REVIEW: "dev",
};

const GATE_DEFINITIONS: Array<{ gate: GateName; label: string; reviewStage: string }> = [
  { gate: "req", label: "REQ", reviewStage: "REQ_REVIEW" },
  { gate: "design", label: "DESIGN", reviewStage: "DESIGN_REVIEW" },
  { gate: "dev", label: "DEV", reviewStage: "DEV_REVIEW" },
];

const DETAIL_TABS = [
  { id: "artifacts", label: "产物" },
  { id: "jobs", label: "Agent\u8F93\u51FA" },
  { id: "trace", label: "\u4E8B\u4EF6\u8FFD\u8E2A" },
  { id: "history", label: "阶段历史" },
] as const;

type DetailTabId = (typeof DETAIL_TABS)[number]["id"];

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
    <section className="rounded-[28px] border border-white/6 bg-panel p-6 shadow-panel">
      <p className="text-[11px] uppercase tracking-[0.3em] text-muted/75">{kicker}</p>
      <h2 className="mt-2 text-lg font-semibold text-white">{title}</h2>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-white/6 bg-panel-strong/80 p-4">
      <p className="text-xs uppercase tracking-[0.24em] text-muted/75">{label}</p>
      <p className="mt-3 break-all font-mono text-sm text-white">{value}</p>
    </div>
  );
}

function EmptyState({ copy }: { copy: string }) {
  return <p className="rounded-2xl border border-dashed border-white/8 bg-white/3 px-4 py-6 text-sm text-muted">{copy}</p>;
}

function LoadingSkeleton() {
  return (
    <div className="space-y-4">
      <div className="h-[180px] animate-pulse rounded-[28px] border border-white/6 bg-panel" />
      <div className="h-[220px] animate-pulse rounded-[28px] border border-white/6 bg-panel" />
      <div className="h-[220px] animate-pulse rounded-[28px] border border-white/6 bg-panel" />
    </div>
  );
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return "-";
  }

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

function formatDuration(seconds: number | null | undefined) {
  if (seconds == null || Number.isNaN(seconds)) {
    return "-";
  }

  if (seconds < 60) {
    return `${seconds}s`;
  }

  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder === 0 ? `${minutes}m` : `${minutes}m ${remainder}s`;
}

function resolveConnectionLabel(state: SseConnectionState) {
  switch (state) {
    case "live":
      return { label: "在线", tone: "success" as const };
    case "reconnecting":
      return { label: "重连中", tone: "warning" as const };
    case "offline":
      return { label: "离线", tone: "danger" as const };
    default:
      return { label: "连接中", tone: "muted" as const };
  }
}

function getStageOrder(stage: string) {
  const index = DASHBOARD_STAGE_FLOW.indexOf(stage as (typeof DASHBOARD_STAGE_FLOW)[number]);
  return index === -1 ? Number.MAX_SAFE_INTEGER : index;
}

function getLatestApproval(approvals: ApprovalRecord[] | undefined, gate: GateName) {
  return [...(approvals ?? [])]
    .filter((approval) => approval.gate === gate)
    .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at))[0];
}

function resolveApprovalState({
  approvals,
  currentStage,
  gate,
  reviewStage,
}: {
  approvals: ApprovalRecord[] | undefined;
  currentStage: string;
  gate: GateName;
  reviewStage: string;
}) {
  const DECISION_LABELS: Record<string, string> = { approved: "已通过", rejected: "已驳回" };
  const record = getLatestApproval(approvals, gate);
  if (record) {
    return {
      byline: `${record.by} · ${formatTimestamp(record.created_at)}`,
      comment: record.comment,
      label: DECISION_LABELS[record.decision] ?? record.decision,
      status: record.decision,
    };
  }

  if (currentStage === reviewStage) {
    return {
      byline: `${gate.toUpperCase()} 审批门控已激活。`,
      comment: null,
      label: "等待决策",
      status: "review",
    };
  }

  if (getStageOrder(currentStage) < getStageOrder(reviewStage)) {
    return {
      byline: "尚未到达此门控。",
      comment: null,
      label: "未到达",
      status: "muted",
    };
  }

  return {
    byline: "门控已通过，但无审批记录。",
    comment: null,
    label: "无记录",
    status: "muted",
  };
}

function ApprovalHistory({ approvals, currentStage }: { approvals: ApprovalRecord[] | undefined; currentStage: string }) {
  return (
    <div className="space-y-3">
      {GATE_DEFINITIONS.map((definition) => {
        const state = resolveApprovalState({
          approvals,
          currentStage,
          gate: definition.gate,
          reviewStage: definition.reviewStage,
        });

        return (
          <article className="overflow-hidden rounded-2xl border border-white/6 bg-panel-strong/80 p-4" key={definition.gate}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-medium text-white">{definition.label}</p>
                <p className="mt-2 truncate text-xs text-muted">{state.byline}</p>
              </div>
              <StatusBadge label={state.label} status={state.status} />
            </div>
            {state.comment ? <p className="mt-3 text-sm text-muted">{state.comment}</p> : null}
          </article>
        );
      })}
    </div>
  );
}

function ArtifactsPanel({
  artifacts,
  artifactState,
  onInspect,
}: {
  artifacts: ArtifactRecord[];
  artifactState: {
    artifactId: number | null;
    content: string;
    diff: string;
    error: string | null;
    loading: boolean;
    path: string;
  };
  onInspect: (artifact: ArtifactRecord) => void | Promise<void>;
}) {
  return (
    <div>
      {artifacts.length === 0 ? (
        <EmptyState copy="当前运行暂无产物。" />
      ) : (
        <div className="space-y-3">
          {artifacts.map((artifact) => (
            <article className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4" key={artifact.id}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="font-mono text-sm text-white">{artifact.path}</p>
                  <p className="mt-1 text-xs text-muted">
                    {artifact.kind} · v{artifact.version} · {artifact.status}
                  </p>
                </div>
                <button
                  className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8"
                  onClick={() => void onInspect(artifact)}
                  type="button"
                >
                  {`查看 ${artifact.path}`}
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      {artifactState.artifactId !== null ? (
        <div className="mt-4 rounded-[24px] border border-white/6 bg-black/20 p-4">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-medium text-white">{artifactState.path}</p>
            {artifactState.loading ? <span className="text-xs text-muted">加载中...</span> : null}
          </div>
          {artifactState.error ? <p className="mt-3 text-sm text-danger">{artifactState.error}</p> : null}
          {artifactState.content ? <pre className="mt-3 overflow-x-auto rounded-2xl bg-black/30 p-4 text-xs text-white whitespace-pre-wrap">{artifactState.content}</pre> : null}
          {artifactState.diff ? <pre className="mt-3 overflow-x-auto rounded-2xl bg-black/30 p-4 text-xs text-white whitespace-pre-wrap">{artifactState.diff}</pre> : null}
        </div>
      ) : null}
    </div>
  );
}

function JobsPanel({
  jobs,
  jobOutputs,
  onLoadOutput,
}: {
  jobs: JobRecord[];
  jobOutputs: Record<string, { error?: string; loading?: boolean; output?: string }>;
  onLoadOutput: (job: JobRecord) => void | Promise<void>;
}) {
  if (jobs.length === 0) {
    return <EmptyState copy="当前运行暂无任务记录。" />;
  }

  return (
    <div className="space-y-3">
      {jobs.map((job) => {
        const outputState = jobOutputs[job.id] ?? {};
        return (
          <article className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4" key={job.id}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="font-mono text-sm text-white">{job.id}</p>
                <p className="mt-1 text-xs text-muted">
                  {job.agent_type} · {job.stage} · {job.status}
                </p>
              </div>
              <button
                className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8"
                onClick={() => void onLoadOutput(job)}
                type="button"
              >
                {`加载输出 ${job.id}`}
              </button>
            </div>
            {outputState.loading ? <p className="mt-3 text-sm text-muted">加载输出中...</p> : null}
            {outputState.error ? <p className="mt-3 text-sm text-danger">{outputState.error}</p> : null}
            {outputState.output ? <pre className="mt-3 overflow-x-auto rounded-2xl bg-black/30 p-4 text-xs text-white whitespace-pre-wrap">{outputState.output}</pre> : null}
          </article>
        );
      })}
    </div>
  );
}

function TraceEvents({ trace }: { trace: RunTraceResponse }) {
  if (trace.events.length === 0) {
    return <EmptyState copy="当前运行暂无追踪事件。" />;
  }

  return (
    <div className="space-y-3">
      {trace.events.map((event, index) => (
        <article className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4" key={`${event.event_type}-${event.created_at}-${index}`}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-white">{event.event_type}</p>
              <p className="mt-1 text-xs text-muted">
                {event.source ?? "engine"} · {event.level ?? "info"}
              </p>
            </div>
            <span className="text-xs text-muted">{formatTimestamp(event.created_at)}</span>
          </div>
          {event.payload ? <pre className="mt-3 overflow-x-auto rounded-2xl bg-black/30 p-4 text-xs text-white whitespace-pre-wrap">{JSON.stringify(event.payload, null, 2)}</pre> : null}
        </article>
      ))}
    </div>
  );
}

function StageHistoryPanel({ steps }: { steps: StepRecord[] | undefined }) {
  const orderedSteps = [...(steps ?? [])].sort((left, right) => Date.parse(left.created_at) - Date.parse(right.created_at));

  if (orderedSteps.length === 0) {
    return <EmptyState copy="当前运行暂无阶段变更记录。" />;
  }

  return (
    <div className="space-y-3">
      {orderedSteps.map((step, index) => (
        <article className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4" key={`${step.from_stage}-${step.to_stage}-${step.created_at}-${index}`}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-white">{`${step.from_stage} -> ${step.to_stage}`}</p>
              <p className="mt-2 text-xs text-muted">触发者 {step.triggered_by ?? "system"}</p>
            </div>
            <span className="text-xs text-muted">{formatTimestamp(step.created_at)}</span>
          </div>
        </article>
      ))}
    </div>
  );
}

export function RunDetailPage() {
  const { runId } = useParams();
  const refreshTimer = useRef<number | null>(null);
  const [activeTab, setActiveTab] = useState<DetailTabId>("artifacts");
  const [cancelPending, setCancelPending] = useState(false);
  const [cancelMessage, setCancelMessage] = useState<string | null>(null);
  const [artifactState, setArtifactState] = useState<{
    artifactId: number | null;
    content: string;
    diff: string;
    error: string | null;
    loading: boolean;
    path: string;
  }>({ artifactId: null, content: "", diff: "", error: null, loading: false, path: "" });
  const [jobOutputs, setJobOutputs] = useState<Record<string, { error?: string; loading?: boolean; output?: string }>>({});

  const run = useSWR(runId ? ["run", runId] : null, () => getRun(runId!), { revalidateOnFocus: false });
  const brief = useSWR(runId ? ["run-brief", runId] : null, () => getRunBrief(runId!), { revalidateOnFocus: false });
  const jobs = useSWR(runId ? ["run-jobs", runId] : null, () => listJobs(runId!), { revalidateOnFocus: false });
  const artifacts = useSWR(runId ? ["run-artifacts", runId] : null, () => listArtifacts(runId!), { revalidateOnFocus: false });
  const trace = useSWR(runId ? ["run-trace", runId] : null, () => getRunTrace(runId!, { limit: 50 }), { revalidateOnFocus: false });

  async function refreshAll() {
    await Promise.all([run.mutate(), brief.mutate(), jobs.mutate(), artifacts.mutate(), trace.mutate()]);
  }

  function scheduleRefresh() {
    if (typeof window === "undefined") {
      void refreshAll();
      return;
    }

    if (refreshTimer.current !== null) {
      window.clearTimeout(refreshTimer.current);
    }
    refreshTimer.current = window.setTimeout(() => {
      refreshTimer.current = null;
      void refreshAll();
    }, 120);
  }

  useEffect(() => {
    return () => {
      if (typeof window !== "undefined" && refreshTimer.current !== null) {
        window.clearTimeout(refreshTimer.current);
      }
    };
  }, []);

  const sse = useSSE(runId ? getRunEventsStreamUrl(runId) : null, {
    enabled: Boolean(runId),
    onEvent: () => {
      scheduleRefresh();
    },
  });

  if (!runId) {
    return (
      <section className="rounded-[28px] border border-danger/15 bg-danger/8 p-6 shadow-panel">
        <h2 className="text-lg font-semibold text-white">缺少运行 ID</h2>
        <p className="mt-2 text-sm text-muted">请从概览或运行列表进入以查看具体运行。</p>
      </section>
    );
  }

  const error = run.error ?? brief.error ?? jobs.error ?? artifacts.error ?? trace.error;
  if (error) {
    return (
      <section className="rounded-[28px] border border-danger/15 bg-danger/8 p-6 shadow-panel">
        <h2 className="text-lg font-semibold text-white">运行详情加载失败</h2>
        <p className="mt-2 text-sm text-muted">重试查询以恢复产物、任务和追踪数据。</p>
        <button className="mt-4 rounded-full bg-white px-4 py-2 text-sm font-medium text-black" onClick={() => void refreshAll()} type="button">
          重试
        </button>
      </section>
    );
  }

  if (!run.data || !brief.data || !jobs.data || !artifacts.data || !trace.data) {
    return <LoadingSkeleton />;
  }

  const resolvedRunId = runId;
  const runData = run.data;
  const briefData = brief.data;
  const connection = resolveConnectionLabel(sse.state);
  const activeGate = REVIEW_GATE_BY_STAGE[runData.current_stage];

  async function handleInspectArtifact(artifact: ArtifactRecord) {
    setArtifactState({ artifactId: artifact.id, content: "", diff: "", error: null, loading: true, path: artifact.path });
    try {
      const [content, diff] = await Promise.all([
        getArtifactContent(resolvedRunId, artifact.id),
        getArtifactDiff(resolvedRunId, artifact.id),
      ]);
      setArtifactState({
        artifactId: artifact.id,
        content: content.content,
        diff: diff.diff,
        error: null,
        loading: false,
        path: artifact.path,
      });
    } catch (loadError) {
      setArtifactState({
        artifactId: artifact.id,
        content: "",
        diff: "",
        error: loadError instanceof Error ? loadError.message : "产物详情加载失败",
        loading: false,
        path: artifact.path,
      });
    }
  }

  async function handleLoadOutput(job: JobRecord) {
    setJobOutputs((current) => ({ ...current, [job.id]: { loading: true } }));
    try {
      const response = await getJobOutput(resolvedRunId, job.id);
      setJobOutputs((current) => ({ ...current, [job.id]: { loading: false, output: response.output } }));
    } catch (loadError) {
      setJobOutputs((current) => ({
        ...current,
        [job.id]: {
          error: loadError instanceof Error ? loadError.message : "任务输出加载失败",
          loading: false,
        },
      }));
    }
  }

  async function handleCancelRun() {
    setCancelPending(true);
    setCancelMessage(null);
    try {
      await cancelRun(resolvedRunId, false);
      setCancelMessage("已请求取消。等待运行流确认终止状态。");
    } catch (cancelError) {
      setCancelMessage(cancelError instanceof Error ? cancelError.message : "取消请求失败");
    } finally {
      setCancelPending(false);
    }
  }

  const activeTabMeta = DETAIL_TABS.find((tab) => tab.id === activeTab) ?? DETAIL_TABS[0];

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-4">
        <SectionPanel kicker="基本信息" title="运行摘要">
          <div className="grid gap-3 md:grid-cols-4">
            <MetricCard label="工单" value={runData.ticket} />
            <MetricCard label="当前阶段" value={runData.current_stage} />
            <MetricCard label="状态" value={runData.status} />
            <MetricCard label="仓库" value={runData.repo_path} />
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-3">
            <StatusBadge status={runData.status} />
            <StatusBadge label={runData.current_stage} status={activeGate ? "review" : runData.status} />
            <span className="text-sm text-muted">更新于 {formatTimestamp(runData.updated_at)}</span>
          </div>

          <p className="mt-5 text-sm leading-6 text-muted">{briefData.current.summary || runData.description || "暂无运行摘要。"}</p>

          <div className="mt-5">
            <StageProgress failedAtStage={runData.failed_at_stage} stage={runData.current_stage} />
          </div>
        </SectionPanel>

        <SectionPanel kicker="当前步骤" title="执行上下文">
          <div className="grid gap-3 md:grid-cols-3">
            <MetricCard label="操作类型" value={briefData.current.action_type} />
            <MetricCard label="已用时" value={formatDuration(briefData.current.elapsed_sec)} />
            <MetricCard label="产物数" value={String(briefData.progress.artifacts_count)} />
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <div className="rounded-2xl border border-white/6 bg-panel-strong/80 p-4">
              <p className="text-xs uppercase tracking-[0.24em] text-muted/75">当前描述</p>
              <p className="mt-3 text-sm text-muted">{briefData.current.description}</p>
            </div>
            <div className="rounded-2xl border border-white/6 bg-panel-strong/80 p-4">
              <p className="text-xs uppercase tracking-[0.24em] text-muted/75">上一阶段</p>
              <p className="mt-3 text-sm text-white">{briefData.previous?.stage ?? "-"}</p>
              <p className="mt-2 text-xs text-muted">{briefData.previous?.result ?? "暂无阶段转换记录。"}</p>
            </div>
          </div>
        </SectionPanel>

        <SectionPanel kicker="详情面板" title={activeTabMeta.label}>
          <div aria-label="Run detail tabs" className="flex flex-wrap gap-2" role="tablist">
            {DETAIL_TABS.map((tab) => {
              const selected = tab.id === activeTab;
              return (
                <button
                  aria-controls={`run-detail-panel-${tab.id}`}
                  aria-selected={selected}
                  className={[
                    "rounded-full border px-4 py-2 text-sm font-medium transition",
                    selected ? "border-accent/30 bg-accent/15 text-white" : "border-white/10 bg-white/4 text-muted hover:border-white/20 hover:bg-white/8 hover:text-white",
                  ].join(" ")}
                  id={`run-detail-tab-${tab.id}`}
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  role="tab"
                  type="button"
                >
                  {tab.label}
                </button>
              );
            })}
          </div>

          <div aria-labelledby={`run-detail-tab-${activeTab}`} className="mt-5" id={`run-detail-panel-${activeTab}`} role="tabpanel">
            {activeTab === "artifacts" ? <ArtifactsPanel artifactState={artifactState} artifacts={artifacts.data} onInspect={handleInspectArtifact} /> : null}
            {activeTab === "jobs" ? <JobsPanel jobOutputs={jobOutputs} jobs={jobs.data} onLoadOutput={handleLoadOutput} /> : null}
            {activeTab === "trace" ? <TraceEvents trace={trace.data} /> : null}
            {activeTab === "history" ? <StageHistoryPanel steps={runData.steps} /> : null}
          </div>
        </SectionPanel>
      </div>

      <div className="space-y-4">
        <SectionPanel kicker="SSE 状态" title="实时连接">
          <div className="rounded-2xl border border-white/6 bg-panel-strong/80 p-4">
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm text-white">运行事件流</span>
              <StatusBadge label={connection.label} status={connection.tone} />
            </div>
            <p className="mt-3 text-sm text-muted">相关运行事件会触发节流刷新，使产物、任务和追踪数据保持最新。</p>
          </div>
        </SectionPanel>

        <SectionPanel kicker="审批状态" title="审批历史">
          <ApprovalHistory approvals={runData.approvals} currentStage={runData.current_stage} />
        </SectionPanel>

        <SectionPanel kicker="操作" title="操作控制">
          <div className="space-y-4">
            {activeGate ? (
              <div className="rounded-2xl border border-white/6 bg-panel-strong/80 p-4">
                <p className="text-sm text-white">审批门控</p>
                <p className="mt-2 text-sm text-muted">{runData.current_stage} 等待审批决策中。</p>
                <div className="mt-4">
                  <ApprovalAction by="detail" gate={activeGate} onComplete={refreshAll} runId={resolvedRunId} />
                </div>
              </div>
            ) : null}

            <div className="rounded-2xl border border-white/6 bg-panel-strong/80 p-4">
              <p className="text-sm text-white">终止运行</p>
              <p className="mt-2 text-sm text-muted">需要停止后续工作时，取消当前运行并等待终止事件。</p>
              <button
                className="mt-4 rounded-full bg-danger px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
                disabled={cancelPending || runData.status !== "running"}
                onClick={() => void handleCancelRun()}
                type="button"
              >
                {cancelPending ? "取消中..." : "取消运行"}
              </button>
              {cancelMessage ? <p className="mt-3 text-sm text-muted">{cancelMessage}</p> : null}
            </div>
          </div>
        </SectionPanel>
      </div>
    </div>
  );
}
