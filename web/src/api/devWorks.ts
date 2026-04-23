import type { CreateDevWorkPayload, DevWork } from "../types";
import { apiFetch } from "./client";

export async function listDevWorks(workspaceId: string): Promise<DevWork[]> {
  return apiFetch<DevWork[]>("/dev-works", { query: { workspace_id: workspaceId } });
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

export async function cancelDevWork(id: string): Promise<void> {
  await apiFetch<void>(`/dev-works/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}
