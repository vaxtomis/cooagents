import type { WorkspaceMetrics } from "../types";
import { apiFetch } from "./client";

export type GetMetricsParams = {
  since?: string;
  until?: string;
};

export async function getWorkspaceMetrics(
  params: GetMetricsParams = {},
): Promise<WorkspaceMetrics> {
  return apiFetch<WorkspaceMetrics>("/metrics/workspaces", {
    query: { since: params.since, until: params.until },
  });
}
