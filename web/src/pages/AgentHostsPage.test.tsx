import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentHostsPage } from "./AgentHostsPage";
import {
  checkAgentHost,
  createAgentHost,
  deleteAgentHost,
  listAgentHosts,
  updateAgentHost,
} from "../api/agents";

vi.mock("../api/agents", () => ({
  checkAgentHost: vi.fn(),
  createAgentHost: vi.fn(),
  deleteAgentHost: vi.fn(),
  listAgentHosts: vi.fn(),
  updateAgentHost: vi.fn(),
}));

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  render(
    <SWRConfig value={{ dedupingInterval: 0, provider: () => new Map(), revalidateOnFocus: false }}>
      <AgentHostsPage />
    </SWRConfig>,
  );
}

describe("AgentHostsPage", () => {
  it("loads hosts and supports check, edit, create, and delete actions", async () => {
    const now = new Date().toISOString();
    let hostsState = [
      {
        agent_type: "both",
        created_at: now,
        current_load: 1,
        host: "local",
        id: "local-box",
        labels: ["runner", "west"],
        labels_json: "[\"runner\",\"west\"]",
        max_concurrent: 2,
        ssh_key: null,
        status: "active",
        updated_at: now,
      },
      {
        agent_type: "codex",
        created_at: now,
        current_load: 0,
        host: "10.0.0.8",
        id: "queue-box",
        labels: ["east"],
        labels_json: "[\"east\"]",
        max_concurrent: 3,
        ssh_key: null,
        status: "active",
        updated_at: now,
      },
    ];

    vi.mocked(listAgentHosts).mockImplementation(async () => hostsState);
    vi.mocked(checkAgentHost).mockImplementation(async (hostId) => {
      hostsState = hostsState.map((host) => (host.id === hostId ? { ...host, status: "offline" } : host));
      return { host_id: hostId, online: false };
    });
    vi.mocked(updateAgentHost).mockImplementation(async (hostId, payload) => {
      hostsState = hostsState.map((host) =>
        host.id === hostId
          ? {
              ...host,
              agent_type: payload.agent_type ?? host.agent_type,
              host: payload.host ?? host.host,
              labels: payload.labels ?? host.labels,
              labels_json: payload.labels ? JSON.stringify(payload.labels) : host.labels_json,
              max_concurrent: payload.max_concurrent ?? host.max_concurrent,
              ssh_key: payload.ssh_key ?? host.ssh_key,
            }
          : host,
      );
      return hostsState.find((host) => host.id === hostId)!;
    });
    vi.mocked(createAgentHost).mockImplementation(async (payload) => {
      const created = {
        ...payload,
        created_at: now,
        current_load: 0,
        labels: payload.labels ?? [],
        labels_json: JSON.stringify(payload.labels ?? []),
        ssh_key: payload.ssh_key ?? null,
        status: "active",
        updated_at: now,
      };
      hostsState = [...hostsState, created];
      return created;
    });
    vi.mocked(deleteAgentHost).mockImplementation(async (hostId) => {
      hostsState = hostsState.filter((host) => host.id !== hostId);
      return { ok: true };
    });

    renderPage();

    expect(await screen.findByText("local-box")).toBeInTheDocument();
    expect(screen.getByText("queue-box")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Check local-box" }));
    await waitFor(() => {
      expect(checkAgentHost).toHaveBeenCalledWith("local-box");
    });
    expect(await screen.findByText("Last check: offline")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Edit local-box" }));
    expect(screen.getByDisplayValue("local-box")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Max concurrent"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save host" }));

    await waitFor(() => {
      expect(updateAgentHost).toHaveBeenCalledWith("local-box", {
        agent_type: "both",
        host: "local",
        labels: ["runner", "west"],
        max_concurrent: 5,
        ssh_key: "",
      });
    });

    fireEvent.click(screen.getByRole("button", { name: "Create new" }));
    fireEvent.change(screen.getByLabelText("Host id"), { target: { value: "new-box" } });
    fireEvent.change(screen.getByLabelText("Host address"), { target: { value: "10.0.0.9" } });
    fireEvent.change(screen.getByLabelText("Agent type"), { target: { value: "claude" } });
    fireEvent.change(screen.getByLabelText("Max concurrent"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Labels"), { target: { value: "blue, night" } });
    fireEvent.click(screen.getByRole("button", { name: "Save host" }));

    await waitFor(() => {
      expect(createAgentHost).toHaveBeenCalledWith({
        agent_type: "claude",
        host: "10.0.0.9",
        id: "new-box",
        labels: ["blue", "night"],
        max_concurrent: 4,
        ssh_key: "",
      });
    });
    expect(await screen.findByText("new-box")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Delete queue-box" }));
    await waitFor(() => {
      expect(deleteAgentHost).toHaveBeenCalledWith("queue-box");
    });
    await waitFor(() => {
      expect(screen.queryByText("queue-box")).not.toBeInTheDocument();
    });
  });
});
