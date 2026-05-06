import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import useSWR from "swr";
import {
  archiveWorkspace,
  createWorkspace,
  listWorkspacePage,
  type ListWorkspacePageParams,
} from "../api/workspaces";
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
  { value: "active", label: "Active" },
  { value: "archived", label: "Archived" },
  { value: "all", label: "All" },
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
    <article className="rounded-[28px] border border-border bg-panel-strong/70 p-4 shadow-[0_0_0_1px_rgba(209,207,197,0.2)]">
      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
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
            Updated {new Date(workspace.updated_at).toLocaleString()}
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => onOpen(workspace.id)}
              className="rounded-xl bg-copy px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
            >
              Open
            </button>
            {workspace.status === "active" ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => onArchive(workspace.id)}
                className="rounded-xl border border-border-strong bg-panel px-4 py-2 text-sm font-medium text-muted transition hover:border-danger/30 hover:text-danger disabled:opacity-50"
              >
                Archive
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
      setError("Title is required");
      return;
    }
    if (!SLUG_RE.test(trimmedSlug)) {
      setError("Slug must be kebab-case, 1-63 chars, without leading or duplicate dashes");
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
      setError(extractError(err, "Failed to create workspace"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="grid gap-3 md:grid-cols-[1fr_1fr_auto]" onSubmit={handleSubmit}>
      <label className="space-y-1 text-sm text-muted">
        <span>Title</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Workspace title"
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
          {submitting ? "Creating..." : "New workspace"}
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
    if (typeof window !== "undefined" && !window.confirm("Archive this workspace?")) {
      return;
    }
    setArchivePending(id);
    setArchiveError(null);
    try {
      await archiveWorkspace(id);
      await query.mutate();
    } catch (err) {
      setArchiveError(extractError(err, "Failed to archive workspace"));
    } finally {
      setArchivePending(null);
    }
  }

  function handleCreated(workspace: Workspace) {
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
      <SectionPanel kicker="Create" title="Create workspace">
        <CreateForm onCreated={handleCreated} />
      </SectionPanel>

      <SectionPanel kicker="Directory" title="Workspace workbench">
        <div className="space-y-4">
          <div className="grid gap-3 xl:grid-cols-[1.2fr_auto_auto]">
            <label className="space-y-1 text-sm text-muted">
              <span>Search</span>
              <input
                className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
                value={search}
                onChange={(event) => updateFilters({ search: event.target.value, offset: 0 })}
                placeholder="Search by title or slug"
              />
            </label>

            <label className="space-y-1 text-sm text-muted">
              <span>Sort</span>
              <select
                className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
                value={sort}
                onChange={(event) => updateFilters({ sort: event.target.value as WorkspaceSort, offset: 0 })}
              >
                <option value="updated_desc">Recently updated</option>
                <option value="created_desc">Recently created</option>
                <option value="title_asc">Title A-Z</option>
                <option value="title_desc">Title Z-A</option>
              </select>
            </label>

            <div className="space-y-1">
              <span className="text-sm text-muted">Status</span>
              <SegmentedControl
                ariaLabel="Workspace status"
                options={STATUS_OPTIONS}
                value={status}
                onChange={(value) => updateFilters({ status: value, offset: 0 })}
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-panel px-4 py-3">
            <div>
              <p className="text-[11px] uppercase tracking-[0.22em] text-muted-soft">Result set</p>
              <p className="mt-1 text-sm text-copy">
                {page ? `${page.pagination.total} workspaces matching the current filters` : "Loading workspaces..."}
              </p>
            </div>
          </div>

          {query.error ? (
            <div className="rounded-2xl border border-danger/15 bg-danger/8 p-5">
              <h3 className="font-serif text-lg font-medium leading-tight tracking-tight text-copy">Workspace data failed to load</h3>
              <p className="mt-2 text-sm text-muted">Retry the request or adjust the current filters.</p>
              <button
                className="mt-4 rounded-xl bg-copy px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
                onClick={() => void query.mutate()}
                type="button"
              >
                Retry
              </button>
            </div>
          ) : !page ? (
            <LoadingSkeleton />
          ) : workspaces.length === 0 ? (
            <EmptyState copy="No workspaces match the current filters." />
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
              itemLabel="Workspaces"
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
