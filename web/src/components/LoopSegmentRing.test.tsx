import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LoopSegmentRing } from "./LoopSegmentRing";

describe("LoopSegmentRing", () => {
  it("renders one segment per configured maximum", () => {
    render(<LoopSegmentRing completed={2} label="DevWork 轮次" max={5} value={2} />);

    expect(screen.getByRole("img", { name: "DevWork 轮次 2/5，已记录" })).toBeInTheDocument();
    expect(screen.getAllByTestId("loop-segment")).toHaveLength(5);
  });

  it("marks completed, current, and max-reached segments with distinct states", () => {
    const { rerender } = render(
      <LoopSegmentRing active completed={2} label="DevWork 轮次" max={4} value={3} />,
    );

    expect(screen.getAllByTestId("loop-segment").map((node) => node.getAttribute("data-state"))).toEqual([
      "complete",
      "complete",
      "current",
      "pending",
    ]);

    rerender(<LoopSegmentRing completed={4} label="DevWork 轮次" max={4} maxReached value={4} />);

    expect(screen.getAllByTestId("loop-segment").map((node) => node.getAttribute("data-state"))).toEqual([
      "complete",
      "complete",
      "complete",
      "maxed",
    ]);
  });
});
