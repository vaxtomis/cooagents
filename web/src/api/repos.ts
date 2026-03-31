import type { MergeQueueItem } from "../types";
import { apiFetch } from "./client";

function parseConflictFiles(value: string | null | undefined): string[] {
  if (!value) {
    return [];
  }

  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map((entry) => String(entry)) : [];
  } catch {
    return [];
  }
}

export async function listMergeQueue(): Promise<MergeQueueItem[]> {
  const items = await apiFetch<Array<Omit<MergeQueueItem, "conflict_files"> & { conflict_files_json?: string | null }>>("/repos/merge-queue");
  return items.map((item) => ({
    ...item,
    conflict_files: parseConflictFiles(item.conflict_files_json),
  }));
}

export async function mergeRun(runId: string, priority = 0): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/runs/${runId}/merge`, {
    body: { priority },
    method: "POST",
  });
}

export async function skipMergeRun(runId: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/runs/${runId}/merge-skip`, {
    method: "POST",
  });
}
