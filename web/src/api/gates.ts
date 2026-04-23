import type { GateInfo, GateStatus } from "../types";
import { apiFetch } from "./client";

// gate_id contains colons (e.g. "dev:abc123:exit") — encodeURIComponent is
// required to preserve them through the URL path.
export async function getGate(gateId: string): Promise<GateInfo> {
  return apiFetch<GateInfo>(`/gates/${encodeURIComponent(gateId)}`);
}

export async function actOnGate(
  gateId: string,
  action: "approve" | "reject",
  payload: { note?: string } = {},
): Promise<{ gate_id: string; status: GateStatus; actor: string }> {
  return apiFetch<{ gate_id: string; status: GateStatus; actor: string }>(
    `/gates/${encodeURIComponent(gateId)}/${encodeURIComponent(action)}`,
    { method: "POST", body: payload },
  );
}
