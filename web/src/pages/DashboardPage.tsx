import useSWR from "swr";
import { listAgentHosts } from "../api/agents";
import { listRuns } from "../api/runs";
import { ApprovalAction } from "../components/ApprovalAction";
import { RunCard } from "../components/RunCard";
import { StatCard } from "../components/StatCard";
import { StatusBadge } from "../components/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import type { AgentHost, GateName, RunRecord } from "../types";

const GATE_BY_STAGE: Record<string, GateName> = {
  REQ_REVIEW: "req",
  DESIGN_REVIEW: "design",
  DEV_REVIEW: "dev",
};

const RUNNING_LABEL = "\u8FD0\u884C\u4E2D";
const PENDING_APPROVAL_LABEL = "\u5F85\u5BA1\u6279";
const MERGING_LABEL = "\u5408\u5E76\u4E2D";
const FAILED_LABEL = "\u5931\u8D25";
const COMPLETED_LABEL = "\u5DF2\u5B8C\u6210";
const ACTIVE_RUNS_TITLE = "活跃运行";
const APPROVALS_TITLE = "\u5F85\u5BA1\u6279";
const HOSTS_TITLE = "Agent \u4E3B\u673A";

function SectionPanel({
  title,
  kicker,
  children,
}: {
  title: string;
  kicker: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-[28px] border border-white/6 bg-panel p-6 shadow-panel">
      <p className="text-[11px] uppercase tracking-[0.3em] text-muted/75">{kicker}</p>
      <h2 className="mt-2 text-lg font-semibold text-white">{title}</h2>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function EmptyState({ copy }: { copy: string }) {
  return <p className="rounded-2xl border border-dashed border-white/8 bg-white/3 px-4 py-6 text-sm text-muted">{copy}</p>;
}

function getPendingApprovals(runs: RunRecord[]) {
  return runs.filter((run) => run.status === "running" && run.current_stage in GATE_BY_STAGE);
}

function getRunningRuns(runs: RunRecord[]) {
  return runs.filter((run) => run.status === "running");
}

function getMergingRuns(runs: RunRecord[]) {
  return runs.filter((run) => run.status === "running" && (run.current_stage === "MERGE_QUEUED" || run.current_stage === "MERGING"));
}

function getCompletedRuns(runs: RunRecord[]) {
  return runs.filter((run) => run.status === "completed" || run.current_stage === "MERGED");
}

function getGate(stage: string): GateName {
  return GATE_BY_STAGE[stage] ?? "req";
}

export function DashboardPage() {
  const polling = usePolling(15_000);
  const overview = useSWR(["dashboard", "overview"], () => listRuns({ limit: 100 }), polling);
  const active = useSWR(["dashboard", "active"], () => listRuns({ limit: 20, status: "running" }), polling);
  const hosts = useSWR(["dashboard", "hosts"], listAgentHosts, polling);

  const allRuns = overview.data?.items ?? [];
  const activeRuns = active.data?.items ?? [];
  const hostItems = hosts.data ?? [];
  const runningRuns = getRunningRuns(allRuns);
  const pendingApprovals = getPendingApprovals(allRuns);
  const mergingRuns = getMergingRuns(allRuns);
  const failedRuns = allRuns.filter((run) => run.status === "failed");
  const completedRuns = getCompletedRuns(allRuns);
  const refreshAll = async () => {
    await Promise.all([overview.mutate(), active.mutate(), hosts.mutate()]);
  };

  const hasError = overview.error || active.error || hosts.error;
  if (hasError) {
    return (
      <section className="rounded-[28px] border border-danger/15 bg-danger/8 p-6 shadow-panel">
        <h2 className="text-lg font-semibold text-white">仪表盘数据加载失败</h2>
        <p className="mt-2 text-sm text-muted">重试查询以恢复概览页面。</p>
        <button className="mt-4 rounded-full bg-white px-4 py-2 text-sm font-medium text-black" onClick={() => void refreshAll()} type="button">
          重试
        </button>
      </section>
    );
  }

  if (!overview.data || !active.data || !hosts.data) {
    return (
      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-5">
          {Array.from({ length: 5 }, (_, index) => (
            <div key={index} className="h-32 animate-pulse rounded-[24px] border border-white/6 bg-panel" />
          ))}
        </div>
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="h-[360px] animate-pulse rounded-[28px] border border-white/6 bg-panel" />
          <div className="space-y-4">
            <div className="h-[180px] animate-pulse rounded-[28px] border border-white/6 bg-panel" />
            <div className="h-[180px] animate-pulse rounded-[28px] border border-white/6 bg-panel" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-5">
        <StatCard title={RUNNING_LABEL} value={runningRuns.length.toString().padStart(2, "0")} />
        <StatCard title={PENDING_APPROVAL_LABEL} value={pendingApprovals.length.toString().padStart(2, "0")} />
        <StatCard title={MERGING_LABEL} value={mergingRuns.length.toString().padStart(2, "0")} />
        <StatCard title={FAILED_LABEL} value={failedRuns.length.toString().padStart(2, "0")} />
        <StatCard title={COMPLETED_LABEL} value={completedRuns.length.toString().padStart(2, "0")} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <SectionPanel kicker="队列快照" title={ACTIVE_RUNS_TITLE}>
          {activeRuns.length === 0 ? (
            <EmptyState copy="当前没有活跃的运行记录。" />
          ) : (
            <div className="space-y-3">
              {activeRuns.map((run) => (
                <RunCard
                  failedAtStage={run.failed_at_stage}
                  key={run.id}
                  stage={run.current_stage}
                  status={run.status}
                  summary={run.description || "暂无摘要。"}
                  ticket={run.ticket}
                />
              ))}
            </div>
          )}
        </SectionPanel>

        <div className="space-y-4">
          <SectionPanel kicker="待办队列" title={APPROVALS_TITLE}>
            {pendingApprovals.length === 0 ? (
              <EmptyState copy="当前没有待审批的审批门控。" />
            ) : (
              <div className="space-y-3">
                {pendingApprovals.map((run) => (
                  <article className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4" key={run.id}>
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-mono text-sm text-white">{run.ticket}</p>
                        <p className="mt-1 text-sm text-muted">{run.description || "审批门控等待决策中。"}</p>
                      </div>
                      <StatusBadge label={run.current_stage} status="review" />
                    </div>
                    <div className="mt-4">
                      <ApprovalAction
                        by="dashboard"
                        gate={getGate(run.current_stage)}
                        onComplete={refreshAll}
                        reason={`${run.ticket} rejected from dashboard`}
                        runId={run.id}
                      />
                    </div>
                  </article>
                ))}
              </div>
            )}
          </SectionPanel>

          <SectionPanel kicker="资源池状态" title={HOSTS_TITLE}>
            {hostItems.length === 0 ? (
              <EmptyState copy="尚未注册任何主机。" />
            ) : (
              <div className="space-y-3">
                {hostItems.map((host) => (
                  <HostSummaryCard host={host} key={host.id} />
                ))}
              </div>
            )}
          </SectionPanel>
        </div>
      </div>
    </div>
  );
}

function HostSummaryCard({ host }: { host: AgentHost }) {
  return (
    <article className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-white">{host.host}</p>
          <p className="mt-1 text-xs text-muted">
            {host.agent_type} · {host.current_load}/{host.max_concurrent}
          </p>
          {host.labels.length > 0 ? <p className="mt-2 text-xs text-muted">{host.labels.join(" · ")}</p> : null}
        </div>
        <StatusBadge status={host.status} />
      </div>
    </article>
  );
}
