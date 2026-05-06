import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmptyState, MetricCard, SectionPanel } from "./SectionPanel";

describe("SectionPanel", () => {
  it("renders console panel hooks for sections, cards, and empty states", () => {
    render(
      <div>
        <SectionPanel kicker="Directory" title="Workspace registry">
          content
        </SectionPanel>
        <MetricCard label="Status" value="Running" />
        <EmptyState copy="Nothing here yet" />
      </div>,
    );

    expect(screen.getByText("Workspace registry").closest("section")).toHaveAttribute(
      "data-panel-tone",
      "console",
    );
    expect(screen.getByText("Running").closest("div")).toHaveAttribute(
      "data-card-tone",
      "console",
    );
    expect(screen.getByText("Nothing here yet")).toHaveAttribute("data-empty-tone", "console");
  });
});
