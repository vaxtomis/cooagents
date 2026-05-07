import type { WorkspaceEventsEnvelope } from "../types";
import { apiFetch } from "./client";

export const WORKSPACE_EVENTS_MAX_LIMIT = 200;

export interface ListWorkspaceEventsParams {
  limit?: number;
  offset?: number;
  event_name?: string[]; // repeatable; server-side dedupes
  correlation_id?: string;
}

export async function listWorkspaceEvents(
  workspaceId: string,
  params: ListWorkspaceEventsParams = {},
): Promise<WorkspaceEventsEnvelope> {
  // Clamp limit client-side so callers cannot silently exceed the server cap.
  const clampedLimit =
    typeof params.limit === "number"
      ? Math.min(Math.max(params.limit, 1), WORKSPACE_EVENTS_MAX_LIMIT)
      : undefined;
  return apiFetch<WorkspaceEventsEnvelope>(
    `/workspaces/${encodeURIComponent(workspaceId)}/events`,
    {
      query: {
        limit: clampedLimit,
        offset: params.offset,
        event_name: params.event_name,
        correlation_id: params.correlation_id,
      },
    },
  );
}
