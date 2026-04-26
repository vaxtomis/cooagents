import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import useSWR from "swr";
import { fetchRepo, getRepo, repoBranches } from "../api/repos";
import { EmptyState, SectionPanel } from "../components/SectionPanel";
import { RepoFetchStatusBadge } from "../components/RepoFetchStatusBadge";
import { extractError } from "../lib/extractError";
import type { Repo, RepoBranches } from "../types";
import { BlobViewer } from "./repo/BlobViewer";
import { BranchPicker } from "./repo/BranchPicker";
import { LogList } from "./repo/LogList";
import { TreeBrowser } from "./repo/TreeBrowser";

const TAB_IDS = ["branches", "tree", "log"] as const;
type TabId = (typeof TAB_IDS)[number];

const TAB_LABELS: Record<TabId, string> = {
  branches: "Branches",
  tree: "Tree",
  log: "Log",
};

function TabSwitch({
  tab,
  setTab,
}: {
  tab: TabId;
  setTab: (next: TabId) => void;
}) {
  return (
    <div className="flex gap-2" role="tablist" aria-label="详情视图切换">
      {TAB_IDS.map((id) => {
        const selected = tab === id;
        return (
          <button
            aria-selected={selected}
            className={[
              "rounded-full border px-3 py-1.5 text-xs font-medium transition",
              selected
                ? "border-accent/30 bg-accent/15 text-copy"
                : "border-border-strong bg-panel-strong/50 text-muted hover:border-copy/20 hover:text-copy",
            ].join(" ")}
            key={id}
            onClick={() => setTab(id)}
            role="tab"
            tabIndex={selected ? 0 : -1}
            type="button"
          >
            {TAB_LABELS[id]}
          </button>
        );
      })}
    </div>
  );
}

function Header({ repo }: { repo: Repo | undefined }) {
  if (!repo) return <p className="text-sm text-muted">加载中…</p>;
  return (
    <div className="flex flex-wrap items-center gap-3">
      <RepoFetchStatusBadge repo={repo} />
      <p className="font-mono text-xs text-muted">
        url: <span className="text-copy">{repo.url}</span>
      </p>
      <p className="font-mono text-xs text-muted">
        default_branch: <span className="text-copy">{repo.default_branch}</span>
      </p>
      <p className="font-mono text-xs text-muted">
        role: <span className="text-copy">{repo.role}</span>
      </p>
      {repo.last_fetched_at ? (
        <p className="font-mono text-xs text-muted">
          last_fetched_at:{" "}
          <span className="text-copy">{repo.last_fetched_at}</span>
        </p>
      ) : null}
    </div>
  );
}

function BranchesList({ branches }: { branches: RepoBranches | undefined }) {
  if (!branches) return <p className="text-xs text-muted">加载中…</p>;
  if (branches.branches.length === 0) {
    return <EmptyState copy="未发现任何分支。" />;
  }
  return (
    <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
      {branches.branches.map((branch) => (
        <li
          className={[
            "flex items-center justify-between gap-2 rounded-md border bg-panel-strong/40 px-3 py-2 font-mono text-xs",
            branch === branches.default_branch
              ? "border-accent/40 text-copy"
              : "border-border text-muted",
          ].join(" ")}
          key={branch}
        >
          <span className="truncate">{branch}</span>
          {branch === branches.default_branch ? (
            <span className="text-[10px] uppercase tracking-[0.2em] text-accent">
              default
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function RepoDetailPage() {
  const { repoId } = useParams<{ repoId: string }>();
  if (!repoId) {
    return (
      <SectionPanel kicker="错误" title="缺少 repo id">
        <p className="text-sm text-muted">URL 缺少 repoId 参数。</p>
      </SectionPanel>
    );
  }
  return <RepoDetailContent repoId={repoId} />;
}

function RepoDetailContent({ repoId }: { repoId: string }) {
  const repoQuery = useSWR(["repo", repoId], () => getRepo(repoId));
  const branchesQuery = useSWR(["repo-branches", repoId], () =>
    repoBranches(repoId),
  );

  const [tab, setTab] = useState<TabId>("tree");
  const [gitRef, setGitRef] = useState<string>("");
  const [path, setPath] = useState<string>("");
  const [filePath, setFilePath] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Initialise ref from default_branch once data arrives.
  useEffect(() => {
    if (!gitRef && repoQuery.data?.default_branch) {
      setGitRef(repoQuery.data.default_branch);
    }
  }, [gitRef, repoQuery.data]);

  // Reset path / selected file whenever the ref changes — paths in another
  // ref may not exist anymore.
  useEffect(() => {
    setPath("");
    setFilePath(null);
  }, [gitRef]);

  async function handleFetch() {
    setFetching(true);
    setActionError(null);
    try {
      await fetchRepo(repoId);
      await Promise.all([repoQuery.mutate(), branchesQuery.mutate()]);
    } catch (err) {
      setActionError(extractError(err, "fetch 失败"));
    } finally {
      setFetching(false);
    }
  }

  return (
    <div className="space-y-6">
      <SectionPanel
        actions={
          <button
            className="inline-flex items-center gap-1 rounded-full border border-border-strong bg-panel-strong/50 px-3 py-1.5 text-xs font-medium text-muted transition hover:border-accent/40 hover:text-accent disabled:opacity-50"
            disabled={fetching}
            onClick={() => void handleFetch()}
            type="button"
          >
            {fetching ? "fetching…" : "立即 fetch"}
          </button>
        }
        kicker="仓库详情"
        title={repoQuery.data?.name ?? repoId}
      >
        <Header repo={repoQuery.data} />
        {actionError ? (
          <p className="mt-3 text-xs text-danger">{actionError}</p>
        ) : null}
        {repoQuery.error ? (
          <p className="mt-3 text-xs text-danger">
            仓库加载失败：
            {String((repoQuery.error as Error).message ?? repoQuery.error)}
          </p>
        ) : null}
      </SectionPanel>

      <SectionPanel
        actions={<TabSwitch setTab={setTab} tab={tab} />}
        kicker="侦察"
        title="branches / tree / log"
      >
        <div className="space-y-4">
          <BranchPicker
            branches={branchesQuery.data}
            onChange={setGitRef}
            value={gitRef}
          />

          {tab === "branches" && <BranchesList branches={branchesQuery.data} />}

          {tab === "tree" && (
            <div className="grid gap-4 md:grid-cols-[280px_1fr]">
              <TreeBrowser
                gitRef={gitRef}
                onPathChange={setPath}
                onSelectFile={setFilePath}
                path={path}
                repoId={repoId}
                selectedPath={filePath}
              />
              <BlobViewer gitRef={gitRef} path={filePath} repoId={repoId} />
            </div>
          )}

          {tab === "log" && <LogList gitRef={gitRef} repoId={repoId} />}
        </div>
      </SectionPanel>
    </div>
  );
}
