import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StageProgress } from "./StageProgress";

describe("StageProgress", () => {
  it("renders the 14-step bar and highlights the current stage", () => {
    render(<StageProgress stage="DEV_RUNNING" />);

    const segments = screen.getAllByRole("listitem");
    expect(segments).toHaveLength(14);
    expect(screen.getByLabelText("DEV_RUNNING")).toHaveAttribute("data-state", "current");
    expect(screen.getByLabelText("REQ_COLLECTING")).toHaveAttribute("data-state", "complete");
  });
});
