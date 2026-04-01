import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { getRun } from "../api/runs";
import {
  getRunConflicts,
  listMergeQueue,
  mergeRun,
  resolveRunConflict,
  skipMergeRun,
} from "../api/repos";
import { MergeQueuePage } from "./MergeQueuePage";

vi.mock("../api/repos", () => ({
  getRunConflicts: vi.fn(),
  listMergeQueue: vi.fn(),
  mergeRun: vi.fn(),
  resolveRunConflict: vi.fn(),
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
  it("loads queue items, enriches conflicts, and supports merge, skip, and resolve-requeue actions", async () => {
    const now = new Date().toISOString();
    let runState = {
      "run-1": {
        created_at: now,
        current_stage: "MERGE_QUEUED",
        description: "Queue merge candidate",
        id: "run-1",
        repo_path: "C:/repo/project",
        status: "running",
        ticket: "PROJ-101",
        updated_at: now,
      },
      "run-2": {
        created_at: now,
        current_stage: "MERGE_CONFLICT",
        description: "Conflicted merge candidate",
        id: "run-2",
        repo_path: "C:/repo/project",
        status: "running",
        ticket: "PROJ-404",
        updated_at: now,
      },
    };
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
        conflict_files: ["src/fallback-conflict.ts"],
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
      const run = runState[runId as keyof typeof runState];
      if (!run) {
        throw new Error("run missing");
      }
      return run;
    });
    vi.mocked(getRunConflicts).mockResolvedValue({
      conflicts: ["src/live-conflict-a.ts", "src/live-conflict-b.ts"],
    });
    vi.mocked(mergeRun).mockResolvedValue({ status: "queued" });
    vi.mocked(skipMergeRun).mockImplementation(async (runId) => {
      queueState = queueState.filter((item) => item.run_id !== runId);
      return { status: "skipped" };
    });
    vi.mocked(resolveRunConflict).mockImplementation(async (runId) => {
      queueState = queueState.map((item) =>
        item.run_id === runId
          ? {
              ...item,
              conflict_files: [],
              status: "waiting",
            }
          : item,
      );
      runState = {
        ...runState,
        "run-2": {
          ...runState["run-2"],
          current_stage: "MERGE_QUEUED",
        },
      };
      return { status: "requeued" };
    });

    renderPage();

    expect((await screen.findAllByText("PROJ-101")).length).toBeGreaterThan(0);
    expect(screen.getByText("PROJ-404")).toBeInTheDocument();

    await waitFor(() => {
      expect(listMergeQueue).toHaveBeenCalled();
      expect(getRun).toHaveBeenCalledWith("run-1");
      expect(getRun).toHaveBeenCalledWith("run-2");
    });

    fireEvent.change(screen.getByLabelText("合并优先级"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "合并 run-1" }));

    await waitFor(() => {
      expect(mergeRun).toHaveBeenCalledWith("run-1", 7);
    });

    fireEvent.click(screen.getByRole("button", { name: "查看 run-2" }));

    await waitFor(() => {
      expect(getRunConflicts).toHaveBeenCalledWith("run-2");
    });

    expect(await screen.findByText("检测到冲突")).toBeInTheDocument();
    expect(await screen.findByText("src/live-conflict-a.ts")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "解决冲突并重新入队 run-2" }));

    await waitFor(() => {
      expect(resolveRunConflict).toHaveBeenCalledWith("run-2", "dashboard");
    });
    expect(await screen.findByText("Requeued run-2")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText("检测到冲突")).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "跳过 run-2" }));

    await waitFor(() => {
      expect(skipMergeRun).toHaveBeenCalledWith("run-2");
    });
    await waitFor(() => {
      expect(screen.queryByText("PROJ-404")).not.toBeInTheDocument();
    });
  });

  it("falls back to queue conflict files and surfaces resolve conflicts from the backend", async () => {
    const now = new Date().toISOString();

    vi.mocked(listMergeQueue).mockResolvedValue([
      {
        branch: "feature/proj-777",
        conflict_files: ["src/fallback-a.ts", "src/fallback-b.ts"],
        created_at: now,
        id: 7,
        priority: 3,
        run_id: "run-7",
        status: "conflict",
        updated_at: now,
      },
    ]);
    vi.mocked(getRun).mockResolvedValue({
      created_at: now,
      current_stage: "MERGE_CONFLICT",
      description: "Conflicted merge candidate",
      id: "run-7",
      repo_path: "C:/repo/project",
      status: "running",
      ticket: "PROJ-777",
      updated_at: now,
    });
    vi.mocked(getRunConflicts).mockRejectedValue(new Error("detail fetch failed"));
    vi.mocked(resolveRunConflict).mockRejectedValue(
      new ApiError(409, "Wrong stage", {
        current_stage: "MERGING",
        error: "conflict",
        message: "Wrong stage",
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(getRunConflicts).toHaveBeenCalledWith("run-7");
    });

    expect(await screen.findByText("src/fallback-a.ts")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试冲突详情" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "解决冲突并重新入队 run-7" }));

    await waitFor(() => {
      expect(resolveRunConflict).toHaveBeenCalledWith("run-7", "dashboard");
    });
    expect(await screen.findByText("Wrong stage (current stage: MERGING)")).toBeInTheDocument();
  });
});
