import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouterProvider, createMemoryRouter, useParams } from "react-router-dom";
import { listEvents } from "../api/events";
import { getJobDiagnosis, getRunTrace, getTraceLookup } from "../api/diagnostics";
import { EventLogPage } from "./EventLogPage";

vi.mock("../api/events", () => ({
  listEvents: vi.fn(),
}));

vi.mock("../api/diagnostics", () => ({
  getJobDiagnosis: vi.fn(),
  getRunTrace: vi.fn(),
  getTraceLookup: vi.fn(),
}));

afterEach(() => {
  vi.clearAllMocks();
});

function RunProbe() {
  const { runId } = useParams();
  return <div>{`run:${runId}`}</div>;
}

function renderPage(initialEntry = "/events?page=2") {
  const router = createMemoryRouter(
    [
      { path: "/events", element: <EventLogPage /> },
      { path: "/runs/:runId", element: <RunProbe /> },
    ],
    { initialEntries: [initialEntry] },
  );

  render(
    <SWRConfig value={{ dedupingInterval: 0, provider: () => new Map(), revalidateOnFocus: false }}>
      <RouterProvider router={router} />
    </SWRConfig>,
  );

  return router;
}

describe("EventLogPage", () => {
  it("keeps the global browser mode for server-backed event browsing", async () => {
    const now = new Date().toISOString();
    vi.mocked(listEvents).mockResolvedValue({
      events: [
        {
          created_at: now,
          event_type: "job.failed",
          id: 101,
          level: "warning",
          payload: { reason: "timeout", step: 3 },
          run_id: "run-1",
          span_type: "job",
          source: "scheduler",
        },
        {
          created_at: now,
          event_type: "system.heartbeat",
          id: 102,
          level: "info",
          payload: "ok",
          run_id: null,
          span_type: "system",
          source: "system",
        },
      ],
      pagination: {
        has_more: true,
        limit: 20,
        offset: 20,
        total: 41,
      },
    });

    const router = renderPage("/events?level=warning&spanType=job&page=2");

    expect(await screen.findByText("job.failed")).toBeInTheDocument();
    await waitFor(() => {
      expect(listEvents).toHaveBeenCalledWith({
        level: "warning",
        limit: 20,
        offset: 20,
        runId: undefined,
        spanType: "job",
      });
    });

    expect(screen.getByDisplayValue("warning")).toBeInTheDocument();
    expect(screen.getByDisplayValue("job")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Expand payload 101" }));
    expect(
      await screen.findByText((_, element) => element?.textContent === JSON.stringify({ reason: "timeout", step: 3 }, null, 2)),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Open run-1" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/runs/run-1");
    });
    expect(screen.getByText("run:run-1")).toBeInTheDocument();
  });

  it("switches to run diagnostic mode, renders anomaly context, and opens drilldowns", async () => {
    const now = new Date().toISOString();
    vi.mocked(getRunTrace).mockResolvedValue({
      created_at: now,
      current_stage: "DEV_RUNNING",
      events: [
        {
          created_at: "2026-04-01T10:00:00",
          event_type: "job.started",
          id: 1,
          job_id: "job-9",
          level: "info",
          payload: { step: "boot" },
          run_id: "run-9",
          source: "scheduler",
          span_type: "job",
          trace_id: "trace-9",
        },
        {
          created_at: "2026-04-01T10:00:01",
          event_type: "job.warning",
          id: 2,
          job_id: "job-9",
          level: "warning",
          payload: { reason: "retry" },
          run_id: "run-9",
          source: "scheduler",
          span_type: "job",
          trace_id: "trace-9",
        },
        {
          created_at: "2026-04-01T10:00:02",
          event_type: "job.failed",
          error_detail: "timeout",
          id: 3,
          job_id: "job-9",
          level: "error",
          payload: { reason: "timeout" },
          run_id: "run-9",
          source: "scheduler",
          span_type: "job",
          trace_id: "trace-9",
        },
        {
          created_at: "2026-04-01T10:00:03",
          event_type: "run.failed",
          id: 4,
          level: "info",
          payload: { stage: "DEV_RUNNING" },
          run_id: "run-9",
          source: "system",
          span_type: "run",
          trace_id: "trace-10",
        },
      ],
      failed_at_stage: "DEV_RUNNING",
      pagination: {
        has_more: false,
        limit: 200,
        offset: 0,
        total: 4,
      },
      run_id: "run-9",
      status: "failed",
      summary: {
        errors: 1,
        jobs: [{ duration_ms: 3210, job_id: "job-9", stage: "DEV_RUNNING", status: "failed" }],
        stages_visited: ["INIT", "DEV_RUNNING"],
        total_duration_ms: 3210,
        total_events: 4,
        warnings: 1,
      },
    });
    vi.mocked(getJobDiagnosis).mockResolvedValue({
      agent_type: "claude",
      diagnosis: {
        duration_ms: 3210,
        error_detail: "stacktrace",
        error_summary: "timeout",
        failure_context: {
          host_status_at_failure: "offline",
          retry_count: 2,
          stage_at_failure: "DEV_RUNNING",
        },
        last_output_excerpt: "last output",
        turn_count: 7,
      },
      ended_at: now,
      events: [],
      host_id: "host-1",
      job_id: "job-9",
      run_id: "run-9",
      session_name: "job-session",
      stage: "DEV_RUNNING",
      started_at: now,
      status: "failed",
      turns: [],
    });
    vi.mocked(getTraceLookup).mockResolvedValue({
      affected_jobs: ["job-9"],
      affected_runs: ["run-9"],
      error_count: 1,
      events: [
        {
          created_at: now,
          event_type: "job.failed",
          id: 3,
          job_id: "job-9",
          level: "error",
          payload: { reason: "timeout" },
          run_id: "run-9",
          span_type: "job",
          trace_id: "trace-9",
        },
      ],
      first_seen: now,
      last_seen: now,
      origin: "internal",
      total_duration_ms: 3210,
      trace_id: "trace-9",
    });

    renderPage("/events?runId=run-9");

    expect(await screen.findByText("Run diagnosis")).toBeInTheDocument();
    await waitFor(() => {
      expect(getRunTrace).toHaveBeenCalledWith("run-9", {
        level: "info",
        limit: 200,
        spanType: undefined,
      });
    });

    expect(screen.getByText("Failed at stage")).toBeInTheDocument();
    expect(screen.getByText("DEV_RUNNING")).toBeInTheDocument();
    expect(screen.getAllByText("Anomaly").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Context").length).toBeGreaterThan(0);
    expect(screen.getByText("job.warning")).toBeInTheDocument();
    expect(screen.getByText("run.failed")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "Open job job-9" })[0]);
    expect(await screen.findByText("Job diagnosis")).toBeInTheDocument();
    expect(await screen.findByText("timeout")).toBeInTheDocument();
    expect(getJobDiagnosis).toHaveBeenCalledWith("job-9");

    fireEvent.click(screen.getAllByRole("button", { name: "Open trace trace-9" })[0]);
    expect(await screen.findByText("Trace trace-9")).toBeInTheDocument();
    expect(getTraceLookup).toHaveBeenCalledWith("trace-9", "info");
  });

  it("shows the empty anomaly state and lets the operator widen the threshold", async () => {
    const now = new Date().toISOString();
    vi.mocked(getRunTrace).mockResolvedValue({
      created_at: now,
      current_stage: "MERGING",
      events: [
        {
          created_at: now,
          event_type: "merge.started",
          id: 1,
          level: "info",
          payload: { branch: "feature/a" },
          run_id: "run-2",
          source: "merge",
          span_type: "run",
        },
      ],
      failed_at_stage: null,
      pagination: {
        has_more: false,
        limit: 200,
        offset: 0,
        total: 1,
      },
      run_id: "run-2",
      status: "running",
      summary: {
        errors: 0,
        jobs: [],
        stages_visited: ["MERGING"],
        total_duration_ms: 100,
        total_events: 1,
        warnings: 0,
      },
    });

    renderPage("/events?runId=run-2");

    expect(await screen.findByText("No warnings or errors for this run")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show info context" }));
    expect(await screen.findByText("merge.started")).toBeInTheDocument();
  });
});
