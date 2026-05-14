import type {
  CreateDesignWorkPayload,
  DesignWork,
  DesignWorkPage,
  DesignWorkRetrySource,
  DesignWorkState,
  RetryDesignWorkPayload,
} from "../types";
import { apiFetch } from "./client";

export async function listDesignWorks(workspaceId: string): Promise<DesignWork[]> {
  return apiFetch<DesignWork[]>("/design-works", { query: { workspace_id: workspaceId } });
}

export interface ListDesignWorkPageParams {
  workspace_id: string;
  state?: DesignWorkState;
  query?: string;
  sort?: "updated_desc" | "updated_asc" | "created_desc" | "created_asc";
  limit?: number;
  offset?: number;
}

export async function listDesignWorkPage(
  params: ListDesignWorkPageParams,
): Promise<DesignWorkPage> {
  return apiFetch<DesignWorkPage>("/design-works", {
    query: {
      ...params,
      paginate: true,
    },
  });
}

export async function getDesignWork(id: string): Promise<DesignWork> {
  return apiFetch<DesignWork>(`/design-works/${encodeURIComponent(id)}`);
}

export async function createDesignWork(payload: CreateDesignWorkPayload): Promise<DesignWork> {
  return apiFetch<DesignWork>("/design-works", { method: "POST", body: payload });
}

export async function getDesignWorkRetrySource(id: string): Promise<DesignWorkRetrySource> {
  return apiFetch<DesignWorkRetrySource>(`/design-works/${encodeURIComponent(id)}/retry-source`);
}

export async function retryDesignWork(
  id: string,
  payload?: RetryDesignWorkPayload,
): Promise<DesignWork> {
  return apiFetch<DesignWork>(`/design-works/${encodeURIComponent(id)}/retry`, {
    method: "POST",
    body: payload,
  });
}

export async function tickDesignWork(id: string): Promise<DesignWork> {
  return apiFetch<DesignWork>(`/design-works/${encodeURIComponent(id)}/tick`, { method: "POST" });
}

export async function cancelDesignWork(id: string): Promise<void> {
  await apiFetch<void>(`/design-works/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}

export async function rerunDesignWork(id: string): Promise<DesignWork> {
  return apiFetch<DesignWork>(`/design-works/${encodeURIComponent(id)}/rerun`, {
    method: "POST",
  });
}

export async function deleteDesignWork(id: string): Promise<void> {
  await apiFetch<void>(`/design-works/${encodeURIComponent(id)}`, { method: "DELETE" });
}
