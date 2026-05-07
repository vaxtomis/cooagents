import { Database, RefreshCw, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import useSWR from "swr";
import {
  createRepo,
  deleteRepo,
  fetchRepo,
  listRepoPage,
  syncRepos,
  type ListRepoPageParams,
} from "../api/repos";
import { AppDialog } from "../components/AppDialog";
import { PaginationControls } from "../components/PaginationControls";
import { EmptyState, SectionPanel } from "../components/SectionPanel";
import { SegmentedControl } from "../components/SegmentedControl";
import { RepoFetchStatusBadge } from "../components/RepoFetchStatusBadge";
import { useWorkspacePolling } from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type { CreateRepoPayload, Repo, RepoFetchStatus, RepoRole } from "../types";

const REPO_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$/;
const ROLES: RepoRole[] = ["backend", "frontend", "fullstack", "infra", "docs", "other"];
const ROLE_LABELS: Record<RepoRole, string> = {
  backend: "后端",
  frontend: "前端",
  fullstack: "全栈",
  infra: "基础设施",
  docs: "文档",
  other: "其他",
};
const PAGE_SIZE_OPTIONS = [6, 12, 24] as const;

type RepoStatusFilter = RepoFetchStatus | "all";
type RepoRoleFilter = RepoRole | "all";
type RepoSort = NonNullable<ListRepoPageParams["sort"]>;

const STATUS_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "healthy", label: "健康" },
  { value: "error", label: "需处理" },
  { value: "unknown", label: "未知" },
] as const satisfies readonly { value: RepoStatusFilter; label: string }[];

interface SyncSummary {
  in_sync: number;
  fs_only: number;
  db_only: number;
}

interface PendingAction {
  kind: "fetch" | "delete";
  id: string;
}

const PRIMARY_ACTION_BUTTON_CLASSNAME =
  "inline-flex items-center justify-center gap-2 rounded-2xl border border-accent-soft/70 bg-[linear-gradient(180deg,rgba(208,160,90,0.98),rgba(169,112,45,0.92))] px-5 py-3 text-sm font-semibold text-ink-invert shadow-[0_18px_34px_rgba(0,0,0,0.36),inset_0_1px_0_rgba(255,255,255,0.16)] transition hover:-translate-y-0.5 hover:brightness-105 disabled:opacity-60 disabled:hover:translate-y-0";
const NEUTRAL_ACTION_BUTTON_CLASSNAME =
  "inline-flex items-center justify-center gap-2 rounded-2xl border border-copy/20 bg-copy px-4 py-3 text-sm font-semibold text-ink-invert shadow-[0_16px_30px_rgba(0,0,0,0.26)] transition hover:-translate-y-0.5 hover:bg-copy/92 disabled:opacity-60 disabled:hover:translate-y-0";
const SECONDARY_ACTION_BUTTON_CLASSNAME =
  "inline-flex items-center justify-center gap-2 rounded-2xl border border-border-dark/60 bg-panel-strong/85 px-4 py-3 text-sm font-medium text-copy-soft shadow-[0_14px_28px_rgba(0,0,0,0.28)] transition hover:border-accent/50 hover:bg-panel hover:text-copy disabled:opacity-50";
const DANGER_ACTION_BUTTON_CLASSNAME =
  "inline-flex items-center justify-center gap-2 rounded-2xl border border-border-dark/60 bg-panel-strong/85 px-4 py-3 text-sm font-medium text-copy-soft shadow-[0_14px_28px_rgba(0,0,0,0.28)] transition hover:border-danger/45 hover:bg-danger/10 hover:text-danger disabled:opacity-50";
const FORM_FIELD_CLASSNAME =
  "w-full rounded-2xl border border-border-strong bg-panel px-4 py-3.5 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]";
const FILTER_BAR_CLASSNAME =
  "grid gap-4 rounded-[24px] border border-border bg-panel-strong/42 p-4 shadow-[0_16px_30px_rgba(0,0,0,0.18)] xl:grid-cols-[minmax(20rem,1.5fr)_minmax(13rem,0.8fr)_minmax(14rem,0.8fr)_auto] xl:items-end";

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} className="h-28 animate-pulse rounded-2xl border border-border bg-panel-strong/70" />
      ))}
    </div>
  );
}

function RepoRow({
  repo,
  onOpen,
  onFetch,
  onDelete,
  fetching,
  deleting,
}: {
  repo: Repo;
  onOpen: (id: string) => void;
  onFetch: (id: string) => void;
  onDelete: (id: string) => void;
  fetching: boolean;
  deleting: boolean;
}) {
  return (
    <article className="rounded-[26px] border border-border bg-panel-strong/72 p-4 shadow-[0_0_0_1px_rgba(209,207,197,0.2),0_18px_36px_rgba(0,0,0,0.24)]">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex min-w-0 gap-4">
          <div className="flex size-12 shrink-0 items-center justify-center rounded-[20px] bg-accent/12 text-accent shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
            <Database className="size-5" strokeWidth={1.8} />
          </div>
          <div className="min-w-0 space-y-3">
            <div className="flex flex-wrap items-center gap-3.5">
              <h3 className="font-serif text-xl font-medium text-copy">{repo.name}</h3>
              <RepoFetchStatusBadge repo={repo} />
            </div>
            <p className="font-mono text-xs text-muted">{repo.id}</p>
            <p className="truncate font-mono text-sm text-muted">{repo.url}</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-muted-soft">
              <span>角色：{ROLE_LABELS[repo.role]}</span>
              <span>默认分支：{repo.default_branch}</span>
              <span>更新：{new Date(repo.updated_at).toLocaleString()}</span>
              {repo.last_fetched_at ? <span>Fetch：{new Date(repo.last_fetched_at).toLocaleString()}</span> : null}
            </div>
            {repo.last_fetch_err ? (
              <p className="rounded-xl border border-danger/15 bg-danger/8 px-3 py-2 text-xs text-danger">
                {repo.last_fetch_err}
              </p>
            ) : null}
          </div>
        </div>

        <div className="flex flex-wrap gap-3 lg:max-w-[18rem] lg:justify-end">
          <button
            type="button"
            onClick={() => onOpen(repo.id)}
            className={NEUTRAL_ACTION_BUTTON_CLASSNAME}
          >
            打开
          </button>
          <button
            type="button"
            disabled={fetching}
            onClick={() => onFetch(repo.id)}
            className={SECONDARY_ACTION_BUTTON_CLASSNAME}
          >
            <RefreshCw className="size-4" strokeWidth={1.8} />
            {fetching ? "Fetch 中..." : "立即 fetch"}
          </button>
          <button
            type="button"
            disabled={deleting}
            onClick={() => onDelete(repo.id)}
            className={DANGER_ACTION_BUTTON_CLASSNAME}
          >
            <Trash2 className="size-4" strokeWidth={1.8} />
            删除
          </button>
        </div>
      </div>
    </article>
  );
}

function CreateForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [defaultBranch, setDefaultBranch] = useState("main");
  const [role, setRole] = useState<RepoRole>("backend");
  const [sshKeyPath, setSshKeyPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = name.trim();
    const trimmedUrl = url.trim();
    const trimmedBranch = defaultBranch.trim();
    const trimmedSshKey = sshKeyPath.trim();
    if (!REPO_NAME_RE.test(trimmedName)) {
      setError("仓库名称必须以字母或数字开头，可包含 _ . -");
      return;
    }
    if (!trimmedUrl) {
      setError("仓库 URL 不能为空");
      return;
    }
    if (!trimmedBranch) {
      setError("默认分支不能为空");
      return;
    }

    setError(null);
    setSubmitting(true);
    const payload: CreateRepoPayload = {
      name: trimmedName,
      url: trimmedUrl,
      default_branch: trimmedBranch,
      role,
      ssh_key_path: trimmedSshKey ? trimmedSshKey : null,
    };
    try {
      await createRepo(payload);
      setName("");
      setUrl("");
      setDefaultBranch("main");
      setRole("backend");
      setSshKeyPath("");
      onCreated();
    } catch (err) {
      setError(extractError(err, "登记仓库失败"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="space-y-5" onSubmit={handleSubmit}>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="space-y-1.5 text-sm text-muted">
          <span>名称</span>
          <input
            className={`${FORM_FIELD_CLASSNAME} font-mono`}
            onChange={(event) => setName(event.target.value)}
            placeholder="frontend"
            value={name}
          />
        </label>
        <label className="space-y-1.5 text-sm text-muted">
          <span>角色</span>
          <select
            className={FORM_FIELD_CLASSNAME}
            onChange={(event) => setRole(event.target.value as RepoRole)}
            value={role}
          >
            {ROLES.map((option) => (
              <option key={option} value={option}>
                {ROLE_LABELS[option]}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1.5 text-sm text-muted md:col-span-2">
          <span>URL</span>
          <input
            className={`${FORM_FIELD_CLASSNAME} font-mono`}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="git@github.com:org/repo.git"
            value={url}
          />
        </label>
        <label className="space-y-1.5 text-sm text-muted">
          <span>默认分支</span>
          <input
            className={`${FORM_FIELD_CLASSNAME} font-mono`}
            onChange={(event) => setDefaultBranch(event.target.value)}
            placeholder="main"
            value={defaultBranch}
          />
        </label>
        <label className="space-y-1.5 text-sm text-muted">
          <span>SSH key 路径</span>
          <input
            className={`${FORM_FIELD_CLASSNAME} font-mono`}
            onChange={(event) => setSshKeyPath(event.target.value)}
            placeholder="~/.ssh/id_ed25519"
            value={sshKeyPath}
          />
        </label>
      </div>
      {error ? <p className="text-xs text-danger">{error}</p> : null}
      <div className="flex flex-col gap-3 border-t border-border/70 pt-4 sm:flex-row sm:items-center sm:justify-end">
        <button
          className={`${PRIMARY_ACTION_BUTTON_CLASSNAME} w-full sm:w-auto`}
          disabled={submitting}
          type="submit"
        >
          {submitting ? "登记中..." : "登记"}
        </button>
      </div>
    </form>
  );
}

export function ReposPage() {
  const polling = useWorkspacePolling();
  const navigate = useNavigate();
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [syncReport, setSyncReport] = useState<SyncSummary | null>(null);
  const [status, setStatus] = useState<RepoStatusFilter>("all");
  const [role, setRole] = useState<RepoRoleFilter>("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<RepoSort>("updated_desc");
  const [limit, setLimit] = useState<number>(12);
  const [offset, setOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);

  const query = useSWR(
    ["repos-page", status, role, search, sort, limit, offset],
    () =>
      listRepoPage({
        fetch_status: status === "all" ? undefined : status,
        role: role === "all" ? undefined : role,
        query: search.trim() || undefined,
        sort,
        limit,
        offset,
      }),
    polling,
  );

  const repos = query.data?.items ?? [];

  async function handleFetch(id: string) {
    setPending({ kind: "fetch", id });
    setActionError(null);
    try {
      await fetchRepo(id);
      await query.mutate();
    } catch (err) {
      setActionError(extractError(err, "Fetch 失败"));
    } finally {
      setPending(null);
    }
  }

  async function handleDelete(id: string) {
    if (typeof window !== "undefined" && !window.confirm(`确认删除仓库 ${id}？`)) {
      return;
    }
    setPending({ kind: "delete", id });
    setActionError(null);
    try {
      await deleteRepo(id);
      await query.mutate();
    } catch (err) {
      setActionError(extractError(err, "删除失败"));
    } finally {
      setPending(null);
    }
  }

  async function handleSync() {
    if (typeof window !== "undefined" && !window.confirm("确认从 config/repos.yaml 同步仓库注册表？")) {
      return;
    }
    setActionError(null);
    try {
      const report = await syncRepos();
      setSyncReport({
        in_sync: report.in_sync.length,
        fs_only: report.fs_only.length,
        db_only: report.db_only.length,
      });
      await query.mutate();
    } catch (err) {
      setActionError(extractError(err, "同步失败"));
    }
  }

  function updateFilters(
    next: Partial<{
      status: RepoStatusFilter;
      role: RepoRoleFilter;
      search: string;
      sort: RepoSort;
      limit: number;
      offset: number;
    }>,
  ) {
    if (next.status !== undefined) setStatus(next.status);
    if (next.role !== undefined) setRole(next.role);
    if (next.search !== undefined) setSearch(next.search);
    if (next.sort !== undefined) setSort(next.sort);
    if (next.limit !== undefined) setLimit(next.limit);
    if (next.offset !== undefined) setOffset(next.offset);
  }

  return (
    <div className="space-y-6">
      <AppDialog
        size="wide"
        description="登记后可在仓库详情中浏览分支、目录树、文件和提交历史。"
        onClose={() => setCreateOpen(false)}
        open={createOpen}
        title="登记仓库"
      >
        <CreateForm
          onCreated={() => {
            setCreateOpen(false);
            void query.mutate();
          }}
        />
      </AppDialog>

      <SectionPanel
        kicker="目录"
        title="仓库注册表"
        actions={
          <>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className={PRIMARY_ACTION_BUTTON_CLASSNAME}
            >
              登记仓库
            </button>
            <button
              type="button"
              onClick={() => void handleSync()}
              className={SECONDARY_ACTION_BUTTON_CLASSNAME}
            >
              <RefreshCw className="size-3.5" strokeWidth={1.8} />
              同步配置
            </button>
          </>
        }
      >
        <div className="space-y-4">
          <div className={FILTER_BAR_CLASSNAME}>
            <label className="space-y-1.5 text-sm text-muted">
              <span>搜索</span>
              <input
                className={FORM_FIELD_CLASSNAME}
                value={search}
                onChange={(event) => updateFilters({ search: event.target.value, offset: 0 })}
                placeholder="按名称、URL 或分支搜索"
              />
            </label>
            <label className="space-y-1.5 text-sm text-muted">
              <span>角色</span>
              <select
                className={FORM_FIELD_CLASSNAME}
                value={role}
                onChange={(event) => updateFilters({ role: event.target.value as RepoRoleFilter, offset: 0 })}
              >
                <option value="all">全部角色</option>
                {ROLES.map((option) => (
                  <option key={option} value={option}>
                    {ROLE_LABELS[option]}
                  </option>
                ))}
              </select>
            </label>

            <label className="space-y-1.5 text-sm text-muted">
              <span>排序</span>
              <select
                className={FORM_FIELD_CLASSNAME}
                value={sort}
                onChange={(event) => updateFilters({ sort: event.target.value as RepoSort, offset: 0 })}
              >
                <option value="updated_desc">最近更新</option>
                <option value="last_fetched_desc">最近 fetch</option>
                <option value="name_asc">名称 A-Z</option>
                <option value="name_desc">名称 Z-A</option>
              </select>
            </label>

            <div className="space-y-1.5 text-sm text-muted xl:justify-self-end">
              <span className="block">健康度</span>
              <div className="flex flex-wrap xl:justify-end">
                <SegmentedControl
                  ariaLabel="仓库 fetch 状态"
                  options={STATUS_OPTIONS}
                  value={status}
                  onChange={(value) => updateFilters({ status: value, offset: 0 })}
                />
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-panel px-4 py-3">
            <div>
              <p className="text-[11px] uppercase tracking-[0.22em] text-muted-soft">结果集</p>
              <p className="mt-1 text-sm text-copy">
                {query.data ? `当前筛选命中 ${query.data.pagination.total} 个仓库` : "正在加载仓库..."}
              </p>
            </div>
            {syncReport ? (
              <p className="text-xs text-muted">
                最近同步：一致 {syncReport.in_sync} / 仅文件 {syncReport.fs_only} / 仅数据库 {syncReport.db_only}
              </p>
            ) : null}
          </div>

          {query.error ? (
            <div className="rounded-2xl border border-danger/15 bg-danger/8 p-5">
              <h3 className="font-serif text-lg font-medium leading-tight tracking-tight text-copy">仓库数据加载失败</h3>
              <p className="mt-2 text-sm text-muted">请重试请求，或收窄当前筛选条件。</p>
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
          ) : repos.length === 0 ? (
            <EmptyState copy="当前筛选条件下没有仓库。" />
          ) : (
            <div className="space-y-3">
              {repos.map((repo) => (
                <RepoRow
                  key={repo.id}
                  repo={repo}
                  fetching={pending?.kind === "fetch" && pending.id === repo.id}
                  deleting={pending?.kind === "delete" && pending.id === repo.id}
                  onDelete={handleDelete}
                  onFetch={handleFetch}
                  onOpen={(id) => navigate(`/repos/${id}`)}
                />
              ))}
            </div>
          )}

          {query.data ? (
            <PaginationControls
              pagination={query.data.pagination}
              itemLabel="仓库"
              pageSizeOptions={[...PAGE_SIZE_OPTIONS]}
              onPageChange={(nextOffset) => updateFilters({ offset: nextOffset })}
              onPageSizeChange={(nextLimit) => updateFilters({ limit: nextLimit, offset: 0 })}
              disabled={query.isLoading}
            />
          ) : null}

          {actionError ? <p className="text-xs text-danger">{actionError}</p> : null}
        </div>
      </SectionPanel>
    </div>
  );
}
