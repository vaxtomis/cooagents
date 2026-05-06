import type {
  CreateWorkspacePayload,
  WorkspacePage,
  Workspace,
  WorkspaceStatus,
  WorkspaceSyncReport,
} from "../types";
import { apiFetch, apiRequest } from "./client";

export async function listWorkspaces(status?: WorkspaceStatus): Promise<Workspace[]> {
  return apiFetch<Workspace[]>("/workspaces", { query: { status } });
}

export interface ListWorkspacePageParams {
  status?: WorkspaceStatus;
  query?: string;
  sort?: "updated_desc" | "updated_asc" | "title_asc" | "title_desc" | "created_desc" | "created_asc";
  limit?: number;
  offset?: number;
}

export async function listWorkspacePage(
  params: ListWorkspacePageParams = {},
): Promise<WorkspacePage> {
  return apiFetch<WorkspacePage>("/workspaces", {
    query: {
      ...params,
      paginate: true,
    },
  });
}

export async function getWorkspace(id: string): Promise<Workspace> {
  return apiFetch<Workspace>(`/workspaces/${encodeURIComponent(id)}`);
}

export async function createWorkspace(payload: CreateWorkspacePayload): Promise<Workspace> {
  return apiFetch<Workspace>("/workspaces", { method: "POST", body: payload });
}

export async function archiveWorkspace(id: string): Promise<void> {
  await apiRequest<void>(`/workspaces/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function syncWorkspaces(): Promise<WorkspaceSyncReport> {
  return apiFetch<WorkspaceSyncReport>("/workspaces/sync", { method: "POST" });
}
