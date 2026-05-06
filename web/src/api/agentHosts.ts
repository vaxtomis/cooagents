import type {
  AgentHost,
  AgentHostsSyncReport,
  CreateAgentHostPayload,
  UpdateAgentHostPayload,
} from "../types";
import { apiFetch, apiRequest } from "./client";

export async function listAgentHosts(): Promise<AgentHost[]> {
  return apiFetch<AgentHost[]>("/agent-hosts");
}

export async function getAgentHost(id: string): Promise<AgentHost> {
  return apiFetch<AgentHost>(`/agent-hosts/${encodeURIComponent(id)}`);
}

export async function createAgentHost(payload: CreateAgentHostPayload): Promise<AgentHost> {
  return apiFetch<AgentHost>("/agent-hosts", {
    method: "POST",
    body: payload,
  });
}

export async function updateAgentHost(
  id: string,
  payload: UpdateAgentHostPayload,
): Promise<AgentHost> {
  return apiFetch<AgentHost>(`/agent-hosts/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: payload,
  });
}

export async function deleteAgentHost(id: string): Promise<void> {
  await apiRequest<void>(`/agent-hosts/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function healthcheckAgentHost(id: string): Promise<AgentHost> {
  return apiFetch<AgentHost>(`/agent-hosts/${encodeURIComponent(id)}/healthcheck`, {
    method: "POST",
  });
}

export async function syncAgentHosts(): Promise<AgentHostsSyncReport> {
  return apiFetch<AgentHostsSyncReport>("/agent-hosts/sync", {
    method: "POST",
  });
}
