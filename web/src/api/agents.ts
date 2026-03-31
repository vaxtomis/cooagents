import type { AgentHost } from "../types";
import { apiFetch } from "./client";

function parseLabels(labelsJson: string | null | undefined): string[] {
  if (!labelsJson) {
    return [];
  }

  try {
    const parsed = JSON.parse(labelsJson);
    return Array.isArray(parsed) ? parsed.map((value) => String(value)) : [];
  } catch {
    return [];
  }
}

export async function listAgentHosts(): Promise<AgentHost[]> {
  const hosts = await apiFetch<Array<Omit<AgentHost, "labels"> & { labels_json?: string | null }>>("/agent-hosts");
  return hosts.map((host) => ({
    ...host,
    labels: parseLabels(host.labels_json),
  }));
}
