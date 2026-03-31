import type { SWRConfiguration } from "swr";

export function usePolling(intervalMs = 15_000, enabled = true): SWRConfiguration {
  return {
    refreshInterval: enabled ? intervalMs : 0,
    revalidateIfStale: enabled,
    revalidateOnFocus: false,
    revalidateOnReconnect: true,
    keepPreviousData: true,
  };
}
