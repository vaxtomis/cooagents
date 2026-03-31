import type {
  ApprovePayload,
  ArtifactContentResponse,
  ArtifactDiffResponse,
  ArtifactRecord,
  JobOutputResponse,
  JobRecord,
  RejectPayload,
  RunBrief,
  RunRecord,
  RunsListResponse,
} from "../types";
import { apiFetch, apiPath, apiRequest } from "./client";

export interface ListRunsParams {
  status?: string;
  ticket?: string;
  currentStage?: string;
  sortBy?: string;
  sortOrder?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

export async function listRuns(params: ListRunsParams = {}): Promise<RunsListResponse> {
  const query = {
    status: params.status,
    ticket: params.ticket,
    current_stage: params.currentStage,
    sort_by: params.sortBy,
    sort_order: params.sortOrder,
    limit: params.limit,
    offset: params.offset,
  };
  const { data, response } = await apiRequest<RunRecord[]>("/runs", { query });
  const totalCount = Number(response.headers.get("X-Total-Count") ?? data.length);

  return {
    items: data,
    total: Number.isNaN(totalCount) ? data.length : totalCount,
    limit: params.limit ?? data.length,
    offset: params.offset ?? 0,
  };
}

export async function getRun(runId: string): Promise<RunRecord> {
  return apiFetch<RunRecord>(`/runs/${runId}`);
}

export async function getRunBrief(runId: string): Promise<RunBrief> {
  return apiFetch<RunBrief>(`/runs/${runId}/brief`);
}

export async function approveRun(runId: string, payload: ApprovePayload): Promise<RunRecord> {
  return apiFetch<RunRecord>(`/runs/${runId}/approve`, { body: payload, method: "POST" });
}

export async function rejectRun(runId: string, payload: RejectPayload): Promise<RunRecord> {
  return apiFetch<RunRecord>(`/runs/${runId}/reject`, { body: payload, method: "POST" });
}

export async function cancelRun(runId: string, cleanup = false): Promise<{ ok?: boolean; status?: string }> {
  return apiFetch<{ ok?: boolean; status?: string }>(`/runs/${runId}`, {
    method: "DELETE",
    query: { cleanup },
  });
}

export async function listArtifacts(runId: string, filters: { kind?: string; status?: string } = {}): Promise<ArtifactRecord[]> {
  return apiFetch<ArtifactRecord[]>(`/runs/${runId}/artifacts`, { query: filters });
}

export async function getArtifactContent(runId: string, artifactId: number): Promise<ArtifactContentResponse> {
  return apiFetch<ArtifactContentResponse>(`/runs/${runId}/artifacts/${artifactId}/content`);
}

export async function getArtifactDiff(runId: string, artifactId: number): Promise<ArtifactDiffResponse> {
  return apiFetch<ArtifactDiffResponse>(`/runs/${runId}/artifacts/${artifactId}/diff`);
}

export async function listJobs(runId: string): Promise<JobRecord[]> {
  return apiFetch<JobRecord[]>(`/runs/${runId}/jobs`);
}

export async function getJobOutput(runId: string, jobId: string): Promise<JobOutputResponse> {
  return apiFetch<JobOutputResponse>(`/runs/${runId}/jobs/${jobId}/output`);
}

export function getRunEventsStreamUrl(runId: string): string {
  return apiPath(`/runs/${runId}/events/stream`);
}
