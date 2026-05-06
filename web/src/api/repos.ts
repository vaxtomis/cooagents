import type {
  CreateRepoPayload,
  FetchRepoResponse,
  Repo,
  RepoBlob,
  RepoBranches,
  RepoLog,
  RepoLogPage,
  RepoPage,
  RepoTree,
  ReposSyncReport,
  RepoFetchStatus,
  RepoRole,
  UpdateRepoPayload,
} from "../types";
import { apiFetch, apiRequest } from "./client";

export async function listRepos(): Promise<Repo[]> {
  return apiFetch<Repo[]>("/repos");
}

export interface ListRepoPageParams {
  role?: RepoRole;
  fetch_status?: RepoFetchStatus;
  query?: string;
  sort?: "name_asc" | "name_desc" | "updated_desc" | "updated_asc" | "last_fetched_desc" | "last_fetched_asc";
  limit?: number;
  offset?: number;
}

export async function listRepoPage(
  params: ListRepoPageParams = {},
): Promise<RepoPage> {
  return apiFetch<RepoPage>("/repos", {
    query: {
      ...params,
      paginate: true,
    },
  });
}

export async function getRepo(id: string): Promise<Repo> {
  return apiFetch<Repo>(`/repos/${encodeURIComponent(id)}`);
}

export async function createRepo(payload: CreateRepoPayload): Promise<Repo> {
  return apiFetch<Repo>("/repos", { method: "POST", body: payload });
}

export async function updateRepo(
  id: string,
  payload: UpdateRepoPayload,
): Promise<Repo> {
  return apiFetch<Repo>(`/repos/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: payload,
  });
}

export async function deleteRepo(id: string): Promise<void> {
  await apiRequest<void>(`/repos/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function syncRepos(): Promise<ReposSyncReport> {
  return apiFetch<ReposSyncReport>("/repos/sync", { method: "POST" });
}

export async function fetchRepo(id: string): Promise<FetchRepoResponse> {
  return apiFetch<FetchRepoResponse>(
    `/repos/${encodeURIComponent(id)}/fetch`,
    { method: "POST" },
  );
}

export async function repoBranches(id: string): Promise<RepoBranches> {
  return apiFetch<RepoBranches>(`/repos/${encodeURIComponent(id)}/branches`);
}

export async function repoTree(
  id: string,
  params: { ref: string; path?: string; depth?: number; max_entries?: number },
): Promise<RepoTree> {
  return apiFetch<RepoTree>(`/repos/${encodeURIComponent(id)}/tree`, {
    query: params,
  });
}

export async function repoBlob(
  id: string,
  params: { ref: string; path: string },
): Promise<RepoBlob> {
  return apiFetch<RepoBlob>(`/repos/${encodeURIComponent(id)}/blob`, {
    query: params,
  });
}

export async function repoLog(
  id: string,
  params: { ref: string; path?: string; limit?: number; offset?: number },
): Promise<RepoLog> {
  return apiFetch<RepoLog>(`/repos/${encodeURIComponent(id)}/log`, {
    query: params,
  });
}

export async function repoLogPage(
  id: string,
  params: { ref: string; path?: string; limit?: number; offset?: number },
): Promise<RepoLogPage> {
  return apiFetch<RepoLogPage>(`/repos/${encodeURIComponent(id)}/log`, {
    query: {
      ...params,
      paginate: true,
    },
  });
}
