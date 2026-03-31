import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import {
  cancelRun,
  getArtifactContent,
  getArtifactDiff,
  getJobOutput,
  getRun,
  getRunBrief,
  getRunEventsStreamUrl,
  listArtifacts,
  listJobs,
} from "../api/runs";
import { getRunTrace } from "../api/diagnostics";
import { useSSE } from "../hooks/useSSE";
import { RunDetailPage } from "./RunDetailPage";

const sseState = vi.hoisted(() => ({
  lastCall: null as null | {
    url: string | null | undefined;
    options: {
      enabled?: boolean;
      eventTypes?: string[];
      onError?: () => void;
      onEvent?: (event: { type: string; data: unknown }) => void;
    };
  },
}));

vi.mock("../api/runs", () => ({
  approveRun: vi.fn(),
  cancelRun: vi.fn(),
  getArtifactContent: vi.fn(),
  getArtifactDiff: vi.fn(),
  getJobOutput: vi.fn(),
  getRun: vi.fn(),
  getRunBrief: vi.fn(),
  getRunEventsStreamUrl: vi.fn((runId: string) => `/api/v1/runs/${runId}/events/stream`),
  listArtifacts: vi.fn(),
  listJobs: vi.fn(),
  rejectRun: vi.fn(),
}));

vi.mock("../api/diagnostics", () => ({
  getRunTrace: vi.fn(),
}));

vi.mock("../hooks/useSSE", () => ({
  useSSE: vi.fn((url: string | null | undefined, options: Record<string, unknown> = {}) => {
    sseState.lastCall = {
      options: options as {
        enabled?: boolean;
        eventTypes?: string[];
        onError?: () => void;
        onEvent?: (event: { type: string; data: unknown }) => void;
      },
      url,
    };
    return { isLive: true, state: "live" };
  }),
}));

afterEach(() => {
  vi.clearAllMocks();
  sseState.lastCall = null;
});

function renderPage(initialEntry = "/runs/run-210") {
  const router = createMemoryRouter([{ path: "/runs/:runId", element: <RunDetailPage /> }], {
    initialEntries: [initialEntry],
  });

  render(
    <SWRConfig value={{ dedupingInterval: 0, provider: () => new Map(), revalidateOnFocus: false }}>
      <RouterProvider router={router} />
    </SWRConfig>,
  );

  return router;
}

describe("RunDetailPage", () => {
  it("loads run detail data, exposes live actions, and revalidates on SSE events", async () => {
    const now = new Date().toISOString();
    vi.mocked(getRun).mockResolvedValue({
      approvals: [],
      artifacts: [],
      created_at: now,
      current_stage: "DEV_REVIEW",
      description: "Dashboard polish",
      id: "run-210",
      recent_events: [],
      repo_path: "C:/repo/project",
      status: "running",
      steps: [],
      ticket: "PROJ-210",
      updated_at: now,
    });
    vi.mocked(getRunBrief).mockResolvedValue({
      created_at: now,
      current: {
        action_type: "approval",
        description: "Awaiting development approval",
        elapsed_sec: 420,
        stage: "DEV_REVIEW",
        summary: "Development output is ready for review.",
      },
      previous: {
        at: now,
        by: "euler",
        reason: null,
        result: "completed",
        stage: "DEV_RUNNING",
        triggered_by: "scheduler",
      },
      progress: {
        artifacts_count: 1,
        gates_passed: ["req", "design"],
        gates_remaining: ["dev"],
      },
      run_id: "run-210",
      status: "running",
      ticket: "PROJ-210",
    });
    vi.mocked(listJobs).mockResolvedValue([
      {
        agent_type: "codex",
        ended_at: null,
        events_file: null,
        host_id: "host-1",
        id: "job-dev-1",
        run_id: "run-210",
        stage: "DEV_RUNNING",
        started_at: now,
        status: "running",
        task_file: null,
      },
    ]);
    vi.mocked(listArtifacts).mockResolvedValue([
      {
        created_at: now,
        id: 7,
        kind: "design_doc",
        path: "docs/plan.md",
        run_id: "run-210",
        status: "ready",
        version: 3,
      },
    ]);
    vi.mocked(getRunTrace).mockResolvedValue({
      created_at: now,
      current_stage: "DEV_REVIEW",
      events: [
        {
          created_at: now,
          event_type: "stage.changed",
          level: "info",
          payload: { to: "DEV_REVIEW" },
          run_id: "run-210",
          source: "engine",
        },
      ],
      failed_at_stage: null,
      pagination: { has_more: false, limit: 50, offset: 0, total: 1 },
      run_id: "run-210",
      status: "running",
      summary: {
        errors: 0,
        jobs: [{ duration_ms: 90000, job_id: "job-dev-1", stage: "DEV_RUNNING", status: "completed" }],
        stages_visited: ["REQ_COLLECTING", "REQ_REVIEW", "DEV_RUNNING", "DEV_REVIEW"],
        total_duration_ms: 90000,
        total_events: 1,
        warnings: 0,
      },
    });
    vi.mocked(getArtifactContent).mockResolvedValue({
      content: "# Plan\nship it",
      created_at: now,
      id: 7,
      kind: "design_doc",
      path: "docs/plan.md",
      run_id: "run-210",
      status: "ready",
      version: 3,
    });
    vi.mocked(getArtifactDiff).mockResolvedValue({ artifact_id: 7, diff: "@@ -1 +1 @@\n- old\n+ new" });
    vi.mocked(getJobOutput).mockResolvedValue({ job_id: "job-dev-1", output: "build ok" });
    vi.mocked(cancelRun).mockResolvedValue({ ok: true, status: "cancelled" });

    renderPage();

    expect(await screen.findByText("PROJ-210")).toBeInTheDocument();
    expect(screen.getByText("Development output is ready for review.")).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();

    await waitFor(() => {
      expect(getRun).toHaveBeenCalledWith("run-210");
      expect(getRunBrief).toHaveBeenCalledWith("run-210");
      expect(listJobs).toHaveBeenCalledWith("run-210");
      expect(listArtifacts).toHaveBeenCalledWith("run-210");
      expect(getRunTrace).toHaveBeenCalledWith("run-210", { limit: 50 });
    });

    expect(getRunEventsStreamUrl).toHaveBeenCalledWith("run-210");
    expect(useSSE).toHaveBeenCalled();
    expect(sseState.lastCall?.url).toBe("/api/v1/runs/run-210/events/stream");

    fireEvent.click(screen.getByRole("button", { name: "Inspect docs/plan.md" }));
    expect(await screen.findByText((_, element) => element?.textContent === "# Plan\nship it")).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === "@@ -1 +1 @@\n- old\n+ new")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Load output job-dev-1" }));
    expect(await screen.findByText("build ok")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    await waitFor(() => {
      expect(cancelRun).toHaveBeenCalledWith("run-210", false);
    });

    await act(async () => {
      sseState.lastCall?.options.onEvent?.({ data: { run_id: "run-210" }, type: "run.completed" });
    });

    await waitFor(() => {
      expect(getRun).toHaveBeenCalledTimes(2);
      expect(getRunTrace).toHaveBeenCalledTimes(2);
    });
  });
});


