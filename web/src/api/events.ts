import type { EventsIndexResponse } from "../types";
import { apiFetch } from "./client";

export interface ListEventsParams {
  runId?: string;
  level?: "debug" | "info" | "warning" | "error";
  spanType?: string;
  limit?: number;
  offset?: number;
}

export async function listEvents(params: ListEventsParams = {}): Promise<EventsIndexResponse> {
  return apiFetch<EventsIndexResponse>("/events", {
    query: {
      run_id: params.runId,
      level: params.level,
      span_type: params.spanType,
      limit: params.limit,
      offset: params.offset,
    },
  });
}
