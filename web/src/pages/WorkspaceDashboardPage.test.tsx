import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Workspace, WorkspaceMetrics } from "../types";
import { WorkspaceDashboardPage } from "./WorkspaceDashboardPage";

vi.mock("../api/metrics", () => ({
  getWorkspaceMetrics: vi.fn(),
}));
vi.mock("../api/workspaces", () => ({
  listWorkspaces: vi.fn(),
}));

import { getWorkspaceMetrics } from "../api/metrics";
import { listWorkspaces } from "../api/workspaces";

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  render(
    <SWRConfig value={{ dedupingInterval: 0, provider: () => new Map(), revalidateOnFocus: false }}>
      <MemoryRouter>
        <WorkspaceDashboardPage />
      </MemoryRouter>
    </SWRConfig>,
  );
}

const baseWorkspace = (id: string): Workspace => ({
  id,
  title: `T-${id}`,
  slug: id,
  status: "active",
  root_path: `/tmp/${id}`,
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-23T00:00:00Z",
});

describe("WorkspaceDashboardPage", () => {
  it("renders all four HeroStat values from a single /metrics/workspaces call", async () => {
    const metrics: WorkspaceMetrics = {
      human_intervention_per_workspace: 1.25,
      active_workspaces: 3,
      first_pass_success_rate: 0.6667,
      avg_iteration_rounds: 2.5,
    };
    vi.mocked(getWorkspaceMetrics).mockResolvedValue(metrics);
    vi.mocked(listWorkspaces).mockResolvedValue([baseWorkspace("ws-a"), baseWorkspace("ws-b")]);

    renderPage();

    // active_workspaces rendered as zero-padded string
    const activeWorkspaces = await screen.findByText("03");
    expect(activeWorkspaces).toBeInTheDocument();
    // 1.25.toFixed(2)
    expect(screen.getByText("1.25")).toBeInTheDocument();
    // 0.6667 * 100 rounded
    expect(screen.getByText("67%")).toBeInTheDocument();
    // 2.5.toFixed(1)
    expect(screen.getByText("2.5")).toBeInTheDocument();

    // getWorkspaceMetrics should be called exactly once for the dashboard load.
    expect(vi.mocked(getWorkspaceMetrics)).toHaveBeenCalledTimes(1);

    for (const metricValue of ["03", "1.25", "67%", "2.5"]) {
      const valueNode = screen.getByText(metricValue);
      expect(valueNode).toHaveAttribute("data-hero-stat-value", "true");
      expect(valueNode.className).toContain("text-copy");
      expect(valueNode.className).not.toContain("ink-invert");
    }
  });

  it("renders '-' placeholders while metrics are loading", async () => {
    // Pending promise — never resolves during the test.
    vi.mocked(getWorkspaceMetrics).mockReturnValue(new Promise(() => {}));
    vi.mocked(listWorkspaces).mockResolvedValue([]);

    renderPage();

    await waitFor(() => {
      // All four HeroStat cards should show '-' placeholder.
      const dashes = screen.getAllByText("-");
      expect(dashes.length).toBe(4);
    });
  });
});
