import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import {
  createRepo,
  deleteRepo,
  fetchRepo,
  listRepoPage,
  syncRepos,
} from "../api/repos";
import type { Repo, RepoPage } from "../types";
import { ReposPage } from "./ReposPage";

vi.mock("../api/repos", () => ({
  listRepos: vi.fn(),
  listRepoPage: vi.fn(),
  getRepo: vi.fn(),
  createRepo: vi.fn(),
  updateRepo: vi.fn(),
  deleteRepo: vi.fn(),
  syncRepos: vi.fn(),
  fetchRepo: vi.fn(),
  repoBranches: vi.fn(),
  repoTree: vi.fn(),
  repoBlob: vi.fn(),
  repoLog: vi.fn(),
  repoLogPage: vi.fn(),
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
    <SWRConfig
      value={{
        dedupingInterval: 0,
        provider: () => new Map(),
        revalidateOnFocus: false,
      }}
    >
      <MemoryRouter>
        <ReposPage />
      </MemoryRouter>
    </SWRConfig>,
  );
}

const repoA: Repo = {
  id: "repo-aaa111",
  name: "frontend",
  url: "git@github.com:org/frontend.git",
  default_branch: "main",
  ssh_key_path: null,
  bare_clone_path: null,
  role: "frontend",
  fetch_status: "healthy",
  last_fetched_at: "2026-04-26T12:00:14Z",
  last_fetch_err: null,
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-26T12:00:14Z",
};

const repoB: Repo = {
  id: "repo-bbb222",
  name: "backend",
  url: "git@github.com:org/backend.git",
  default_branch: "main",
  ssh_key_path: null,
  bare_clone_path: null,
  role: "backend",
  fetch_status: "error",
  last_fetched_at: null,
  last_fetch_err: "ssh: Could not resolve host",
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-26T12:00:14Z",
};

const page: RepoPage = {
  items: [repoA, repoB],
  pagination: { limit: 12, offset: 0, total: 2, has_more: false },
};

describe("ReposPage", () => {
  it("renders repos from the paginated API", async () => {
    vi.mocked(listRepoPage).mockResolvedValue(page);
    renderPage();
    expect(await screen.findByText("git@github.com:org/frontend.git")).toBeInTheDocument();
    expect(screen.getByText("git@github.com:org/backend.git")).toBeInTheDocument();
    expect(screen.getByText("ssh: Could not resolve host")).toBeInTheDocument();
  });

  it("creates a repo and refreshes the list", async () => {
    vi.mocked(listRepoPage).mockResolvedValue({ items: [], pagination: { limit: 12, offset: 0, total: 0, has_more: false } });
    vi.mocked(createRepo).mockResolvedValue({
      ...repoA,
      id: "repo-new",
      name: "new-repo",
    });

    renderPage();
    await waitFor(() => expect(listRepoPage).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "登记仓库" }));
    fireEvent.change(screen.getByLabelText("名称"), {
      target: { value: "new-repo" },
    });
    fireEvent.change(screen.getByLabelText("URL"), {
      target: { value: "git@github.com:org/new.git" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登记" }));

    await waitFor(() => {
      expect(createRepo).toHaveBeenCalledWith({
        name: "new-repo",
        url: "git@github.com:org/new.git",
        default_branch: "main",
        role: "backend",
        ssh_key_path: null,
      });
    });
  });

  it("shows inline validation error for invalid repo name", async () => {
    vi.mocked(listRepoPage).mockResolvedValue({ items: [], pagination: { limit: 12, offset: 0, total: 0, has_more: false } });
    renderPage();
    await waitFor(() => expect(listRepoPage).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "登记仓库" }));
    fireEvent.change(screen.getByLabelText("名称"), {
      target: { value: "-bad" },
    });
    fireEvent.change(screen.getByLabelText("URL"), {
      target: { value: "git@github.com:org/x.git" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登记" }));

    expect(await screen.findByText(/必须以字母或数字开头/)).toBeInTheDocument();
    expect(createRepo).not.toHaveBeenCalled();
  });

  it("triggers an immediate fetch when the row button is clicked", async () => {
    vi.mocked(listRepoPage).mockResolvedValue({ items: [repoA], pagination: { limit: 12, offset: 0, total: 1, has_more: false } });
    vi.mocked(fetchRepo).mockResolvedValue({
      outcome: "fetched",
      fetch_status: "healthy",
      last_fetched_at: "2026-04-26T13:00:00Z",
    });

    renderPage();
    const fetchBtn = await screen.findByRole("button", { name: /立即 fetch/ });
    fireEvent.click(fetchBtn);

    await waitFor(() => {
      expect(fetchRepo).toHaveBeenCalledWith("repo-aaa111");
    });
  });

  it("confirms before delete and surfaces 409 error", async () => {
    vi.mocked(listRepoPage).mockResolvedValue({ items: [repoA], pagination: { limit: 12, offset: 0, total: 1, has_more: false } });
    vi.mocked(deleteRepo).mockRejectedValue(
      new ApiError(409, "still referenced by DevWork", null),
    );
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    renderPage();
    const deleteBtn = await screen.findByRole("button", { name: /删除/ });
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(deleteRepo).toHaveBeenCalledWith("repo-aaa111");
    });
    expect(await screen.findByText("still referenced by DevWork")).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("syncs repos and shows the report counts", async () => {
    vi.mocked(listRepoPage).mockResolvedValue({ items: [repoA], pagination: { limit: 12, offset: 0, total: 1, has_more: false } });
    vi.mocked(syncRepos).mockResolvedValue({
      in_sync: ["repo-aaa111"],
      fs_only: ["new-from-yaml"],
      db_only: [],
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    renderPage();
    await waitFor(() => expect(listRepoPage).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /同步配置/ }));

    await waitFor(() => expect(syncRepos).toHaveBeenCalled());
    expect(await screen.findByText(/一致 1 \/ 仅文件 1 \/ 仅数据库 0/)).toBeInTheDocument();
    confirmSpy.mockRestore();
  });
});
