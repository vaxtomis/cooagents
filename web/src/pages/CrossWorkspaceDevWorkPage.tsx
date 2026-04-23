import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import useSWR from "swr";
import { listDevWorks } from "../api/devWorks";
import { listWorkspaces } from "../api/workspaces";
import { SectionPanel } from "../components/SectionPanel";
import { StatusBadge } from "../components/StatusBadge";
import { useWorkspacePolling } from "../hooks/useWorkspacePolling";
import type { DevWork, DevWorkStep, Workspace } from "../types";

const STEP_FILTER_OPTIONS: { value: DevWorkStep | ""; label: string }[] = [
  { value: "", label: "全部" },
  { value: "INIT", label: "就绪" },
  { value: "STEP1_VALIDATE", label: "Step1" },
  { value: "STEP2_ITERATION", label: "Step2" },
  { value: "STEP3_CONTEXT", label: "Step3" },
  { value: "STEP4_DEVELOP", label: "Step4" },
  { value: "STEP5_REVIEW", label: "Step5" },
  { value: "COMPLETED", label: "完成" },
  { value: "ESCALATED", label: "升级" },
];

type GroupedRow = { workspace: Workspace; devWorks: DevWork[] };

export function CrossWorkspaceDevWorkPage() {
  const polling = useWorkspacePolling();
  const [stepFilter, setStepFilter] = useState<DevWorkStep | "">("");

  const workspacesQuery = useSWR(
    ["workspaces", "active"],
    () => listWorkspaces("active"),
    polling,
  );
  const activeWorkspaces = useMemo<Workspace[]>(
    () => workspacesQuery.data ?? [],
    [workspacesQuery.data],
  );
  const fanKey = activeWorkspaces.length > 0 ? JSON.stringify(activeWorkspaces.map((ws) => ws.id)) : null;

  const devWorksQuery = useSWR(
    fanKey ? ["cross-dev-works", fanKey] : null,
    async () => {
      const results = await Promise.all(
        activeWorkspaces.map(async (ws) => {
          const devWorks = await listDevWorks(ws.id).catch(() => [] as DevWork[]);
          return { workspace: ws, devWorks };
        }),
      );
      return results;
    },
    polling,
  );

  const groups = useMemo<GroupedRow[]>(() => {
    const data = devWorksQuery.data ?? [];
    if (!stepFilter) return data;
    return data
      .map((row) => ({ ...row, devWorks: row.devWorks.filter((dv) => dv.current_step === stepFilter) }))
      .filter((row) => row.devWorks.length > 0);
  }, [devWorksQuery.data, stepFilter]);

  return (
    <div className="space-y-6">
      <SectionPanel
        actions={
          <label className="flex items-center gap-2 text-xs text-muted">
            <span>步骤</span>
            <select
              className="rounded-xl border border-border-strong bg-panel-strong px-3 py-2 text-xs text-copy outline-none [&_option]:bg-panel-strong"
              onChange={(event) => setStepFilter(event.target.value as DevWorkStep | "")}
              value={stepFilter}
            >
              {STEP_FILTER_OPTIONS.map((option) => (
                <option key={option.value || "all"} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        }
        kicker="跨 Workspace"
        title="DevWork 总览"
      >
        {workspacesQuery.error ? (
          <p className="rounded-2xl border border-danger/15 bg-danger/8 p-4 text-sm text-muted">
            数据加载失败。
          </p>
        ) : activeWorkspaces.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
            当前没有活跃 Workspace。
          </p>
        ) : groups.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">
            当前筛选条件下暂无 DevWork。
          </p>
        ) : (
          <div className="space-y-5">
            {groups.map(({ workspace, devWorks }) => (
              <div className="space-y-3" key={workspace.id}>
                <div className="flex items-center justify-between gap-2">
                  <Link
                    className="font-serif text-lg text-copy hover:text-accent"
                    to={`/workspaces/${workspace.id}`}
                  >
                    {workspace.title}
                  </Link>
                  <span className="font-mono text-xs text-muted">{workspace.slug}</span>
                </div>
                {devWorks.length === 0 ? (
                  <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-3 text-xs text-muted">
                    无 DevWork。
                  </p>
                ) : (
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {devWorks.map((dv) => (
                      <Link
                        className="flex flex-col gap-2 rounded-2xl border border-border bg-panel-strong/80 p-4 transition hover:border-accent/30"
                        key={dv.id}
                        to={`/workspaces/${workspace.id}/dev-works/${dv.id}`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <p className="truncate font-mono text-xs text-copy">{dv.id}</p>
                          <StatusBadge status={dv.current_step} />
                        </div>
                        <p className="text-xs text-muted">
                          轮次 {dv.iteration_rounds} · 分数 {dv.last_score ?? "-"}
                        </p>
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </SectionPanel>
    </div>
  );
}
