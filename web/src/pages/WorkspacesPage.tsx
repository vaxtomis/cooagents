import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import useSWR from "swr";
import { archiveWorkspace, createWorkspace, listWorkspaces } from "../api/workspaces";
import { EmptyState, SectionPanel } from "../components/SectionPanel";
import { StatusBadge } from "../components/StatusBadge";
import { useWorkspacePolling } from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type { Workspace, WorkspaceStatus } from "../types";

const SLUG_RE = /^[a-z0-9](?:[a-z0-9]|-(?!-)){0,61}[a-z0-9]$|^[a-z0-9]$/;

type StatusFilter = WorkspaceStatus | "";

function LoadingSkeleton() {
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }, (_, index) => (
        <div className="h-36 animate-pulse rounded-2xl border border-border bg-panel-strong/70" key={index} />
      ))}
    </div>
  );
}

function WorkspaceCard({
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
    <article className="flex h-full flex-col rounded-2xl border border-border bg-panel-strong/80 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-serif text-lg font-medium text-copy">{workspace.title}</p>
          <p className="mt-1 truncate font-mono text-xs text-muted">{workspace.slug}</p>
        </div>
        <StatusBadge status={workspace.status} />
      </div>
      <p className="mt-4 truncate text-xs text-muted">{workspace.root_path}</p>
      <p className="mt-1 text-xs text-muted-soft">更新于 {workspace.updated_at}</p>
      <div className="mt-auto flex gap-2 pt-4">
        <button
          className="flex-1 rounded-lg bg-copy px-3 py-2 text-xs font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
          onClick={() => onOpen(workspace.id)}
          type="button"
        >
          打开
        </button>
        {workspace.status === "active" ? (
          <button
            className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-2 text-xs font-medium text-muted transition hover:border-danger/30 hover:text-danger disabled:opacity-50"
            disabled={busy}
            onClick={() => onArchive(workspace.id)}
            type="button"
          >
            归档
          </button>
        ) : null}
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
      setError("请填写标题");
      return;
    }
    if (!SLUG_RE.test(trimmedSlug)) {
      setError("slug 必须为 kebab-case（1-63 位，无首尾或连续短横线）");
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
      setError(extractError(err, "创建失败"));
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
          placeholder="工作区标题"
          value={title}
        />
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>Slug</span>
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
          {submitting ? "创建中..." : "新建 Workspace"}
        </button>
      </div>
      {error ? <p className="text-xs text-danger md:col-span-3">{error}</p> : null}
    </form>
  );
}

export function WorkspacesPage() {
  const polling = useWorkspacePolling();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<StatusFilter>("active");
  const [archivePending, setArchivePending] = useState<string | null>(null);
  const [archiveError, setArchiveError] = useState<string | null>(null);

  const query = useSWR(
    ["workspaces", filter],
    () => listWorkspaces(filter || undefined),
    polling,
  );

  const workspaces = query.data ?? [];

  async function handleArchive(id: string) {
    if (typeof window !== "undefined" && !window.confirm("确认归档此 Workspace？")) {
      return;
    }
    setArchivePending(id);
    setArchiveError(null);
    try {
      await archiveWorkspace(id);
      await query.mutate();
    } catch (err) {
      setArchiveError(extractError(err, "归档失败"));
    } finally {
      setArchivePending(null);
    }
  }

  function handleCreated(workspace: Workspace) {
    void query.mutate();
    navigate(`/workspaces/${workspace.id}`);
  }

  return (
    <div className="space-y-6">
      <SectionPanel kicker="新建" title="创建 Workspace">
        <CreateForm onCreated={handleCreated} />
      </SectionPanel>

      <SectionPanel
        actions={
          <div className="flex gap-2" role="radiogroup" aria-label="状态筛选">
            {(["active", "archived", ""] as StatusFilter[]).map((value) => {
              const label = value === "" ? "全部" : value === "active" ? "活跃" : "归档";
              const selected = filter === value;
              return (
                <button
                  aria-checked={selected}
                  className={[
                    "rounded-full border px-3 py-1.5 text-xs font-medium transition",
                    selected
                      ? "border-accent/30 bg-accent/15 text-copy"
                      : "border-border-strong bg-panel-strong/50 text-muted hover:border-copy/20 hover:text-copy",
                  ].join(" ")}
                  key={value || "all"}
                  onClick={() => setFilter(value)}
                  role="radio"
                  tabIndex={selected ? 0 : -1}
                  type="button"
                >
                  {label}
                </button>
              );
            })}
          </div>
        }
        kicker="清单"
        title="Workspaces"
      >
        {query.error ? (
          <div className="rounded-2xl border border-danger/15 bg-danger/8 p-5">
            <h3 className="font-serif text-lg font-medium leading-tight tracking-tight text-copy">Workspace 数据加载失败</h3>
            <p className="mt-2 text-sm text-muted">请重试。</p>
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
        ) : workspaces.length === 0 ? (
          <EmptyState copy="未找到匹配的 Workspace。" />
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {workspaces.map((workspace) => (
              <WorkspaceCard
                busy={archivePending === workspace.id}
                key={workspace.id}
                onArchive={handleArchive}
                onOpen={(id) => navigate(`/workspaces/${id}`)}
                workspace={workspace}
              />
            ))}
          </div>
        )}
        {archiveError ? <p className="mt-3 text-xs text-danger">{archiveError}</p> : null}
      </SectionPanel>
    </div>
  );
}
