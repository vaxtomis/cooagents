import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  it("prioritizes host configuration details while preserving check, edit, create, and delete actions", async () => {
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
        ssh_key: "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQ",
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
    expect(screen.getAllByText("Agent 类型").length).toBeGreaterThan(0);
    expect(screen.getAllByText("最大并发").length).toBeGreaterThan(0);
    expect(screen.getAllByText("SSH 密钥").length).toBeGreaterThan(0);
    expect(screen.getByText("未配置")).toBeInTheDocument();
    expect(screen.getByText("已配置")).toBeInTheDocument();
    expect(screen.getByText("runner")).toBeInTheDocument();
    expect(screen.getByText("west")).toBeInTheDocument();

    const localCard = screen.getByText("local-box").closest("article")!;
    fireEvent.click(within(localCard).getByRole("button", { name: "检查" }));
    await waitFor(() => {
      expect(checkAgentHost).toHaveBeenCalledWith("local-box");
    });
    expect(await screen.findByText("检查结果：离线")).toBeInTheDocument();

    fireEvent.click(within(localCard).getByRole("button", { name: "编辑" }));
    expect(screen.getByDisplayValue("local-box")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("最大并发"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "保存主机" }));

    await waitFor(() => {
      expect(updateAgentHost).toHaveBeenCalledWith("local-box", {
        agent_type: "both",
        host: "local",
        labels: ["runner", "west"],
        max_concurrent: 5,
        ssh_key: "",
      });
    });

    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    fireEvent.change(screen.getByLabelText("主机 ID"), { target: { value: "new-box" } });
    fireEvent.change(screen.getByLabelText("主机地址"), { target: { value: "10.0.0.9" } });
    fireEvent.change(screen.getByLabelText("Agent 类型"), { target: { value: "claude" } });
    fireEvent.change(screen.getByLabelText("最大并发"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("标签"), { target: { value: "blue, night" } });
    fireEvent.click(screen.getByRole("button", { name: "保存主机" }));

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

    const queueCard = screen.getByText("queue-box").closest("article")!;
    fireEvent.click(within(queueCard).getByRole("button", { name: "删除" }));
    await waitFor(() => {
      expect(deleteAgentHost).toHaveBeenCalledWith("queue-box");
    });
    await waitFor(() => {
      expect(screen.queryByText("queue-box")).not.toBeInTheDocument();
    });
  });
});
