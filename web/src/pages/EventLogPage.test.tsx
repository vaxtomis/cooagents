import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouterProvider, createMemoryRouter, useParams } from "react-router-dom";
import { listEvents } from "../api/events";
import { EventLogPage } from "./EventLogPage";

vi.mock("../api/events", async () => {
  const actual = await vi.importActual<typeof import("../api/events")>("../api/events");
  return {
    ...actual,
    listEvents: vi.fn(),
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

function RunProbe() {
  const { runId } = useParams();
  return <div>{`run:${runId}`}</div>;
}

function renderPage(initialEntry = "/events?runId=run-1&level=warning&spanType=job&page=2") {
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
  it("reads filters from the URL, paginates server-side, expands payloads, and links to run detail", async () => {
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

    const router = renderPage();

    expect(await screen.findByText("job.failed")).toBeInTheDocument();
    await waitFor(() => {
      expect(listEvents).toHaveBeenCalledWith({
        level: "warning",
        limit: 20,
        offset: 20,
        runId: "run-1",
        spanType: "job",
      });
    });

    expect(screen.getByDisplayValue("run-1")).toBeInTheDocument();
    expect(screen.getByDisplayValue("warning")).toBeInTheDocument();
    expect(screen.getByDisplayValue("job")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Expand payload 101" }));
    expect(await screen.findByText((_, element) => element?.textContent === JSON.stringify({ reason: "timeout", step: 3 }, null, 2))).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "Open run-1" }));
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/runs/run-1");
    });
    expect(screen.getByText("run:run-1")).toBeInTheDocument();
  });
});
