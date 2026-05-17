import type {
  CreateWorkspacePayload,
  WorkspaceAttachment,
  WorkspaceFilesEnvelope,
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

export interface ListWorkspaceFilesParams {
  kind?: string;
  query?: string;
  selectable?: boolean;
  limit?: number;
  offset?: number;
}

export async function listWorkspaceFiles(
  workspaceId: string,
  params: ListWorkspaceFilesParams = {},
): Promise<WorkspaceFilesEnvelope> {
  const query = { ...params };
  return apiFetch<WorkspaceFilesEnvelope>(
    `/workspaces/${encodeURIComponent(workspaceId)}/files`,
    { query },
  );
}

export async function uploadWorkspaceFile(
  workspaceId: string,
  file: File,
): Promise<WorkspaceAttachment> {
  const body = new FormData();
  body.append("file", file);
  return apiFetch<WorkspaceAttachment>(
    `/workspaces/${encodeURIComponent(workspaceId)}/files/upload`,
    { method: "POST", body },
  );
}

export async function deleteWorkspaceFile(
  workspaceId: string,
  relativePath: string,
): Promise<void> {
  await apiRequest<void>(`/workspaces/${encodeURIComponent(workspaceId)}/files`, {
    method: "DELETE",
    query: { relative_path: relativePath },
  });
}

export async function uploadWorkspaceAttachment(
  workspaceId: string,
  file: File,
): Promise<WorkspaceAttachment> {
  const body = new FormData();
  body.append("file", file);
  return apiFetch<WorkspaceAttachment>(
    `/workspaces/${encodeURIComponent(workspaceId)}/attachments`,
    { method: "POST", body },
  );
}
