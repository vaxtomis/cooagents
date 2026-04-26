import type { Repo } from "../types";
import { StatusBadge } from "./StatusBadge";

interface Props {
  repo: Pick<Repo, "fetch_status" | "last_fetched_at" | "last_fetch_err">;
}

export function RepoFetchStatusBadge({ repo }: Props) {
  const tooltip =
    repo.fetch_status === "error" && repo.last_fetch_err
      ? repo.last_fetch_err
      : repo.last_fetched_at
        ? `最近 fetch: ${repo.last_fetched_at}`
        : undefined;
  return (
    <span title={tooltip}>
      <StatusBadge status={repo.fetch_status} />
    </span>
  );
}
