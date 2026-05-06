import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import useSWR from "swr";
import {
  archiveWorkspace,
  createWorkspace,
  listWorkspacePage,
  type ListWorkspacePageParams,
} from "../api/workspaces";
import { AppDialog } from "../components/AppDialog";
import { PaginationControls } from "../components/PaginationControls";
import { EmptyState, SectionPanel } from "../components/SectionPanel";
import { SegmentedControl } from "../components/SegmentedControl";
import { StatusBadge } from "../components/StatusBadge";
import { useWorkspacePolling } from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type { Workspace, WorkspaceStatus } from "../types";

const SLUG_RE = /^[a-z0-9](?:[a-z0-9]|-(?!-)){0,61}[a-z0-9]$|^[a-z0-9]$/;
const PAGE_SIZE_OPTIONS = [6, 12, 24] as const;

type StatusFilter = WorkspaceStatus | "all";
type WorkspaceSort = NonNullable<ListWorkspacePageParams["sort"]>;

const STATUS_OPTIONS = [
  { value: "active", label: "活跃" },
  { value: "archived", label: "已归档" },
  { value: "all", label: "全部" },
] as const satisfies readonly { value: StatusFilter; label: string }[];

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} className="h-28 animate-pulse rounded-2xl border border-border bg-panel-strong/70" />
      ))}
    </div>
  );
}

function WorkspaceRow({
  workspace,
  onOpen,
  onArchive,
  busy,
}: {
  workspace: Workspace;
  onOpen: (id: string) => void;
  onArchive: (id: string) => void;
  busy: boolean;
}) {
  return (
    <article className="rounded-2xl border border-border bg-panel-strong/70 p-3 shadow-[0_0_0_1px_rgba(209,207,197,0.2)]">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="font-serif text-xl font-medium text-copy">{workspace.title}</h3>
            <StatusBadge status={workspace.status} />
          </div>
          <p className="font-mono text-xs text-muted">{workspace.slug}</p>
          <p className="truncate text-sm text-muted">{workspace.root_path}</p>
        </div>

        <div className="flex min-w-[220px] flex-col items-start gap-3 md:items-end">
          <p className="text-[11px] uppercase tracking-[0.22em] text-muted-soft">
            更新于 {new Date(workspace.updated_at).toLocaleString()}
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => onOpen(workspace.id)}
              className="rounded-xl bg-copy px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
            >
              打开
            </button>
            {workspace.status === "active" ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => onArchive(workspace.id)}
                className="rounded-xl border border-border-strong bg-panel px-4 py-2 text-sm font-medium text-muted transition hover:border-danger/30 hover:text-danger disabled:opacity-50"
              >
                归档
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </article>
  );
}

function CreateForm({ onCreated }: { onCreated: (workspace: Workspace) => void }) {
  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedTitle = title.trim();
    const trimmedSlug = slug.trim();
    if (!trimmedTitle) {
      setError("标题不能为空");
      return;
    }
    if (!SLUG_RE.test(trimmedSlug)) {
      setError("Slug 标识必须是 1-63 位 kebab-case，不能以短横线开头或包含连续短横线");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const created = await createWorkspace({ title: trimmedTitle, slug: trimmedSlug });
      setTitle("");
      setSlug("");
      onCreated(created);
    } catch (err) {
      setError(extractError(err, "创建 Workspace 失败"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="grid gap-3 md:grid-cols-[1fr_1fr_auto]" onSubmit={handleSubmit}>
      <label className="space-y-1 text-sm text-muted">
          <span>标题</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Workspace 标题"
          value={title}
        />
      </label>
      <label className="space-y-1 text-sm text-muted">
          <span>Slug 标识</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setSlug(event.target.value.toLowerCase())}
          placeholder="my-workspace"
          value={slug}
        />
      </label>
      <div className="flex items-end">
        <button
          className="w-full rounded-xl bg-accent px-4 py-3 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] transition hover:bg-accent-soft disabled:opacity-60 md:w-auto"
          disabled={submitting}
          type="submit"
        >
          {submitting ? "创建中..." : "创建 Workspace"}
        </button>
      </div>
      {error ? <p className="text-xs text-danger md:col-span-3">{error}</p> : null}
    </form>
  );
}

export function WorkspacesPage() {
  const polling = useWorkspacePolling();
  const navigate = useNavigate();
  const [status, setStatus] = useState<StatusFilter>("active");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<WorkspaceSort>("updated_desc");
  const [limit, setLimit] = useState<number>(12);
  const [offset, setOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [archivePending, setArchivePending] = useState<string | null>(null);
  const [archiveError, setArchiveError] = useState<string | null>(null);

  const query = useSWR(
    ["workspaces-page", status, search, sort, limit, offset],
    () =>
      listWorkspacePage({
        status: status === "all" ? undefined : status,
        query: search.trim() || undefined,
        sort,
        limit,
        offset,
      }),
    polling,
  );

  const page = query.data;
  const workspaces = page?.items ?? [];

  async function handleArchive(id: string) {
    if (typeof window !== "undefined" && !window.confirm("确认归档这个 Workspace？")) {
      return;
    }
    setArchivePending(id);
    setArchiveError(null);
    try {
      await archiveWorkspace(id);
      await query.mutate();
    } catch (err) {
      setArchiveError(extractError(err, "归档 Workspace 失败"));
    } finally {
      setArchivePending(null);
    }
  }

  function handleCreated(workspace: Workspace) {
    setCreateOpen(false);
    void query.mutate();
    navigate(`/workspaces/${workspace.id}`);
  }

  function updateFilters(next: Partial<{ status: StatusFilter; search: string; sort: WorkspaceSort; limit: number; offset: number }>) {
    if (next.status !== undefined) setStatus(next.status);
    if (next.search !== undefined) setSearch(next.search);
    if (next.sort !== undefined) setSort(next.sort);
    if (next.limit !== undefined) setLimit(next.limit);
    if (next.offset !== undefined) setOffset(next.offset);
  }

  return (
    <div className="space-y-6">
      <AppDialog
        description="填写标题和稳定 slug，创建后会直接进入该 Workspace。"
        onClose={() => setCreateOpen(false)}
        open={createOpen}
        title="新建 Workspace"
      >
        <CreateForm onCreated={handleCreated} />
      </AppDialog>

      <SectionPanel
        actions={
          <button
            className="rounded-xl bg-accent px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] transition hover:bg-accent-soft"
            onClick={() => setCreateOpen(true)}
            type="button"
          >
            新建 Workspace
          </button>
        }
        kicker="目录"
        title="Workspace 工作台"
      >
        <div className="space-y-4">
          <div className="grid gap-3 xl:grid-cols-[1.2fr_auto_auto]">
            <label className="space-y-1 text-sm text-muted">
              <span>搜索</span>
              <input
                className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
                value={search}
                onChange={(event) => updateFilters({ search: event.target.value, offset: 0 })}
                placeholder="按标题或 slug 搜索"
              />
            </label>

            <label className="space-y-1 text-sm text-muted">
              <span>排序</span>
              <select
                className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
                value={sort}
                onChange={(event) => updateFilters({ sort: event.target.value as WorkspaceSort, offset: 0 })}
              >
                <option value="updated_desc">最近更新</option>
                <option value="created_desc">最近创建</option>
                <option value="title_asc">标题 A-Z</option>
                <option value="title_desc">标题 Z-A</option>
              </select>
            </label>

            <div className="space-y-1">
              <span className="text-sm text-muted">状态</span>
              <SegmentedControl
                ariaLabel="Workspace 状态"
                options={STATUS_OPTIONS}
                value={status}
                onChange={(value) => updateFilters({ status: value, offset: 0 })}
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-panel px-4 py-3">
            <div>
              <p className="text-[11px] uppercase tracking-[0.22em] text-muted-soft">结果集</p>
              <p className="mt-1 text-sm text-copy">
                {page ? `当前筛选命中 ${page.pagination.total} 个 Workspace` : "正在加载 Workspace..."}
              </p>
            </div>
          </div>

          {query.error ? (
            <div className="rounded-2xl border border-danger/15 bg-danger/8 p-5">
              <h3 className="font-serif text-lg font-medium leading-tight tracking-tight text-copy">
                Workspace 数据加载失败
              </h3>
              <p className="mt-2 text-sm text-muted">请重试请求，或调整当前筛选条件。</p>
              <button
                className="mt-4 rounded-xl bg-copy px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
                onClick={() => void query.mutate()}
                type="button"
              >
                重试
              </button>
            </div>
          ) : !page ? (
            <LoadingSkeleton />
          ) : workspaces.length === 0 ? (
            <EmptyState copy="当前筛选条件下没有 Workspace。" />
          ) : (
            <div className="space-y-3">
              {workspaces.map((workspace) => (
                <WorkspaceRow
                  key={workspace.id}
                  workspace={workspace}
                  busy={archivePending === workspace.id}
                  onArchive={handleArchive}
                  onOpen={(id) => navigate(`/workspaces/${id}`)}
                />
              ))}
            </div>
          )}

          {page ? (
            <PaginationControls
              pagination={page.pagination}
              itemLabel="Workspace"
              pageSizeOptions={[...PAGE_SIZE_OPTIONS]}
              onPageChange={(nextOffset) => updateFilters({ offset: nextOffset })}
              onPageSizeChange={(nextLimit) => updateFilters({ limit: nextLimit, offset: 0 })}
              disabled={query.isLoading}
            />
          ) : null}

          {archiveError ? <p className="text-xs text-danger">{archiveError}</p> : null}
        </div>
      </SectionPanel>
    </div>
  );
}
