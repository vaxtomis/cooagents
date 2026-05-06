import { EmptyState } from "./SectionPanel";
import { StatusBadge } from "./StatusBadge";
import type { WorkerRepoHandoff } from "../types";

interface Props {
  repos: WorkerRepoHandoff[];
}

function shortSha(sha: string | null): string {
  return sha ? sha.slice(0, 8) : "";
}

export function RepoPushStatusGrid({ repos }: Props) {
  if (repos.length === 0) {
    return <EmptyState copy="此 DevWork 未绑定任何仓库。" />;
  }
  return (
    <ul className="space-y-2">
      {repos.map((r) => (
        <li
          className="rounded-2xl border border-border bg-panel-strong/80 p-4"
          key={r.mount_name}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-copy">
              <span className="font-medium">{r.mount_name}</span>
              {r.is_primary ? (
                <span className="ml-2 text-[11px] text-muted">主仓库</span>
              ) : null}
            </p>
            <span title={r.push_err ?? undefined}>
              <StatusBadge status={r.push_state} />
            </span>
          </div>
          <p className="mt-1 truncate font-mono text-[11px] text-muted">
            {r.repo_id} → {r.devwork_branch}
          </p>
          <p className="mt-1 text-[11px] text-muted-soft">
            基准：{r.base_branch}
            {r.base_rev ? `@${shortSha(r.base_rev)}` : ""}
          </p>
          {r.push_err ? (
            <p
              className="mt-2 line-clamp-2 text-xs text-danger"
              title={r.push_err}
            >
              {r.push_err}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
