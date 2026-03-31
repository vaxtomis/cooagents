import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MergeQueuePage } from "./MergeQueuePage";
import { getRun } from "../api/runs";
import { listMergeQueue, mergeRun, skipMergeRun } from "../api/repos";

vi.mock("../api/repos", () => ({
  listMergeQueue: vi.fn(),
  mergeRun: vi.fn(),
  skipMergeRun: vi.fn(),
}));

vi.mock("../api/runs", async () => {
  const actual = await vi.importActual<typeof import("../api/runs")>("../api/runs");
  return {
    ...actual,
    getRun: vi.fn(),
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  render(
    <SWRConfig value={{ dedupingInterval: 0, provider: () => new Map(), revalidateOnFocus: false }}>
      <MergeQueuePage />
    </SWRConfig>,
  );
}

describe("MergeQueuePage", () => {
  it("loads queue items, enriches runs, and supports merge and skip actions", async () => {
    const now = new Date().toISOString();
    let queueState = [
      {
        branch: "feature/proj-101",
        conflict_files: [],
        created_at: now,
        id: 1,
        priority: 2,
        run_id: "run-1",
        status: "waiting",
        updated_at: now,
      },
      {
        branch: "feature/proj-404",
        conflict_files: ["src/conflict-a.ts", "src/conflict-b.ts"],
        created_at: now,
        id: 2,
        priority: 1,
        run_id: "run-2",
        status: "conflict",
        updated_at: now,
      },
    ];

    vi.mocked(listMergeQueue).mockImplementation(async () => queueState);
    vi.mocked(getRun).mockImplementation(async (runId) => {
      if (runId === "run-2") {
        throw new Error("run missing");
      }

      return {
        created_at: now,
        current_stage: "MERGE_QUEUED",
        description: "Queue merge candidate",
        id: "run-1",
        repo_path: "C:/repo/project",
        status: "running",
        ticket: "PROJ-101",
        updated_at: now,
      };
    });
    vi.mocked(mergeRun).mockResolvedValue({ status: "queued" });
    vi.mocked(skipMergeRun).mockImplementation(async (runId) => {
      queueState = queueState.filter((item) => item.run_id !== runId);
      return { status: "skipped" };
    });

    renderPage();

    expect((await screen.findAllByText("PROJ-101")).length).toBeGreaterThan(0);
    expect(screen.getByText("run-2")).toBeInTheDocument();

    await waitFor(() => {
      expect(listMergeQueue).toHaveBeenCalled();
      expect(getRun).toHaveBeenCalledWith("run-1");
      expect(getRun).toHaveBeenCalledWith("run-2");
    });

    fireEvent.change(screen.getByLabelText("Merge priority"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Merge run-1" }));

    await waitFor(() => {
      expect(mergeRun).toHaveBeenCalledWith("run-1", 7);
    });

    fireEvent.click(screen.getByRole("button", { name: "Inspect run-2" }));
    expect(await screen.findByText("src/conflict-a.ts")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Skip run-2" }));

    await waitFor(() => {
      expect(skipMergeRun).toHaveBeenCalledWith("run-2");
    });
    await waitFor(() => {
      expect(screen.queryByText("run-2")).not.toBeInTheDocument();
    });
  });
});
