import type { CreateDevWorkPayload, DevWork, DevWorkPage, DevWorkStep } from "../types";
import { apiFetch } from "./client";

export async function listDevWorks(workspaceId: string): Promise<DevWork[]> {
  return apiFetch<DevWork[]>("/dev-works", { query: { workspace_id: workspaceId } });
}

export interface ListDevWorkPageParams {
  workspace_id: string;
  step?: DevWorkStep;
  query?: string;
  sort?: "updated_desc" | "updated_asc" | "created_desc" | "created_asc";
  limit?: number;
  offset?: number;
}

export async function listDevWorkPage(
  params: ListDevWorkPageParams,
): Promise<DevWorkPage> {
  return apiFetch<DevWorkPage>("/dev-works", {
    query: {
      ...params,
      paginate: true,
    },
  });
}

export async function getDevWork(id: string): Promise<DevWork> {
  return apiFetch<DevWork>(`/dev-works/${encodeURIComponent(id)}`);
}

export async function createDevWork(payload: CreateDevWorkPayload): Promise<DevWork> {
  return apiFetch<DevWork>("/dev-works", { method: "POST", body: payload });
}

export async function tickDevWork(id: string): Promise<DevWork> {
  return apiFetch<DevWork>(`/dev-works/${encodeURIComponent(id)}/tick`, { method: "POST" });
}

export async function continueDevWork(id: string, additionalRounds: number): Promise<DevWork> {
  return apiFetch<DevWork>(`/dev-works/${encodeURIComponent(id)}/continue`, {
    method: "POST",
    body: { additional_rounds: additionalRounds },
  });
}

export async function cancelDevWork(id: string): Promise<void> {
  await apiFetch<void>(`/dev-works/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}

export async function pushDevWorkBranches(id: string): Promise<DevWork> {
  return apiFetch<DevWork>(`/dev-works/${encodeURIComponent(id)}/push`, {
    method: "POST",
  });
}
