import type { JobDiagnosisResponse, RunTraceResponse, TraceLookupResponse } from "../types";
import { apiFetch } from "./client";

export interface RunTraceParams {
  level?: "debug" | "info" | "warning" | "error";
  spanType?: string;
  limit?: number;
  offset?: number;
}

export async function getRunTrace(runId: string, params: RunTraceParams = {}): Promise<RunTraceResponse> {
  return apiFetch<RunTraceResponse>(`/runs/${runId}/trace`, {
    query: {
      level: params.level,
      span_type: params.spanType,
      limit: params.limit,
      offset: params.offset,
    },
  });
}

export async function getJobDiagnosis(jobId: string): Promise<JobDiagnosisResponse> {
  return apiFetch<JobDiagnosisResponse>(`/jobs/${jobId}/diagnosis`);
}

export async function getTraceLookup(
  traceId: string,
  level: "debug" | "info" | "warning" | "error" = "info",
): Promise<TraceLookupResponse> {
  return apiFetch<TraceLookupResponse>(`/traces/${traceId}`, {
    query: { level },
  });
}
