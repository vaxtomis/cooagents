import { Link } from "react-router-dom";
import useSWR from "swr";
import { getWorkspaceMetrics } from "../api/metrics";
import { listWorkspaces } from "../api/workspaces";
import { SectionPanel } from "../components/SectionPanel";
import { StatusBadge } from "../components/StatusBadge";
import { useWorkspacePolling } from "../hooks/useWorkspacePolling";
import type { Workspace } from "../types";

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

  const metricsQuery = useSWR(
    ["metrics", "workspaces"],
    () => getWorkspaceMetrics(),
    polling,
  );

  const activeWorkspaces = (workspacesQuery.data ?? []).slice().sort(compareUpdatedDesc);
  const metrics = metricsQuery.data;
  const metricsError = metricsQuery.error;

  // Distinguish loading ("-") from error ("—") so a failed /metrics/workspaces
  // call does not look like the initial fetch.
  const placeholder = metricsError ? "—" : "-";
  const activeValue = metrics ? metrics.active_workspaces.toString().padStart(2, "0") : placeholder;
  const interventionValue = metrics
    ? metrics.human_intervention_per_workspace.toFixed(2)
    : placeholder;
  const firstPassValue = metrics
    ? `${Math.round(metrics.first_pass_success_rate * 100)}%`
    : placeholder;
  const avgRoundsValue = metrics ? metrics.avg_iteration_rounds.toFixed(1) : placeholder;

  return (
    <div className="space-y-6">
      {metricsError ? (
        <p
          className="rounded-2xl border border-danger/15 bg-danger/8 p-4 text-sm text-muted"
          role="alert"
        >
          指标数据加载失败，已显示占位符。
        </p>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <HeroStat
          caption="当前活跃 Workspace 数量；每 15 秒自动刷新。"
          title="并行 active 数"
          value={activeValue}
        />
        <HeroStat
          caption="人工介入事件总数 / Workspace 总数（含已归档），全量统计。"
          title="人工介入 / Workspace"
          value={interventionValue}
        />
        <HeroStat
          caption="终态 DevWork 中 first_pass_success === true 的占比。"
          title="一次性准出率"
          value={firstPassValue}
        />
        <HeroStat
          caption="终态 DevWork 的迭代轮次平均值。"
          title="平均循环轮次"
          value={avgRoundsValue}
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
    </div>
  );
}
