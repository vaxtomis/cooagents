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
import { PaginationControls } from "../components/PaginationControls";
import { EmptyState, SectionPanel } from "../components/SectionPanel";
import { SegmentedControl } from "../components/SegmentedControl";
import { RepoFetchStatusBadge } from "../components/RepoFetchStatusBadge";
import { useWorkspacePolling } from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type { CreateRepoPayload, Repo, RepoFetchStatus, RepoRole } from "../types";

const REPO_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$/;
const ROLES: RepoRole[] = ["backend", "frontend", "fullstack", "infra", "docs", "other"];
const PAGE_SIZE_OPTIONS = [6, 12, 24] as const;

type RepoStatusFilter = RepoFetchStatus | "all";
type RepoRoleFilter = RepoRole | "all";
type RepoSort = NonNullable<ListRepoPageParams["sort"]>;

const STATUS_OPTIONS = [
  { value: "all", label: "All" },
  { value: "healthy", label: "Healthy" },
  { value: "error", label: "Attention" },
  { value: "unknown", label: "Unknown" },
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
    <article className="rounded-[28px] border border-border bg-panel-strong/70 p-4 shadow-[0_0_0_1px_rgba(209,207,197,0.2)]">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex min-w-0 gap-4">
          <div className="flex size-11 shrink-0 items-center justify-center rounded-2xl bg-accent/10 text-accent">
            <Database className="size-5" strokeWidth={1.8} />
          </div>
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="font-serif text-xl font-medium text-copy">{repo.name}</h3>
              <RepoFetchStatusBadge repo={repo} />
            </div>
            <p className="font-mono text-xs text-muted">{repo.id}</p>
            <p className="truncate font-mono text-sm text-muted">{repo.url}</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-soft">
              <span>Role: {repo.role}</span>
              <span>Default branch: {repo.default_branch}</span>
              <span>Updated: {new Date(repo.updated_at).toLocaleString()}</span>
              {repo.last_fetched_at ? <span>Fetched: {new Date(repo.last_fetched_at).toLocaleString()}</span> : null}
            </div>
            {repo.last_fetch_err ? (
              <p className="rounded-xl border border-danger/15 bg-danger/8 px-3 py-2 text-xs text-danger">
                {repo.last_fetch_err}
              </p>
            ) : null}
          </div>
        </div>

        <div className="flex flex-wrap gap-2 lg:justify-end">
          <button
            type="button"
            onClick={() => onOpen(repo.id)}
            className="rounded-xl bg-copy px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
          >
            Open
          </button>
          <button
            type="button"
            disabled={fetching}
            onClick={() => onFetch(repo.id)}
            className="inline-flex items-center gap-2 rounded-xl border border-border-strong bg-panel px-4 py-2 text-sm font-medium text-muted transition hover:border-accent/40 hover:text-accent disabled:opacity-50"
          >
            <RefreshCw className="size-4" strokeWidth={1.8} />
            {fetching ? "Fetching..." : "Fetch now"}
          </button>
          <button
            type="button"
            disabled={deleting}
            onClick={() => onDelete(repo.id)}
            className="inline-flex items-center gap-2 rounded-xl border border-border-strong bg-panel px-4 py-2 text-sm font-medium text-muted transition hover:border-danger/30 hover:text-danger disabled:opacity-50"
          >
            <Trash2 className="size-4" strokeWidth={1.8} />
            Delete
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
      setError("Repository name must start with a letter or number and may include _ . -");
      return;
    }
    if (!trimmedUrl) {
      setError("Repository URL is required");
      return;
    }
    if (!trimmedBranch) {
      setError("Default branch is required");
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
      setError(extractError(err, "Failed to register repository"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="grid gap-3 md:grid-cols-[1fr_2fr_1fr_1fr_1fr_auto]" onSubmit={handleSubmit}>
      <label className="space-y-1 text-sm text-muted">
        <span>Name</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setName(event.target.value)}
          placeholder="frontend"
          value={name}
        />
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>URL</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setUrl(event.target.value)}
          placeholder="git@github.com:org/repo.git"
          value={url}
        />
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>Default branch</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setDefaultBranch(event.target.value)}
          placeholder="main"
          value={defaultBranch}
        />
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>Role</span>
        <select
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setRole(event.target.value as RepoRole)}
          value={role}
        >
          {ROLES.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>SSH key path</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setSshKeyPath(event.target.value)}
          placeholder="~/.ssh/id_ed25519"
          value={sshKeyPath}
        />
      </label>
      <div className="flex items-end">
        <button
          className="w-full rounded-xl bg-accent px-4 py-3 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] transition hover:bg-accent-soft disabled:opacity-60 md:w-auto"
          disabled={submitting}
          type="submit"
        >
          {submitting ? "Registering..." : "Register"}
        </button>
      </div>
      {error ? <p className="text-xs text-danger md:col-span-6">{error}</p> : null}
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
      setActionError(extractError(err, "Fetch failed"));
    } finally {
      setPending(null);
    }
  }

  async function handleDelete(id: string) {
    if (typeof window !== "undefined" && !window.confirm(`Delete repository ${id}?`)) {
      return;
    }
    setPending({ kind: "delete", id });
    setActionError(null);
    try {
      await deleteRepo(id);
      await query.mutate();
    } catch (err) {
      setActionError(extractError(err, "Delete failed"));
    } finally {
      setPending(null);
    }
  }

  async function handleSync() {
    if (typeof window !== "undefined" && !window.confirm("Sync the repository registry from config/repos.yaml?")) {
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
      setActionError(extractError(err, "Sync failed"));
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
      <SectionPanel kicker="Register" title="Register repository">
        <CreateForm onCreated={() => void query.mutate()} />
      </SectionPanel>

      <SectionPanel
        kicker="Directory"
        title="Repository registry"
        actions={
          <button
            type="button"
            onClick={() => void handleSync()}
            className="inline-flex items-center gap-2 rounded-full border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs font-medium text-muted transition hover:border-accent/40 hover:text-accent"
          >
            <RefreshCw className="size-3.5" strokeWidth={1.8} />
            Sync config
          </button>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-3 xl:grid-cols-[1.2fr_1fr_1fr_auto]">
            <label className="space-y-1 text-sm text-muted">
              <span>Search</span>
              <input
                className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
                value={search}
                onChange={(event) => updateFilters({ search: event.target.value, offset: 0 })}
                placeholder="Search by name, URL, or branch"
              />
            </label>

            <label className="space-y-1 text-sm text-muted">
              <span>Role</span>
              <select
                className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
                value={role}
                onChange={(event) => updateFilters({ role: event.target.value as RepoRoleFilter, offset: 0 })}
              >
                <option value="all">All roles</option>
                {ROLES.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>

            <label className="space-y-1 text-sm text-muted">
              <span>Sort</span>
              <select
                className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
                value={sort}
                onChange={(event) => updateFilters({ sort: event.target.value as RepoSort, offset: 0 })}
              >
                <option value="updated_desc">Recently updated</option>
                <option value="last_fetched_desc">Recently fetched</option>
                <option value="name_asc">Name A-Z</option>
                <option value="name_desc">Name Z-A</option>
              </select>
            </label>

            <div className="space-y-1">
              <span className="text-sm text-muted">Health</span>
              <SegmentedControl
                ariaLabel="Repository fetch status"
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
                {query.data ? `${query.data.pagination.total} repositories matching the current filters` : "Loading repositories..."}
              </p>
            </div>
            {syncReport ? (
              <p className="text-xs text-muted">
                Last sync: in_sync {syncReport.in_sync} / fs_only {syncReport.fs_only} / db_only {syncReport.db_only}
              </p>
            ) : null}
          </div>

          {query.error ? (
            <div className="rounded-2xl border border-danger/15 bg-danger/8 p-5">
              <h3 className="font-serif text-lg font-medium leading-tight tracking-tight text-copy">Repository data failed to load</h3>
              <p className="mt-2 text-sm text-muted">Retry the request or narrow the current filters.</p>
              <button
                className="mt-4 rounded-xl bg-copy px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
                onClick={() => void query.mutate()}
                type="button"
              >
                Retry
              </button>
            </div>
          ) : !query.data ? (
            <LoadingSkeleton />
          ) : repos.length === 0 ? (
            <EmptyState copy="No repositories match the current filters." />
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
              itemLabel="Repositories"
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
