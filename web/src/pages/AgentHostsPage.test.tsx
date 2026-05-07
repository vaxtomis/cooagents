import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createAgentHost,
  deleteAgentHost,
  healthcheckAgentHost,
  listAgentHosts,
  syncAgentHosts,
  updateAgentHost,
} from "../api/agentHosts";
import type { AgentHost } from "../types";
import { AgentHostsPage } from "./AgentHostsPage";

vi.mock("../api/agentHosts", () => ({
  listAgentHosts: vi.fn(),
  getAgentHost: vi.fn(),
  createAgentHost: vi.fn(),
  updateAgentHost: vi.fn(),
  deleteAgentHost: vi.fn(),
  healthcheckAgentHost: vi.fn(),
  syncAgentHosts: vi.fn(),
}));

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  render(
    <SWRConfig
      value={{
        dedupingInterval: 0,
        provider: () => new Map(),
        revalidateOnFocus: false,
      }}
    >
      <MemoryRouter>
        <AgentHostsPage />
      </MemoryRouter>
    </SWRConfig>,
  );
}

const localHost: AgentHost = {
  id: "local",
  host: "local",
  agent_type: "both",
  max_concurrent: 1,
  labels: [],
  health_status: "healthy",
  last_health_at: "2026-05-06T05:30:00Z",
  last_health_err: null,
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-06T05:30:00Z",
};

const remoteHost: AgentHost = {
  id: "ah-remote",
  host: "dev@10.0.0.5",
  agent_type: "codex",
  max_concurrent: 4,
  labels: ["gpu", "cn"],
  health_status: "unknown",
  last_health_at: null,
  last_health_err: null,
  created_at: "2026-05-02T00:00:00Z",
  updated_at: "2026-05-06T05:32:00Z",
};

describe("AgentHostsPage", () => {
  it("renders hosts from the API", async () => {
    vi.mocked(listAgentHosts).mockResolvedValue([localHost, remoteHost]);

    renderPage();

    expect(await screen.findByText("本机 Agent Host")).toBeInTheDocument();
    expect(screen.getByText("dev@10.0.0.5")).toBeInTheDocument();
    expect(screen.getByText("gpu")).toBeInTheDocument();
  });

  it("creates an agent host from the dialog form", async () => {
    vi.mocked(listAgentHosts).mockResolvedValue([]);
    vi.mocked(createAgentHost).mockResolvedValue(remoteHost);

    renderPage();
    await waitFor(() => expect(listAgentHosts).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "新增 Agent Host" }));
    expect(document.querySelector('[data-dialog-size="wide"]')).not.toBeNull();
    fireEvent.change(screen.getByLabelText("连接地址"), {
      target: { value: "dev@10.0.0.5" },
    });
    fireEvent.change(screen.getByLabelText("并发上限"), {
      target: { value: "4" },
    });
    fireEvent.change(screen.getByLabelText("标签"), {
      target: { value: "gpu, cn" },
    });
    fireEvent.submit(screen.getByLabelText("连接地址").closest("form")!);

    await waitFor(() => {
      expect(createAgentHost).toHaveBeenCalledWith({
        host: "dev@10.0.0.5",
        agent_type: "both",
        max_concurrent: 4,
        id: undefined,
        labels: ["gpu", "cn"],
        ssh_key: null,
      });
    });
  });

  it("runs a healthcheck for a host from the row action", async () => {
    vi.mocked(listAgentHosts).mockResolvedValue([remoteHost]);
    vi.mocked(healthcheckAgentHost).mockResolvedValue({
      ...remoteHost,
      health_status: "healthy",
      last_health_at: "2026-05-06T05:40:00Z",
    });
    vi.mocked(deleteAgentHost).mockResolvedValue();
    vi.mocked(syncAgentHosts).mockResolvedValue({ upserted: 0, marked_unknown: 0 });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "健康检查" }));

    await waitFor(() => {
      expect(healthcheckAgentHost).toHaveBeenCalledWith("ah-remote");
    });
  });

  it("edits a remote host without clearing the stored ssh key", async () => {
    vi.mocked(listAgentHosts).mockResolvedValue([localHost, remoteHost]);
    vi.mocked(updateAgentHost).mockResolvedValue({
      ...remoteHost,
      host: "ops@10.0.0.9",
      agent_type: "claude",
      max_concurrent: 6,
      labels: ["ops", "eu"],
    });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Edit ah-remote" }));

    const dialog = await screen.findByRole("dialog", { name: "Edit Agent Host" });
    fireEvent.change(within(dialog).getByLabelText("Edit host"), {
      target: { value: "ops@10.0.0.9" },
    });
    fireEvent.change(within(dialog).getByLabelText("Edit agent type"), {
      target: { value: "claude" },
    });
    fireEvent.change(within(dialog).getByLabelText("Edit max concurrent"), {
      target: { value: "6" },
    });
    fireEvent.change(within(dialog).getByLabelText("Edit labels"), {
      target: { value: "ops, eu" },
    });
    fireEvent.submit(within(dialog).getByLabelText("Edit host").closest("form")!);

    await waitFor(() => {
      expect(updateAgentHost).toHaveBeenCalledWith("ah-remote", {
        host: "ops@10.0.0.9",
        agent_type: "claude",
        max_concurrent: 6,
        labels: ["ops", "eu"],
      });
    });
  });

  it("allows editing the local host while keeping host fixed to local", async () => {
    vi.mocked(listAgentHosts).mockResolvedValue([localHost]);
    vi.mocked(updateAgentHost).mockResolvedValue({
      ...localHost,
      agent_type: "codex",
      max_concurrent: 2,
      labels: ["built-in"],
    });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Edit local" }));

    const dialog = await screen.findByRole("dialog", { name: "Edit Agent Host" });
    const hostInput = within(dialog).getByLabelText("Edit host");
    expect(hostInput).toHaveValue("local");
    expect(hostInput).toHaveAttribute("readonly");
    fireEvent.change(within(dialog).getByLabelText("Edit agent type"), {
      target: { value: "codex" },
    });
    fireEvent.change(within(dialog).getByLabelText("Edit max concurrent"), {
      target: { value: "2" },
    });
    fireEvent.change(within(dialog).getByLabelText("Edit labels"), {
      target: { value: "built-in" },
    });
    fireEvent.submit(hostInput.closest("form")!);

    await waitFor(() => {
      expect(updateAgentHost).toHaveBeenCalledWith("local", {
        host: "local",
        agent_type: "codex",
        max_concurrent: 2,
        labels: ["built-in"],
      });
    });
  });
});
