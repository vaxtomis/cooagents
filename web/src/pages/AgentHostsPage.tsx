import { RefreshCw, Server, Trash2 } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import useSWR from "swr";
import {
  createAgentHost,
  deleteAgentHost,
  healthcheckAgentHost,
  listAgentHosts,
  syncAgentHosts,
} from "../api/agentHosts";
import { AppDialog } from "../components/AppDialog";
import { EmptyState, SectionPanel } from "../components/SectionPanel";
import { SegmentedControl } from "../components/SegmentedControl";
import { StatusBadge } from "../components/StatusBadge";
import { useWorkspacePolling } from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type {
  AgentHost,
  AgentHostType,
  CreateAgentHostPayload,
  HealthStatus,
} from "../types";

const LOCAL_HOST_ID = "local";
const HOST_RE = /^(local|[\w.-]+@[\w.-]+(?::\d+)?)$/;

const AGENT_TYPE_LABELS: Record<AgentHostType, string> = {
  both: "Claude + Codex",
  claude: "Claude",
  codex: "Codex",
};

const HEALTH_LABELS: Record<HealthStatus, string> = {
  healthy: "健康",
  unhealthy: "异常",
  unknown: "未知",
};

const HEALTH_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "healthy", label: "健康" },
  { value: "unhealthy", label: "异常" },
  { value: "unknown", label: "未知" },
] as const satisfies readonly { value: HealthStatus | "all"; label: string }[];

type HealthFilter = HealthStatus | "all";
type AgentTypeFilter = AgentHostType | "all";

interface SyncSummary {
  upserted: number;
  markedUnknown: number;
}

interface PendingAction {
  kind: "healthcheck" | "delete";
  id: string;
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 4 }, (_, index) => (
        <div
          key={index}
          className="h-28 animate-pulse rounded-2xl border border-border bg-panel-strong/70"
        />
      ))}
    </div>
  );
}

function AgentTypeChip({ type }: { type: AgentHostType }) {
  return (
    <span className="inline-flex shrink-0 items-center rounded-full border border-border bg-panel px-3 py-1 text-xs font-medium text-muted">
      {AGENT_TYPE_LABELS[type]}
    </span>
  );
}

function HostRow({
  host,
  checking,
  deleting,
  onHealthcheck,
  onDelete,
}: {
  host: AgentHost;
  checking: boolean;
  deleting: boolean;
  onHealthcheck: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const isLocal = host.id === LOCAL_HOST_ID;

  return (
    <article className="rounded-2xl border border-border bg-panel-strong/70 p-3 shadow-[0_0_0_1px_rgba(209,207,197,0.2)]">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex min-w-0 gap-4">
          <div className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-accent/10 text-accent">
            <Server className="size-5" strokeWidth={1.8} />
          </div>
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="font-serif text-xl font-medium text-copy">
                {isLocal ? "本机 Agent Host" : host.id}
              </h3>
              <StatusBadge status={host.health_status} label={HEALTH_LABELS[host.health_status]} />
              <AgentTypeChip type={host.agent_type} />
              {isLocal ? (
                <span className="inline-flex shrink-0 items-center rounded-full border border-border bg-panel px-3 py-1 text-xs font-medium text-muted-soft">
                  内置
                </span>
              ) : null}
            </div>
            <p className="font-mono text-sm text-muted">{host.host}</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-soft">
              <span>ID：{host.id}</span>
              <span>并发上限：{host.max_concurrent}</span>
              <span>
                最近探测：
                {host.last_health_at ? new Date(host.last_health_at).toLocaleString() : "未执行"}
              </span>
              <span>更新于：{new Date(host.updated_at).toLocaleString()}</span>
            </div>
            {host.labels.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {host.labels.map((label) => (
                  <span
                    key={label}
                    className="inline-flex items-center rounded-full border border-border bg-panel px-3 py-1 text-xs text-muted"
                  >
                    {label}
                  </span>
                ))}
              </div>
            ) : null}
            {host.last_health_err ? (
              <p className="rounded-xl border border-danger/15 bg-danger/8 px-3 py-2 text-xs text-danger">
                {host.last_health_err}
              </p>
            ) : null}
          </div>
        </div>

        <div className="flex flex-wrap gap-2 lg:justify-end">
          <button
            type="button"
            disabled={checking}
            onClick={() => onHealthcheck(host.id)}
            className="inline-flex items-center gap-2 rounded-xl border border-border-strong bg-panel px-4 py-2 text-sm font-medium text-muted transition hover:border-accent/40 hover:text-accent disabled:opacity-50"
          >
            <RefreshCw className="size-4" strokeWidth={1.8} />
            {checking ? "检查中..." : "健康检查"}
          </button>
          {!isLocal ? (
            <button
              type="button"
              disabled={deleting}
              onClick={() => onDelete(host.id)}
              className="inline-flex items-center gap-2 rounded-xl border border-border-strong bg-panel px-4 py-2 text-sm font-medium text-muted transition hover:border-danger/30 hover:text-danger disabled:opacity-50"
            >
              <Trash2 className="size-4" strokeWidth={1.8} />
              删除
            </button>
          ) : null}
        </div>
      </div>
    </article>
  );
}

function CreateForm({ onCreated }: { onCreated: () => void }) {
  const [id, setId] = useState("");
  const [host, setHost] = useState("");
  const [agentType, setAgentType] = useState<AgentHostType>("both");
  const [maxConcurrent, setMaxConcurrent] = useState("1");
  const [sshKey, setSshKey] = useState("");
  const [labelsText, setLabelsText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedId = id.trim();
    const trimmedHost = host.trim();
    const trimmedSshKey = sshKey.trim();
    const parsedMaxConcurrent = Number.parseInt(maxConcurrent, 10);
    const labels = labelsText
      .split(",")
      .map((entry) => entry.trim())
      .filter(Boolean);

    if (trimmedId === LOCAL_HOST_ID) {
      setError("local 是保留 ID，请留空或使用其他标识。");
      return;
    }
    if (!HOST_RE.test(trimmedHost)) {
      setError("连接地址必须是 local 或 user@host[:port]。");
      return;
    }
    if (!Number.isInteger(parsedMaxConcurrent) || parsedMaxConcurrent < 1 || parsedMaxConcurrent > 64) {
      setError("并发上限必须是 1 到 64 之间的整数。");
      return;
    }

    setError(null);
    setSubmitting(true);
    const payload: CreateAgentHostPayload = {
      host: trimmedHost,
      agent_type: agentType,
      max_concurrent: parsedMaxConcurrent,
      id: trimmedId || undefined,
      labels,
      ssh_key: trimmedSshKey || null,
    };

    try {
      await createAgentHost(payload);
      setId("");
      setHost("");
      setAgentType("both");
      setMaxConcurrent("1");
      setSshKey("");
      setLabelsText("");
      onCreated();
    } catch (err) {
      setError(extractError(err, "新增 Agent Host 失败"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="grid gap-3 md:grid-cols-[1fr_1.4fr_1fr_1fr] xl:grid-cols-[1fr_1.6fr_1fr_1fr_1.2fr_auto]" onSubmit={handleSubmit}>
      <label className="space-y-1 text-sm text-muted">
        <span>Host ID（可选）</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setId(event.target.value)}
          placeholder="ah-build-01"
          value={id}
        />
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>连接地址</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setHost(event.target.value)}
          placeholder="dev@10.0.0.5"
          value={host}
        />
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>Agent 类型</span>
        <select
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setAgentType(event.target.value as AgentHostType)}
          value={agentType}
        >
          <option value="both">Claude + Codex</option>
          <option value="claude">Claude</option>
          <option value="codex">Codex</option>
        </select>
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>并发上限</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          inputMode="numeric"
          min={1}
          max={64}
          onChange={(event) => setMaxConcurrent(event.target.value)}
          placeholder="1"
          type="number"
          value={maxConcurrent}
        />
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>SSH key 路径</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setSshKey(event.target.value)}
          placeholder="~/.ssh/id_ed25519"
          value={sshKey}
        />
      </label>
      <label className="space-y-1 text-sm text-muted xl:col-span-2">
        <span>标签</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setLabelsText(event.target.value)}
          placeholder="gpu, cn, high-memory"
          value={labelsText}
        />
      </label>
      <div className="flex items-end">
        <button
          className="w-full rounded-xl bg-accent px-4 py-3 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] transition hover:bg-accent-soft disabled:opacity-60 md:w-auto"
          disabled={submitting}
          type="submit"
        >
          {submitting ? "新增中..." : "新增 Agent Host"}
        </button>
      </div>
      {error ? <p className="text-xs text-danger md:col-span-4 xl:col-span-7">{error}</p> : null}
    </form>
  );
}

export function AgentHostsPage() {
  const polling = useWorkspacePolling();
  const [search, setSearch] = useState("");
  const [health, setHealth] = useState<HealthFilter>("all");
  const [agentType, setAgentType] = useState<AgentTypeFilter>("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [syncReport, setSyncReport] = useState<SyncSummary | null>(null);

  const query = useSWR(["agent-hosts"], listAgentHosts, polling);
  const hosts = query.data ?? [];

  const filteredHosts = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return hosts
      .filter((host) => {
        if (health !== "all" && host.health_status !== health) return false;
        if (agentType !== "all" && host.agent_type !== agentType) return false;
        if (!needle) return true;
        return [
          host.id,
          host.host,
          AGENT_TYPE_LABELS[host.agent_type],
          ...host.labels,
        ]
          .join(" ")
          .toLowerCase()
          .includes(needle);
      })
      .sort((left, right) => {
        if (left.id === LOCAL_HOST_ID) return -1;
        if (right.id === LOCAL_HOST_ID) return 1;
        return right.updated_at.localeCompare(left.updated_at);
      });
  }, [agentType, health, hosts, search]);

  async function handleHealthcheck(id: string) {
    setPending({ kind: "healthcheck", id });
    setActionError(null);
    try {
      await healthcheckAgentHost(id);
      await query.mutate();
    } catch (err) {
      setActionError(extractError(err, "健康检查失败"));
    } finally {
      setPending(null);
    }
  }

  async function handleDelete(id: string) {
    if (typeof window !== "undefined" && !window.confirm(`确认删除 Agent Host ${id}？`)) {
      return;
    }
    setPending({ kind: "delete", id });
    setActionError(null);
    try {
      await deleteAgentHost(id);
      await query.mutate();
    } catch (err) {
      setActionError(extractError(err, "删除 Agent Host 失败"));
    } finally {
      setPending(null);
    }
  }

  async function handleSync() {
    if (typeof window !== "undefined" && !window.confirm("确认从 config/agents.yaml 同步 Agent Host 配置？")) {
      return;
    }
    setActionError(null);
    try {
      const report = await syncAgentHosts();
      setSyncReport({
        upserted: report.upserted,
        markedUnknown: report.marked_unknown,
      });
      await query.mutate();
    } catch (err) {
      setActionError(extractError(err, "同步 Agent Host 配置失败"));
    }
  }

  return (
    <div className="space-y-6">
      <AppDialog
        description="登记后可以对 Agent Host 执行健康检查，并作为远端执行节点参与任务调度。"
        onClose={() => setCreateOpen(false)}
        open={createOpen}
        title="新增 Agent Host"
      >
        <CreateForm
          onCreated={() => {
            setCreateOpen(false);
            void query.mutate();
          }}
        />
      </AppDialog>

      <SectionPanel
        kicker="基础设施"
        title="Agent Host 管理"
        actions={
          <>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="rounded-xl bg-accent px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] transition hover:bg-accent-soft"
            >
              新增 Agent Host
            </button>
            <button
              type="button"
              onClick={() => void handleSync()}
              className="inline-flex items-center gap-2 rounded-xl border border-border-strong bg-panel-strong/50 px-3 py-2 text-sm font-medium text-muted transition hover:border-accent/40 hover:text-accent"
            >
              <RefreshCw className="size-3.5" strokeWidth={1.8} />
              同步配置
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-3 xl:grid-cols-[1.2fr_1fr_auto]">
            <label className="space-y-1 text-sm text-muted">
              <span>搜索</span>
              <input
                className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
                onChange={(event) => setSearch(event.target.value)}
                placeholder="按 ID、连接地址或标签搜索"
                value={search}
              />
            </label>

            <label className="space-y-1 text-sm text-muted">
              <span>Agent 类型</span>
              <select
                className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
                onChange={(event) => setAgentType(event.target.value as AgentTypeFilter)}
                value={agentType}
              >
                <option value="all">全部类型</option>
                <option value="both">Claude + Codex</option>
                <option value="claude">Claude</option>
                <option value="codex">Codex</option>
              </select>
            </label>

            <div className="space-y-1">
              <span className="text-sm text-muted">健康状态</span>
              <SegmentedControl
                ariaLabel="Agent Host 健康状态"
                options={HEALTH_OPTIONS}
                onChange={(value) => setHealth(value)}
                value={health}
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-panel px-4 py-3">
            <div>
              <p className="text-[11px] uppercase tracking-[0.22em] text-muted-soft">结果集</p>
              <p className="mt-1 text-sm text-copy">
                {query.data
                  ? `当前显示 ${filteredHosts.length} / ${hosts.length} 个 Agent Host`
                  : "正在加载 Agent Host..."}
              </p>
            </div>
            {syncReport ? (
              <p className="text-xs text-muted">
                最近同步：新增或更新 {syncReport.upserted} 个 / 标记未知 {syncReport.markedUnknown} 个
              </p>
            ) : null}
          </div>

          {query.error ? (
            <div className="rounded-2xl border border-danger/15 bg-danger/8 p-5">
              <h3 className="font-serif text-lg font-medium leading-tight tracking-tight text-copy">
                Agent Host 数据加载失败
              </h3>
              <p className="mt-2 text-sm text-muted">请重试请求，或检查后端服务与当前会话状态。</p>
              <button
                className="mt-4 rounded-xl bg-copy px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
                onClick={() => void query.mutate()}
                type="button"
              >
                重试
              </button>
            </div>
          ) : !query.data ? (
            <LoadingSkeleton />
          ) : filteredHosts.length === 0 ? (
            <EmptyState copy="当前筛选条件下没有 Agent Host。" />
          ) : (
            <div className="space-y-3">
              {filteredHosts.map((host) => (
                <HostRow
                  key={host.id}
                  host={host}
                  checking={pending?.kind === "healthcheck" && pending.id === host.id}
                  deleting={pending?.kind === "delete" && pending.id === host.id}
                  onDelete={handleDelete}
                  onHealthcheck={handleHealthcheck}
                />
              ))}
            </div>
          )}

          {actionError ? <p className="text-xs text-danger">{actionError}</p> : null}
        </div>
      </SectionPanel>
    </div>
  );
}
