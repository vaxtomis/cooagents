import type { SWRConfiguration } from "swr";
import { usePolling } from "./usePolling";

// Single source of truth for the Workspace-centric polling interval.
// Phase 8 will replace polling with SSE — only this constant changes.
export const WORKSPACE_POLLING_INTERVAL_MS = 15_000;

/**
 * Returns an SWR configuration object preconfigured for Workspace polling.
 * Spread this into a `useSWR(...)` call as the third argument; it does not
 * trigger any fetching on its own.
 */
export function useWorkspacePolling(enabled = true): SWRConfiguration {
  return usePolling(WORKSPACE_POLLING_INTERVAL_MS, enabled);
}
