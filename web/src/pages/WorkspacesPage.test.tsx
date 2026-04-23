import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { archiveWorkspace, createWorkspace, listWorkspaces } from "../api/workspaces";
import { WorkspacesPage } from "./WorkspacesPage";
import type { Workspace } from "../types";

vi.mock("../api/workspaces", () => ({
  listWorkspaces: vi.fn(),
  createWorkspace: vi.fn(),
  archiveWorkspace: vi.fn(),
  syncWorkspaces: vi.fn(),
  getWorkspace: vi.fn(),
}));

const navigateMock = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigateMock };
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  render(
    <SWRConfig value={{ dedupingInterval: 0, provider: () => new Map(), revalidateOnFocus: false }}>
      <MemoryRouter>
        <WorkspacesPage />
      </MemoryRouter>
    </SWRConfig>,
  );
}

const workspace: Workspace = {
  id: "ws-1",
  title: "Test Workspace",
  slug: "test-workspace",
  status: "active",
  root_path: "/workspaces/test-workspace",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-04-23T00:00:00Z",
};

describe("WorkspacesPage", () => {
  it("renders workspaces from the API", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([workspace]);
    renderPage();
    expect(await screen.findByText("Test Workspace")).toBeInTheDocument();
    expect(screen.getByText("test-workspace")).toBeInTheDocument();
  });

  it("creates a workspace and navigates to its detail page", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([]);
    vi.mocked(createWorkspace).mockResolvedValue({ ...workspace, id: "ws-new" });

    renderPage();
    await waitFor(() => expect(listWorkspaces).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "New WS" } });
    fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "new-ws" } });
    fireEvent.click(screen.getByRole("button", { name: "新建 Workspace" }));

    await waitFor(() => {
      expect(createWorkspace).toHaveBeenCalledWith({ title: "New WS", slug: "new-ws" });
    });
    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith("/workspaces/ws-new");
    });
  });

  it("shows inline validation error for invalid slug", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(listWorkspaces).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "Bad" } });
    fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "--nope--" } });
    fireEvent.click(screen.getByRole("button", { name: "新建 Workspace" }));

    expect(await screen.findByText(/kebab-case/)).toBeInTheDocument();
    expect(createWorkspace).not.toHaveBeenCalled();
  });

  it("calls archiveWorkspace when the archive button is confirmed", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([workspace]);
    vi.mocked(archiveWorkspace).mockResolvedValue();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    renderPage();
    const archiveBtn = await screen.findByRole("button", { name: "归档" });
    fireEvent.click(archiveBtn);

    await waitFor(() => {
      expect(archiveWorkspace).toHaveBeenCalledWith("ws-1");
    });
    confirmSpy.mockRestore();
  });
});
