import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SegmentedControl } from "./SegmentedControl";

describe("SegmentedControl", () => {
  it("renders console segmented chrome and marks the selected option", () => {
    const onChange = vi.fn();

    render(
      <SegmentedControl
        ariaLabel="View switch"
        onChange={onChange}
        options={[
          { value: "all", label: "All" },
          { value: "active", label: "Active" },
        ]}
        value="active"
      />,
    );

    const tablist = screen.getByRole("tablist", { name: "View switch" });
    expect(tablist).toHaveAttribute("data-segmented-tone", "console");

    const selected = screen.getByRole("tab", { name: "Active" });
    expect(selected).toHaveAttribute("data-selected", "true");

    fireEvent.click(screen.getByRole("tab", { name: "All" }));
    expect(onChange).toHaveBeenCalledWith("all");
  });
});
