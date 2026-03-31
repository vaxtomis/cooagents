import type { RunTraceResponse } from "../types";
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
