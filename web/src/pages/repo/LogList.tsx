import useSWR from "swr";
import { GitCommit } from "lucide-react";
import { repoLog } from "../../api/repos";
import { EmptyState } from "../../components/SectionPanel";
import type { RepoLog } from "../../types";

interface Props {
  repoId: string;
  gitRef: string;
}

const LOG_LIMIT = 50;

export function LogList({ repoId, gitRef }: Props) {
  const query = useSWR<RepoLog>(
    gitRef ? ["repo-log", repoId, gitRef] : null,
    () => repoLog(repoId, { ref: gitRef, limit: LOG_LIMIT }),
  );

  if (query.error) {
    return (
      <p className="rounded-2xl border border-danger/15 bg-danger/8 p-3 text-xs text-danger">
        提交日志加载失败：
        {String((query.error as Error).message ?? query.error)}
      </p>
    );
  }
  if (!query.data) {
    return (
      <ul className="space-y-1">
        {Array.from({ length: 6 }, (_, index) => (
          <li
            className="h-9 animate-pulse rounded-md bg-panel-strong/70"
            key={index}
          />
        ))}
      </ul>
    );
  }
  if (query.data.entries.length === 0) {
    return <EmptyState copy="该 ref 下无提交。" />;
  }
  return (
    <ul className="space-y-2">
      {query.data.entries.map((entry) => (
        <li
          className="flex flex-wrap items-baseline gap-3 rounded-md border border-border bg-panel-strong/50 px-3 py-2 text-xs text-copy"
          key={entry.sha}
        >
          <GitCommit className="size-3.5 shrink-0 text-accent" strokeWidth={1.8} />
          <span className="font-mono text-muted-soft">
            {entry.sha.slice(0, 12)}
          </span>
          <span className="min-w-0 flex-1 truncate">{entry.subject}</span>
          <span className="text-muted-soft">
            {entry.author} · {entry.committed_at}
          </span>
        </li>
      ))}
      {query.data.entries.length === LOG_LIMIT && (
        <li className="px-2 py-1 text-[11px] text-muted-soft">
          仅显示最近 {LOG_LIMIT} 条。
        </li>
      )}
    </ul>
  );
}
