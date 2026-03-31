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

function calculateRecentRuns(runs: RunRecord[]) {
  const threshold = Date.now() - 24 * 60 * 60 * 1000;
  return runs.filter((run) => Date.parse(run.created_at) >= threshold).length;
}

function getPendingApprovals(runs: RunRecord[]) {
  return runs.filter((run) => run.status === "running" && run.current_stage in GATE_BY_STAGE);
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
  const pendingApprovals = getPendingApprovals(allRuns);
  const failedRuns = allRuns.filter((run) => run.status === "failed");
  const refreshAll = async () => {
    await Promise.all([overview.mutate(), active.mutate(), hosts.mutate()]);
  };

  const hasError = overview.error || active.error || hosts.error;
  if (hasError) {
    return (
      <section className="rounded-[28px] border border-danger/15 bg-danger/8 p-6 shadow-panel">
        <h2 className="text-lg font-semibold text-white">Dashboard data failed to load</h2>
        <p className="mt-2 text-sm text-muted">Retry the dashboard queries to restore the overview surface.</p>
        <button className="mt-4 rounded-full bg-white px-4 py-2 text-sm font-medium text-black" onClick={() => void refreshAll()} type="button">
          Retry
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
        <StatCard title="运行中" value={activeRuns.length.toString().padStart(2, "0")} />
        <StatCard title="待审批" value={pendingApprovals.length.toString().padStart(2, "0")} />
        <StatCard title="失败中" value={failedRuns.length.toString().padStart(2, "0")} />
        <StatCard title="主机" value={hostItems.length.toString().padStart(2, "0")} />
        <StatCard title="最近 24h" value={calculateRecentRuns(allRuns).toString().padStart(2, "0")} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <SectionPanel kicker="Queue Snapshot" title="活跃任务">
          {activeRuns.length === 0 ? (
            <EmptyState copy="No active runs are available right now." />
          ) : (
            <div className="space-y-3">
              {activeRuns.map((run) => (
                <RunCard
                  failedAtStage={run.failed_at_stage}
                  key={run.id}
                  stage={run.current_stage}
                  status={run.status}
                  summary={run.description || "No summary provided."}
                  ticket={run.ticket}
                />
              ))}
            </div>
          )}
        </SectionPanel>

        <div className="space-y-4">
          <SectionPanel kicker="Action Queue" title="待审批">
            {pendingApprovals.length === 0 ? (
              <EmptyState copy="No review gates are waiting for approval." />
            ) : (
              <div className="space-y-3">
                {pendingApprovals.map((run) => (
                  <article className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4" key={run.id}>
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-mono text-sm text-white">{run.ticket}</p>
                        <p className="mt-1 text-sm text-muted">{run.description || "Review gate is ready for a decision."}</p>
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

          <SectionPanel kicker="Pool Health" title="Agent 主机">
            {hostItems.length === 0 ? (
              <EmptyState copy="No registered hosts are available yet." />
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
