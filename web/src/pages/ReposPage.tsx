import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import useSWR from "swr";
import { Database, RefreshCw, Trash2 } from "lucide-react";
import {
  createRepo,
  deleteRepo,
  fetchRepo,
  listRepos,
  syncRepos,
} from "../api/repos";
import { EmptyState, SectionPanel } from "../components/SectionPanel";
import { RepoFetchStatusBadge } from "../components/RepoFetchStatusBadge";
import { useWorkspacePolling } from "../hooks/useWorkspacePolling";
import { extractError } from "../lib/extractError";
import type { CreateRepoPayload, Repo, RepoRole } from "../types";

const REPO_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$/;
const ROLES: RepoRole[] = [
  "backend",
  "frontend",
  "fullstack",
  "infra",
  "docs",
  "other",
];

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
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }, (_, index) => (
        <div
          className="h-36 animate-pulse rounded-2xl border border-border bg-panel-strong/70"
          key={index}
        />
      ))}
    </div>
  );
}

function ErrorBlock({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="rounded-2xl border border-danger/15 bg-danger/8 p-5">
      <h3 className="font-serif text-lg font-medium leading-tight tracking-tight text-copy">
        仓库注册表加载失败
      </h3>
      <p className="mt-2 text-sm text-muted">请重试。</p>
      <button
        className="mt-4 rounded-xl bg-copy px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
        onClick={onRetry}
        type="button"
      >
        重试
      </button>
    </div>
  );
}

function RepoCard({
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
    <article className="flex h-full flex-col rounded-2xl border border-border bg-panel-strong/80 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-accent/10 text-accent">
            <Database className="size-4" strokeWidth={1.8} />
          </div>
          <div className="min-w-0">
            <p className="truncate font-serif text-lg font-medium text-copy">
              {repo.name}
            </p>
            <p className="mt-1 truncate font-mono text-xs text-muted">
              {repo.id}
            </p>
          </div>
        </div>
        <RepoFetchStatusBadge repo={repo} />
      </div>
      <p className="mt-4 truncate font-mono text-xs text-muted">{repo.url}</p>
      <p className="mt-1 text-xs text-muted-soft">
        default_branch: {repo.default_branch} · role: {repo.role}
      </p>
      {repo.last_fetched_at ? (
        <p className="mt-1 text-xs text-muted-soft">
          last_fetched_at {repo.last_fetched_at}
        </p>
      ) : null}
      {repo.last_fetch_err ? (
        <p
          className="mt-2 line-clamp-2 text-xs text-danger"
          title={repo.last_fetch_err}
        >
          {repo.last_fetch_err}
        </p>
      ) : null}
      <div className="mt-auto flex flex-wrap gap-2 pt-4">
        <button
          className="flex-1 rounded-lg bg-copy px-3 py-2 text-xs font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
          onClick={() => onOpen(repo.id)}
          type="button"
        >
          打开
        </button>
        <button
          className="inline-flex items-center gap-1 rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-2 text-xs font-medium text-muted transition hover:border-accent/40 hover:text-accent disabled:opacity-50"
          disabled={fetching}
          onClick={() => onFetch(repo.id)}
          type="button"
        >
          <RefreshCw className="size-3.5" strokeWidth={1.8} />
          {fetching ? "fetching…" : "立即 fetch"}
        </button>
        <button
          className="inline-flex items-center gap-1 rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-2 text-xs font-medium text-muted transition hover:border-danger/30 hover:text-danger disabled:opacity-50"
          disabled={deleting}
          onClick={() => onDelete(repo.id)}
          type="button"
        >
          <Trash2 className="size-3.5" strokeWidth={1.8} />
          删除
        </button>
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
      setError(
        "name 必须以字母或数字开头，仅含字母数字与 _ . - （1-63 位）",
      );
      return;
    }
    if (!trimmedUrl) {
      setError("请填写仓库 URL");
      return;
    }
    if (!trimmedBranch) {
      setError("default_branch 不能为空");
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
      setError(extractError(err, "创建失败"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      className="grid gap-3 md:grid-cols-[1fr_2fr_1fr_1fr_1fr_auto]"
      onSubmit={handleSubmit}
    >
      <label className="space-y-1 text-sm text-muted">
        <span>name</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setName(event.target.value)}
          placeholder="frontend"
          value={name}
        />
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>url</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setUrl(event.target.value)}
          placeholder="git@github.com:org/repo.git"
          value={url}
        />
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>default_branch</span>
        <input
          className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 font-mono text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
          onChange={(event) => setDefaultBranch(event.target.value)}
          placeholder="main"
          value={defaultBranch}
        />
      </label>
      <label className="space-y-1 text-sm text-muted">
        <span>role</span>
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
        <span>ssh_key_path（可选）</span>
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
          {submitting ? "注册中..." : "注册"}
        </button>
      </div>
      {error ? (
        <p className="text-xs text-danger md:col-span-6">{error}</p>
      ) : null}
    </form>
  );
}

export function ReposPage() {
  const polling = useWorkspacePolling();
  const navigate = useNavigate();
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [syncReport, setSyncReport] = useState<SyncSummary | null>(null);

  const query = useSWR(["repos"], listRepos, polling);
  const repos = query.data ?? [];

  async function handleFetch(id: string) {
    setPending({ kind: "fetch", id });
    setActionError(null);
    try {
      await fetchRepo(id);
      await query.mutate();
    } catch (err) {
      setActionError(extractError(err, "fetch 失败"));
    } finally {
      setPending(null);
    }
  }

  async function handleDelete(id: string) {
    if (
      typeof window !== "undefined" &&
      !window.confirm(`确认删除仓库 ${id}？仍被 DevWork 引用时会被拒绝。`)
    ) {
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
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        "从 config/repos.yaml 同步注册表？这会基于 YAML 增删 DB 中的行。",
      )
    ) {
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

  return (
    <div className="space-y-6">
      <SectionPanel kicker="新建" title="注册仓库">
        <CreateForm onCreated={() => void query.mutate()} />
      </SectionPanel>

      <SectionPanel
        actions={
          <button
            className="inline-flex items-center gap-1 rounded-full border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs font-medium text-muted transition hover:border-accent/40 hover:text-accent"
            onClick={() => void handleSync()}
            type="button"
          >
            <RefreshCw className="size-3.5" strokeWidth={1.8} />
            同步配置
          </button>
        }
        kicker="清单"
        title="仓库注册表"
      >
        {syncReport ? (
          <p className="mb-3 text-xs text-muted">
            已同步 — in_sync: {syncReport.in_sync} · fs_only: {syncReport.fs_only} · db_only: {syncReport.db_only}
          </p>
        ) : null}
        {query.error ? (
          <ErrorBlock onRetry={() => void query.mutate()} />
        ) : !query.data ? (
          <LoadingSkeleton />
        ) : repos.length === 0 ? (
          <EmptyState copy="未注册任何仓库。在 config/repos.yaml 声明并点击同步配置，或使用上方的注册表单。" />
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {repos.map((repo) => (
              <RepoCard
                deleting={
                  pending?.kind === "delete" && pending.id === repo.id
                }
                fetching={
                  pending?.kind === "fetch" && pending.id === repo.id
                }
                key={repo.id}
                onDelete={handleDelete}
                onFetch={handleFetch}
                onOpen={(id) => navigate(`/repos/${id}`)}
                repo={repo}
              />
            ))}
          </div>
        )}
        {actionError ? (
          <p className="mt-3 text-xs text-danger">{actionError}</p>
        ) : null}
      </SectionPanel>
    </div>
  );
}
