import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppDialog } from "./AppDialog";

describe("AppDialog", () => {
  it("renders console dialog chrome and exposes stable layout hooks", () => {
    const onClose = vi.fn();

    render(
      <AppDialog
        bodyClassName="dialog-body-test"
        description="Dialog description"
        onClose={onClose}
        open
        size="wide"
        title="Dialog title"
      >
        <div>Dialog body</div>
      </AppDialog>,
    );

    const panel = document.querySelector('[data-dialog-panel="true"]');
    expect(panel).not.toBeNull();
    expect(panel).toHaveAttribute("data-dialog-tone", "console");
    expect(panel).toHaveAttribute("data-dialog-size", "wide");

    const body = document.querySelector('[data-dialog-body="true"]');
    expect(body).not.toBeNull();
    expect(body).toHaveClass("dialog-body-test");
    expect(screen.getByText("Dialog body")).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "关闭弹窗" })).toBeInTheDocument();
  });

  it("only closes through explicit dialog actions", () => {
    const onClose = vi.fn();

    render(
      <AppDialog onClose={onClose} open title="Dialog title">
        <button type="button">Body action</button>
      </AppDialog>,
    );

    const backdrop = document.querySelector('[data-dialog-backdrop="true"]');
    expect(backdrop).not.toBeNull();

    fireEvent.click(backdrop!);
    fireEvent.mouseDown(document.body);
    fireEvent.mouseUp(document.body);
    fireEvent.click(document.body);

    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Body action" }));

    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "关闭弹窗" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
