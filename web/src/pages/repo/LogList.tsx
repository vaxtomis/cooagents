import { GitCommit } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { repoLogPage } from "../../api/repos";
import { PaginationControls } from "../../components/PaginationControls";
import { EmptyState } from "../../components/SectionPanel";
import type { RepoLogPage } from "../../types";

interface Props {
  repoId: string;
  gitRef: string;
}

const LOG_LIMIT = 20;

export function LogList({ repoId, gitRef }: Props) {
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    setOffset(0);
  }, [gitRef]);

  const query = useSWR<RepoLogPage>(
    gitRef ? ["repo-log-page", repoId, gitRef, offset] : null,
    () => repoLogPage(repoId, { ref: gitRef, limit: LOG_LIMIT, offset }),
  );

  if (query.error) {
    return (
      <p className="rounded-2xl border border-danger/15 bg-danger/8 p-3 text-xs text-danger">
        Commit log failed to load: {String((query.error as Error).message ?? query.error)}
      </p>
    );
  }

  if (!query.data) {
    return (
      <ul className="space-y-1">
        {Array.from({ length: 6 }, (_, index) => (
          <li key={index} className="h-9 animate-pulse rounded-md bg-panel-strong/70" />
        ))}
      </ul>
    );
  }

  if (query.data.items.length === 0) {
    return <EmptyState copy="No commits were found for the selected ref." />;
  }

  return (
    <div className="space-y-3">
      <ul className="space-y-2">
        {query.data.items.map((entry) => (
          <li
            key={entry.sha}
            className="flex flex-wrap items-baseline gap-3 rounded-md border border-border bg-panel-strong/50 px-3 py-2 text-xs text-copy"
          >
            <GitCommit className="size-3.5 shrink-0 text-accent" strokeWidth={1.8} />
            <span className="font-mono text-muted-soft">{entry.sha.slice(0, 12)}</span>
            <span className="min-w-0 flex-1 truncate">{entry.subject}</span>
            <span className="text-muted-soft">
              {entry.author} / {entry.committed_at}
            </span>
          </li>
        ))}
      </ul>

      <PaginationControls
        pagination={query.data.pagination}
        itemLabel="Commits"
        onPageChange={setOffset}
        disabled={query.isLoading}
      />
    </div>
  );
}
