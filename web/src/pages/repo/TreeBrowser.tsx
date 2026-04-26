import useSWR from "swr";
import { ChevronUp, FileText, Folder } from "lucide-react";
import { repoTree } from "../../api/repos";
import { EmptyState } from "../../components/SectionPanel";
import { formatBytes } from "../../lib/formatBytes";
import type { RepoTree } from "../../types";

interface Props {
  repoId: string;
  gitRef: string;
  path: string;
  onPathChange: (path: string) => void;
  onSelectFile: (path: string) => void;
  selectedPath: string | null;
}

function displayName(fullPath: string, parent: string): string {
  return parent ? fullPath.slice(parent.length + 1) : fullPath;
}

function parentOf(path: string): string {
  if (!path) return "";
  const idx = path.lastIndexOf("/");
  return idx < 0 ? "" : path.slice(0, idx);
}

function Breadcrumb({
  path,
  onJump,
}: {
  path: string;
  onJump: (path: string) => void;
}) {
  const segments = path ? path.split("/") : [];
  return (
    <div className="flex flex-wrap items-center gap-1 font-mono text-xs text-muted">
      <button
        className="rounded-md border border-transparent px-2 py-1 transition hover:border-border hover:text-copy"
        onClick={() => onJump("")}
        type="button"
      >
        /
      </button>
      {segments.map((seg, idx) => {
        const sub = segments.slice(0, idx + 1).join("/");
        return (
          <span className="flex items-center gap-1" key={sub}>
            <span className="text-muted-soft">/</span>
            <button
              className="rounded-md border border-transparent px-2 py-1 transition hover:border-border hover:text-copy"
              onClick={() => onJump(sub)}
              type="button"
            >
              {seg}
            </button>
          </span>
        );
      })}
    </div>
  );
}

export function TreeBrowser({
  repoId,
  gitRef,
  path,
  onPathChange,
  onSelectFile,
  selectedPath,
}: Props) {
  const query = useSWR<RepoTree>(
    gitRef ? ["repo-tree", repoId, gitRef, path] : null,
    () => repoTree(repoId, { ref: gitRef, path, depth: 1 }),
  );

  function up() {
    onPathChange(parentOf(path));
  }

  return (
    <div className="space-y-3">
      <Breadcrumb path={path} onJump={onPathChange} />
      {query.error ? (
        <p className="rounded-2xl border border-danger/15 bg-danger/8 p-3 text-xs text-danger">
          目录加载失败：{String((query.error as Error).message ?? query.error)}
        </p>
      ) : !query.data ? (
        <ul className="space-y-1">
          {Array.from({ length: 5 }, (_, index) => (
            <li
              className="h-7 animate-pulse rounded-md bg-panel-strong/70"
              key={index}
            />
          ))}
        </ul>
      ) : query.data.entries.length === 0 ? (
        <EmptyState copy="目录为空。" />
      ) : (
        <ul className="space-y-1">
          {path !== "" && (
            <li>
              <button
                className="inline-flex w-full items-center gap-2 rounded-md px-2 py-1 text-xs text-muted transition hover:bg-panel-strong/60 hover:text-copy"
                onClick={up}
                type="button"
              >
                <ChevronUp className="size-3.5" strokeWidth={1.8} />
                ../
              </button>
            </li>
          )}
          {query.data.entries.map((entry) => {
            const name = displayName(entry.path, path);
            if (entry.type === "tree") {
              return (
                <li key={entry.path}>
                  <button
                    className="inline-flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs text-copy transition hover:bg-panel-strong/60"
                    onClick={() => onPathChange(entry.path)}
                    type="button"
                  >
                    <Folder
                      className="size-3.5 text-accent"
                      strokeWidth={1.8}
                    />
                    <span className="truncate font-mono">{name}/</span>
                  </button>
                </li>
              );
            }
            const active = selectedPath === entry.path;
            return (
              <li key={entry.path}>
                <button
                  className={[
                    "inline-flex w-full items-center justify-between gap-2 rounded-md px-2 py-1 text-left text-xs transition",
                    active
                      ? "bg-accent/15 text-copy"
                      : "text-muted hover:bg-panel-strong/60 hover:text-copy",
                  ].join(" ")}
                  onClick={() => onSelectFile(entry.path)}
                  type="button"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <FileText className="size-3.5" strokeWidth={1.8} />
                    <span className="truncate font-mono">{name}</span>
                  </span>
                  <span className="shrink-0 text-muted-soft">
                    {formatBytes(entry.size)}
                  </span>
                </button>
              </li>
            );
          })}
          {query.data.truncated && (
            <li className="px-2 py-1 text-[11px] text-warning">
              已截断（达到 entries 上限）
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
