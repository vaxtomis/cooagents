import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouterProvider, createMemoryRouter, useParams } from "react-router-dom";
import { listRuns } from "../api/runs";
import { RunsListPage } from "./RunsListPage";

vi.mock("../api/runs", async () => {
  const actual = await vi.importActual<typeof import("../api/runs")>("../api/runs");
  return {
    ...actual,
    listRuns: vi.fn(),
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

function DetailProbe() {
  const { runId } = useParams();
  return <div>{`detail:${runId}`}</div>;
}

function renderPage(initialEntry = "/runs?ticket=PROJ&stage=DEV_RUNNING&sortBy=ticket&sortOrder=asc&page=2") {
  const router = createMemoryRouter(
    [
      { path: "/runs", element: <RunsListPage /> },
      { path: "/runs/:runId", element: <DetailProbe /> },
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

describe("RunsListPage", () => {
  it("requests server-side data, preserves query state across refresh, and navigates to run detail", async () => {
    const now = new Date().toISOString();
    vi.mocked(listRuns).mockResolvedValue({
      items: [
        {
          created_at: now,
          current_stage: "DEV_RUNNING",
          description: "Dashboard polish",
          id: "run-210",
          repo_path: "C:/repo/project",
          status: "running",
          ticket: "PROJ-210",
          updated_at: now,
        },
      ],
      limit: 10,
      offset: 10,
      total: 21,
    });

    const router = renderPage();

    expect(await screen.findByText("PROJ-210")).toBeInTheDocument();
    await waitFor(() => {
      expect(listRuns).toHaveBeenCalledWith({
        currentStage: "DEV_RUNNING",
        limit: 10,
        offset: 10,
        sortBy: "ticket",
        sortOrder: "asc",
        ticket: "PROJ",
      });
    });

    expect(screen.getByDisplayValue("PROJ")).toBeInTheDocument();
    expect(screen.getByDisplayValue("DEV_RUNNING")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "刷新" }));

    await waitFor(() => {
      expect(listRuns).toHaveBeenCalledTimes(2);
    });
    expect(vi.mocked(listRuns).mock.calls.at(-1)?.[0]).toEqual({
      currentStage: "DEV_RUNNING",
      limit: 10,
      offset: 10,
      sortBy: "ticket",
      sortOrder: "asc",
      ticket: "PROJ",
    });

    fireEvent.click(screen.getByRole("button", { name: "打开 PROJ-210" }));

    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/runs/run-210");
    });
    expect(screen.getByText("detail:run-210")).toBeInTheDocument();
  });
});
