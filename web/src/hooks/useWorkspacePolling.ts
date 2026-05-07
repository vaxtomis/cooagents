import type { SWRConfiguration } from "swr";
import { usePolling } from "./usePolling";

// Single source of truth for the Workspace-centric polling interval.
// Phase 8 will replace polling with SSE — only this constant changes.
export const WORKSPACE_POLLING_INTERVAL_MS = 15_000;
export const WORKSPACE_ACTIVE_POLLING_INTERVAL_MS = 3_000;

/**
 * Returns an SWR configuration object preconfigured for Workspace polling.
 * Spread this into a `useSWR(...)` call as the third argument; it does not
 * trigger any fetching on its own.
 */
export function useWorkspacePolling(enabled = true): SWRConfiguration {
  return usePolling(WORKSPACE_POLLING_INTERVAL_MS, enabled);
}

export function useWorkspaceActivePolling(enabled = true): SWRConfiguration {
  return usePolling(WORKSPACE_ACTIVE_POLLING_INTERVAL_MS, enabled);
}

export function useWorkspaceDetailPolling<T>(
  isActive: (latestData: T | undefined) => boolean,
  enabled = true,
): SWRConfiguration<T> {
  const polling = usePolling(WORKSPACE_POLLING_INTERVAL_MS, enabled);
  return {
    ...polling,
    refreshInterval: enabled
      ? (latestData) =>
          isActive(latestData) ? WORKSPACE_ACTIVE_POLLING_INTERVAL_MS : WORKSPACE_POLLING_INTERVAL_MS
      : 0,
  };
}
