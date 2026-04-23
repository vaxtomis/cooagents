import type { CreateDesignWorkPayload, DesignWork } from "../types";
import { apiFetch } from "./client";

export async function listDesignWorks(workspaceId: string): Promise<DesignWork[]> {
  return apiFetch<DesignWork[]>("/design-works", { query: { workspace_id: workspaceId } });
}

export async function getDesignWork(id: string): Promise<DesignWork> {
  return apiFetch<DesignWork>(`/design-works/${encodeURIComponent(id)}`);
}

export async function createDesignWork(payload: CreateDesignWorkPayload): Promise<DesignWork> {
  return apiFetch<DesignWork>("/design-works", { method: "POST", body: payload });
}

export async function tickDesignWork(id: string): Promise<DesignWork> {
  return apiFetch<DesignWork>(`/design-works/${encodeURIComponent(id)}/tick`, { method: "POST" });
}

export async function cancelDesignWork(id: string): Promise<void> {
  await apiFetch<void>(`/design-works/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}
