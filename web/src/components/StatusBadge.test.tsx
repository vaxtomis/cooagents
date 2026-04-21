import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { approveRun, rejectRun } from "../api/runs";
import { ApprovalAction } from "./ApprovalAction";
import { StatusBadge } from "./StatusBadge";

vi.mock("../api/runs", () => ({
  approveRun: vi.fn(),
  rejectRun: vi.fn(),
}));

afterEach(() => {
  vi.clearAllMocks();
});

describe("StatusBadge", () => {
  it("maps approved statuses to the expected label and tone", () => {
    render(<StatusBadge status="running" />);

    const badge = screen.getByRole("status", { name: "运行中" });
    expect(badge).toHaveAttribute("data-tone", "success");
    expect(badge).toHaveClass("bg-success/10", "text-success");
  });
});

describe("ApprovalAction", () => {
  it("triggers the correct approve and reject API calls", async () => {
    vi.mocked(approveRun).mockResolvedValue({ ok: true } as never);
    vi.mocked(rejectRun).mockResolvedValue({ ok: true } as never);

    render(
      <ApprovalAction
        gate="req"
        reason="Needs more detail"
        runId="run-1"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    await waitFor(() => {
      expect(approveRun).toHaveBeenCalledWith("run-1", {
        comment: undefined,
        gate: "req",
      });
    });

    fireEvent.click(screen.getByRole("button", { name: "驳回" }));
    await waitFor(() => {
      expect(rejectRun).toHaveBeenCalledWith("run-1", {
        gate: "req",
        reason: "Needs more detail",
      });
    });
  });
});
