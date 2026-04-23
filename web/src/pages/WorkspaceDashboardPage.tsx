import { useMemo } from "react";
import { Link } from "react-router-dom";
import useSWR from "swr";
import { listDevWorks } from "../api/devWorks";
import { listWorkspaceEvents } from "../api/workspaceEvents";
import { listWorkspaces } from "../api/workspaces";
import { SectionPanel } from "../components/SectionPanel";
import { StatusBadge } from "../components/StatusBadge";
import { useWorkspacePolling } from "../hooks/useWorkspacePolling";
import type { DevWork, Workspace, WorkspaceEventsEnvelope } from "../types";

const DASHBOARD_WORKSPACE_CAP = 20;
const INTERVENTION_EVENT = "workspace.human_intervention";
const EVENT_FETCH_LIMIT = 200;

function HeroStat({
  title,
  value,
  caption,
}: {
  title: string;
  value: string;
  caption: string;
}) {
  return (
    <section className="relative overflow-hidden rounded-[32px] border border-[color:var(--color-border-dark)] bg-[color:var(--color-panel-deep)] p-8 shadow-panel">
      <div className="pointer-events-none absolute -right-24 -top-24 size-72 rounded-full bg-accent/20 blur-3xl" aria-hidden />
      <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-[color:var(--color-accent-soft)]">
        {title}
      </p>
      <div className="mt-6 flex items-end gap-4">
        <div className="font-serif text-[4rem] font-medium leading-none tracking-tight text-[color:var(--color-ink-invert)] [font-variant-numeric:tabular-nums]">
          {value}
        </div>
      </div>
      <p className="mt-5 max-w-md text-sm leading-relaxed text-[color:var(--color-ink-invert-soft)]">
        {caption}
      </p>
    </section>
  );
}

function WorkspaceRow({ workspace }: { workspace: Workspace }) {
  return (
    <Link
      className="flex items-center justify-between gap-3 rounded-2xl border border-border bg-panel-strong/80 p-4 transition hover:border-accent/30"
      to={`/workspaces/${workspace.id}`}
    >
      <div className="min-w-0">
        <p className="truncate font-medium text-copy">{workspace.title}</p>
        <p className="mt-1 truncate font-mono text-xs text-muted">{workspace.slug}</p>
      </div>
      <StatusBadge status={workspace.status} />
    </Link>
  );
}

function compareUpdatedDesc(a: Workspace, b: Workspace) {
  // ISO-8601 strings sort lexicographically.
  return b.updated_at.localeCompare(a.updated_at);
}

export function WorkspaceDashboardPage() {
  const polling = useWorkspacePolling();

  const workspacesQuery = useSWR(
    ["workspaces", "active"],
    () => listWorkspaces("active"),
    polling,
  );

  const activeWorkspaces = workspacesQuery.data ?? [];
  const topWorkspaces = useMemo(
    () => [...activeWorkspaces].sort(compareUpdatedDesc).slice(0, DASHBOARD_WORKSPACE_CAP),
    [activeWorkspaces],
  );
  const topIds = useMemo(() => topWorkspaces.map((ws) => ws.id), [topWorkspaces]);
  const fanOutKey = topIds.length > 0 ? JSON.stringify(topIds) : null;

  const devWorksFanOut = useSWR(
    fanOutKey ? ["dashboard", "dev-works", fanOutKey] : null,
    async () => {
      const results = await Promise.all(topIds.map((id) => listDevWorks(id).catch(() => [] as DevWork[])));
      return results.flat();
    },
    polling,
  );

  const interventionFanOut = useSWR(
    fanOutKey ? ["dashboard", "interventions", fanOutKey] : null,
    async () => {
      const results = await Promise.all(
        topIds.map((id) =>
          listWorkspaceEvents(id, { limit: EVENT_FETCH_LIMIT, event_name: [INTERVENTION_EVENT] }).catch(
            () => ({ events: [], pagination: { limit: 0, offset: 0, has_more: false } } as WorkspaceEventsEnvelope),
          ),
        ),
      );
      return results;
    },
    polling,
  );

  const devWorks = devWorksFanOut.data ?? [];
  const interventionEnvelopes = interventionFanOut.data ?? [];

  const interventionCount = interventionEnvelopes.reduce((sum, env) => sum + env.events.length, 0);
  const truncated = interventionEnvelopes.some((env) => env.pagination.has_more);

  const total = devWorks.length;
  const successes = devWorks.filter((d) => d.first_pass_success === true).length;
  const firstPassRate = total === 0 ? null : Math.round((successes / total) * 100);
  const averageRounds =
    total === 0
      ? null
      : devWorks.reduce((sum, d) => sum + d.iteration_rounds, 0) / total;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <HeroStat
          caption="当前活跃 Workspace 数量；每 15 秒自动刷新。"
          title="并行 active 数"
          value={activeWorkspaces.length.toString().padStart(2, "0")}
        />
        <HeroStat
          caption={
            truncated
              ? "最近 30 天内的人工介入事件（样本截断；部分 Workspace 命中 200 条上限）。"
              : "最近 30 天内的人工介入事件。"
          }
          title="人工介入"
          value={interventionCount.toString().padStart(2, "0")}
        />
        <HeroStat
          caption="first_pass_success === true 的 DevWork 占比。"
          title="一次性准出率"
          value={firstPassRate === null ? "-" : `${firstPassRate}%`}
        />
        <HeroStat
          caption="DevWork 迭代轮次平均值。"
          title="平均循环轮次"
          value={averageRounds === null ? "-" : averageRounds.toFixed(1)}
        />
      </div>

      <SectionPanel kicker="清单" title="活跃 Workspaces">
        {workspacesQuery.error ? (
          <p className="rounded-2xl border border-danger/15 bg-danger/8 p-4 text-sm text-muted">
            Workspace 数据加载失败。
          </p>
        ) : activeWorkspaces.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
            当前没有活跃 Workspace。
          </p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {activeWorkspaces.map((ws) => (
              <WorkspaceRow key={ws.id} workspace={ws} />
            ))}
          </div>
        )}
      </SectionPanel>

      <p className="text-xs text-muted-soft">
        指标基于最近活跃的 {DASHBOARD_WORKSPACE_CAP} 个 Workspace 聚合；Phase 8 接入
        <code className="mx-1 font-mono">/api/metrics/workspaces</code>
        后改为全量。
      </p>
    </div>
  );
}
