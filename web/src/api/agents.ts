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

export interface AgentHostPayload {
  id: string;
  host: string;
  agent_type: string;
  max_concurrent: number;
  ssh_key?: string | null;
  labels?: string[];
}

export interface UpdateAgentHostPayload {
  host?: string;
  agent_type?: string;
  max_concurrent?: number;
  ssh_key?: string | null;
  labels?: string[];
}

export interface HostCheckResponse {
  host_id: string;
  online: boolean;
}

export async function createAgentHost(payload: AgentHostPayload): Promise<AgentHost> {
  const host = await apiFetch<Omit<AgentHost, "labels"> & { labels_json?: string | null }>("/agent-hosts", {
    body: payload,
    method: "POST",
  });
  return {
    ...host,
    labels: parseLabels(host.labels_json),
  };
}

export async function updateAgentHost(hostId: string, payload: UpdateAgentHostPayload): Promise<AgentHost> {
  const host = await apiFetch<Omit<AgentHost, "labels"> & { labels_json?: string | null }>(`/agent-hosts/${hostId}`, {
    body: payload,
    method: "PUT",
  });
  return {
    ...host,
    labels: parseLabels(host.labels_json),
  };
}

export async function deleteAgentHost(hostId: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/agent-hosts/${hostId}`, {
    method: "DELETE",
  });
}

export async function checkAgentHost(hostId: string): Promise<HostCheckResponse> {
  return apiFetch<HostCheckResponse>(`/agent-hosts/${hostId}/check`, {
    method: "POST",
  });
}
