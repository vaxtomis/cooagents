import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { listAgentHosts } from "../api/agents";
import { approveRun, listRuns, rejectRun } from "../api/runs";
import { DashboardPage } from "./DashboardPage";

vi.mock("../api/agents", () => ({
  listAgentHosts: vi.fn(),
}));

vi.mock("../api/runs", async () => {
  const actual = await vi.importActual<typeof import("../api/runs")>("../api/runs");
  return {
    ...actual,
    listRuns: vi.fn(),
    approveRun: vi.fn(),
    rejectRun: vi.fn(),
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  return render(
    <SWRConfig value={{ dedupingInterval: 0, provider: () => new Map(), revalidateOnFocus: false }}>
      <DashboardPage />
    </SWRConfig>,
  );
}

function expectStatCard(title: string, value: string) {
  const card = screen.getAllByText(title)[0]?.closest("section");
  expect(card).not.toBeNull();
  expect(within(card as HTMLElement).getByText(value)).toBeInTheDocument();
}

describe("DashboardPage", () => {
  it("renders spec-aligned stats, active runs, pending approvals, host summary, and refreshes after approval", async () => {
    const now = new Date().toISOString();
    let approvalComplete = false;

    vi.mocked(listRuns).mockImplementation(async (params = {}) => {
      if (params.status === "running") {
        return {
          items: approvalComplete
            ? [
                {
                  created_at: now,
                  current_stage: "DEV_RUNNING",
                  description: "Implement dashboard shell",
                  id: "run-1",
                  repo_path: "C:/repo/project",
                  status: "running",
                  ticket: "PROJ-100",
                  updated_at: now,
                },
                {
                  created_at: now,
                  current_stage: "DEV_RUNNING",
                  description: "Requirement brief approved",
                  id: "run-2",
                  repo_path: "C:/repo/project",
                  status: "running",
                  ticket: "PROJ-101",
                  updated_at: now,
                },
                {
                  created_at: now,
                  current_stage: "MERGING",
                  description: "Merge execution in progress",
                  id: "run-4",
                  repo_path: "C:/repo/project",
                  status: "running",
                  ticket: "PROJ-102",
                  updated_at: now,
                },
              ]
            : [
                {
                  created_at: now,
                  current_stage: "DEV_RUNNING",
                  description: "Implement dashboard shell",
                  id: "run-1",
                  repo_path: "C:/repo/project",
                  status: "running",
                  ticket: "PROJ-100",
                  updated_at: now,
                },
                {
                  created_at: now,
                  current_stage: "REQ_REVIEW",
                  description: "Requirement brief awaiting review",
                  id: "run-2",
                  repo_path: "C:/repo/project",
                  status: "running",
                  ticket: "PROJ-101",
                  updated_at: now,
                },
                {
                  created_at: now,
                  current_stage: "MERGING",
                  description: "Merge execution in progress",
                  id: "run-4",
                  repo_path: "C:/repo/project",
                  status: "running",
                  ticket: "PROJ-102",
                  updated_at: now,
                },
              ],
          limit: 20,
          offset: 0,
          total: 3,
        };
      }

      return {
        items: approvalComplete
          ? [
              {
                created_at: now,
                current_stage: "DEV_RUNNING",
                description: "Implement dashboard shell",
                id: "run-1",
                repo_path: "C:/repo/project",
                status: "running",
                ticket: "PROJ-100",
                updated_at: now,
              },
              {
                created_at: now,
                current_stage: "DEV_RUNNING",
                description: "Requirement brief approved",
                id: "run-2",
                repo_path: "C:/repo/project",
                status: "running",
                ticket: "PROJ-101",
                updated_at: now,
              },
              {
                created_at: now,
                current_stage: "FAILED",
                description: "Broken merge recovery",
                failed_at_stage: "DEV_RUNNING",
                id: "run-3",
                repo_path: "C:/repo/project",
                status: "failed",
                ticket: "PROJ-099",
                updated_at: now,
              },
              {
                created_at: now,
                current_stage: "MERGING",
                description: "Merge execution in progress",
                id: "run-4",
                repo_path: "C:/repo/project",
                status: "running",
                ticket: "PROJ-102",
                updated_at: now,
              },
              {
                created_at: now,
                current_stage: "MERGED",
                description: "Completed and merged",
                id: "run-5",
                repo_path: "C:/repo/project",
                status: "completed",
                ticket: "PROJ-103",
                updated_at: now,
              },
            ]
          : [
              {
                created_at: now,
                current_stage: "DEV_RUNNING",
                description: "Implement dashboard shell",
                id: "run-1",
                repo_path: "C:/repo/project",
                status: "running",
                ticket: "PROJ-100",
                updated_at: now,
              },
              {
                created_at: now,
                current_stage: "REQ_REVIEW",
                description: "Requirement brief awaiting review",
                id: "run-2",
                repo_path: "C:/repo/project",
                status: "running",
                ticket: "PROJ-101",
                updated_at: now,
              },
              {
                created_at: now,
                current_stage: "FAILED",
                description: "Broken merge recovery",
                failed_at_stage: "DEV_RUNNING",
                id: "run-3",
                repo_path: "C:/repo/project",
                status: "failed",
                ticket: "PROJ-099",
                updated_at: now,
              },
              {
                created_at: now,
                current_stage: "MERGING",
                description: "Merge execution in progress",
                id: "run-4",
                repo_path: "C:/repo/project",
                status: "running",
                ticket: "PROJ-102",
                updated_at: now,
              },
              {
                created_at: now,
                current_stage: "MERGED",
                description: "Completed and merged",
                id: "run-5",
                repo_path: "C:/repo/project",
                status: "completed",
                ticket: "PROJ-103",
                updated_at: now,
              },
            ],
        limit: 100,
        offset: 0,
        total: 5,
      };
    });

    vi.mocked(listAgentHosts).mockResolvedValue([
      {
        agent_type: "both",
        created_at: now,
        current_load: 1,
        host: "codex-worker-01",
        id: "host-1",
        labels: ["linux", "west"],
        labels_json: '["linux","west"]',
        max_concurrent: 2,
        status: "active",
        updated_at: now,
      },
      {
        agent_type: "claude",
        created_at: now,
        current_load: 0,
        host: "claude-host-02",
        id: "host-2",
        labels: ["macos", "east"],
        labels_json: '["macos","east"]',
        max_concurrent: 4,
        status: "offline",
        updated_at: now,
      },
    ]);

    vi.mocked(approveRun).mockImplementation(async () => {
      approvalComplete = true;
      return { id: "run-2", run_id: "run-2" } as never;
    });
    vi.mocked(rejectRun).mockResolvedValue({ id: "run-2", run_id: "run-2" } as never);

    renderPage();

    await screen.findAllByText("运行中");
    expectStatCard("运行中", "03");
    expectStatCard("待审批", "01");
    expectStatCard("合并中", "01");
    expectStatCard("失败", "01");
    expectStatCard("已完成", "01");
    expect(screen.getAllByText("PROJ-100").length).toBeGreaterThan(0);
    expect(screen.getAllByText("PROJ-101").length).toBeGreaterThan(0);
    expect(screen.getAllByText("PROJ-102").length).toBeGreaterThan(0);
    expect(screen.getByText("codex-worker-01")).toBeInTheDocument();
    expect(screen.getByText("claude-host-02")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "批准" }));

    await waitFor(() => {
      expect(approveRun).toHaveBeenCalledWith("run-2", {
        comment: undefined,
        gate: "req",
      });
    });

    await waitFor(() => {
      expect(listRuns.mock.calls.length).toBeGreaterThan(2);
    });

    await waitFor(() => {
      expectStatCard("待审批", "00");
      expect(screen.queryByRole("button", { name: "批准" })).not.toBeInTheDocument();
    });
  });
});
