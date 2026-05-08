import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DesignWorkStateProgress } from "./DesignWorkStateProgress";
import { DevWorkStepProgress } from "./DevWorkStepProgress";

describe("work progress steppers", () => {
  it("highlights the active DesignWork state while running", () => {
    render(<DesignWorkStateProgress active current="LLM_GENERATE" />);

    const activeStep = screen.getByLabelText("LLM_GENERATE");
    expect(activeStep).toHaveAttribute("data-state", "active");
    expect(activeStep).toHaveClass("progress-step-active");
  });

  it("highlights the final DesignWork segment when completed", () => {
    render(<DesignWorkStateProgress current="COMPLETED" />);

    expect(screen.getByLabelText("COMPLETED")).toHaveAttribute("data-state", "done");
  });

  it("highlights the active DevWork step while running", () => {
    render(<DevWorkStepProgress active current="STEP4_DEVELOP" />);

    const activeStep = screen.getByLabelText("STEP4_DEVELOP");
    expect(activeStep).toHaveAttribute("data-state", "active");
    expect(activeStep).toHaveClass("progress-step-active");
  });

  it("highlights the final DevWork segment when completed", () => {
    render(<DevWorkStepProgress current="COMPLETED" />);

    expect(screen.getByLabelText("COMPLETED")).toHaveAttribute("data-state", "done");
  });
});
