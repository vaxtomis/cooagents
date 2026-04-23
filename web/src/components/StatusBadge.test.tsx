import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("maps running to success tone", () => {
    render(<StatusBadge status="running" />);
    const badge = screen.getByRole("status", { name: "运行中" });
    expect(badge).toHaveAttribute("data-tone", "success");
    expect(badge).toHaveClass("bg-success/10", "text-success");
  });

  it("maps STEP5_REVIEW (uppercase enum) to a warning label", () => {
    render(<StatusBadge status="STEP5_REVIEW" />);
    const badge = screen.getByRole("status", { name: "Step5 评审" });
    expect(badge).toHaveAttribute("data-tone", "warning");
  });

  it("maps archived workspace status", () => {
    render(<StatusBadge status="archived" />);
    const badge = screen.getByRole("status", { name: "已归档" });
    expect(badge).toHaveAttribute("data-tone", "muted");
  });

  it("falls through to muted for unknown status", () => {
    render(<StatusBadge status="totally-unknown" />);
    const badge = screen.getByRole("status", { name: "totally-unknown" });
    expect(badge).toHaveAttribute("data-tone", "muted");
  });
});
