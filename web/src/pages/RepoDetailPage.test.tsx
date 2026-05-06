import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchRepo,
  getRepo,
  repoBlob,
  repoBranches,
  repoLogPage,
  repoTree,
} from "../api/repos";
import type { Repo, RepoBlob, RepoBranches, RepoLogPage, RepoTree } from "../types";
import { RepoDetailPage } from "./RepoDetailPage";

vi.mock("../api/repos", () => ({
  listRepos: vi.fn(),
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

afterEach(() => {
  vi.clearAllMocks();
});

const repo: Repo = {
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

const branches: RepoBranches = {
  default_branch: "main",
  branches: ["main", "feat/x"],
};

const treeRoot: RepoTree = {
  ref: "main",
  path: "",
  truncated: false,
  entries: [
    { path: "src", type: "tree", mode: "040000", size: null },
    { path: "README.md", type: "blob", mode: "100644", size: 1234 },
  ],
};

const treeSrc: RepoTree = {
  ref: "main",
  path: "src",
  truncated: false,
  entries: [
    { path: "src/App.tsx", type: "blob", mode: "100644", size: 800 },
  ],
};

function renderAt(initialPath = "/repos/repo-aaa111") {
  render(
    <SWRConfig
      value={{
        dedupingInterval: 0,
        provider: () => new Map(),
        revalidateOnFocus: false,
      }}
    >
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/repos/:repoId" element={<RepoDetailPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  );
}

describe("RepoDetailPage", () => {
  it("renders header with name + role + fetch_status badge", async () => {
    vi.mocked(getRepo).mockResolvedValue(repo);
    vi.mocked(repoBranches).mockResolvedValue(branches);
    vi.mocked(repoTree).mockResolvedValue(treeRoot);

    renderAt();

    // Repo name renders as the SectionPanel heading; "frontend" also appears
    // as the role value in the metadata row, so match the heading explicitly.
    expect(
      await screen.findByRole("heading", { name: "frontend" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/角色：/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("loads tree at default_branch and shows entries", async () => {
    vi.mocked(getRepo).mockResolvedValue(repo);
    vi.mocked(repoBranches).mockResolvedValue(branches);
    vi.mocked(repoTree).mockResolvedValue(treeRoot);

    renderAt();

    expect(await screen.findByText("README.md")).toBeInTheDocument();
    expect(screen.getByText("src/")).toBeInTheDocument();
    await waitFor(() => {
      expect(repoTree).toHaveBeenCalledWith("repo-aaa111", {
        ref: "main",
        path: "",
        depth: 1,
      });
    });
  });

  it("clicks a folder, breadcrumb updates, repoTree called with new path", async () => {
    vi.mocked(getRepo).mockResolvedValue(repo);
    vi.mocked(repoBranches).mockResolvedValue(branches);
    vi.mocked(repoTree).mockImplementation(async (_id, params) =>
      params.path === "src" ? treeSrc : treeRoot,
    );

    renderAt();

    const folderBtn = await screen.findByRole("button", { name: /src\// });
    fireEvent.click(folderBtn);

    await waitFor(() => {
      expect(repoTree).toHaveBeenCalledWith("repo-aaa111", {
        ref: "main",
        path: "src",
        depth: 1,
      });
    });
    expect(await screen.findByText("App.tsx")).toBeInTheDocument();
  });

  it("clicks a file, BlobViewer renders content", async () => {
    vi.mocked(getRepo).mockResolvedValue(repo);
    vi.mocked(repoBranches).mockResolvedValue(branches);
    vi.mocked(repoTree).mockResolvedValue(treeRoot);
    const blob: RepoBlob = {
      ref: "main",
      path: "README.md",
      size: 12,
      binary: false,
      content: "# Hello World\n",
    };
    vi.mocked(repoBlob).mockResolvedValue(blob);

    renderAt();

    const fileBtn = await screen.findByRole("button", { name: /README\.md/ });
    fireEvent.click(fileBtn);

    await waitFor(() => {
      expect(repoBlob).toHaveBeenCalledWith("repo-aaa111", {
        ref: "main",
        path: "README.md",
      });
    });
  });

  it("renders binary placeholder, no <pre>", async () => {
    vi.mocked(getRepo).mockResolvedValue(repo);
    vi.mocked(repoBranches).mockResolvedValue(branches);
    vi.mocked(repoTree).mockResolvedValue({
      ref: "main",
      path: "",
      truncated: false,
      entries: [
        { path: "logo.png", type: "blob", mode: "100644", size: 8200 },
      ],
    });
    vi.mocked(repoBlob).mockResolvedValue({
      ref: "main",
      path: "logo.png",
      size: 8200,
      binary: true,
      content: null,
    });

    renderAt();
    fireEvent.click(await screen.findByRole("button", { name: /logo\.png/ }));

    expect(
      await screen.findByText("二进制文件，暂不支持预览。"),
    ).toBeInTheDocument();
    expect(document.querySelector("pre")).toBeNull();
  });

  it("escapes script tags in highlighted blob", async () => {
    vi.mocked(getRepo).mockResolvedValue(repo);
    vi.mocked(repoBranches).mockResolvedValue(branches);
    vi.mocked(repoTree).mockResolvedValue({
      ref: "main",
      path: "",
      truncated: false,
      entries: [
        { path: "evil.html", type: "blob", mode: "100644", size: 32 },
      ],
    });
    vi.mocked(repoBlob).mockResolvedValue({
      ref: "main",
      path: "evil.html",
      size: 32,
      binary: false,
      content: "<script>alert(1)</script>",
    });

    renderAt();
    fireEvent.click(await screen.findByRole("button", { name: /evil\.html/ }));

    await waitFor(() => expect(repoBlob).toHaveBeenCalled());
    // The literal text content should contain "script" but no real <script>
    // element should exist inside the rendered blob viewer.
    const scripts = document.querySelectorAll("article script");
    expect(scripts.length).toBe(0);
  });

  it("log tab calls repoLogPage(limit=20, offset=0)", async () => {
    vi.mocked(getRepo).mockResolvedValue(repo);
    vi.mocked(repoBranches).mockResolvedValue(branches);
    vi.mocked(repoTree).mockResolvedValue(treeRoot);
    const log: RepoLogPage = {
      ref: "main",
      path: null,
      items: [
        {
          sha: "abcdef0123456789abcdef0123456789abcdef01",
          author: "Alice",
          email: "alice@example.com",
          committed_at: "2026-04-26T10:00:00Z",
          subject: "feat: add things",
        },
      ],
      pagination: {
        limit: 20,
        offset: 0,
        total: 1,
        has_more: false,
      },
    };
    vi.mocked(repoLogPage).mockResolvedValue(log);

    renderAt();
    await screen.findByText("README.md");

    fireEvent.click(screen.getByRole("tab", { name: "提交历史" }));

    await waitFor(() => {
      expect(repoLogPage).toHaveBeenCalledWith("repo-aaa111", {
        ref: "main",
        limit: 20,
        offset: 0,
      });
    });
    expect(await screen.findByText("feat: add things")).toBeInTheDocument();
  });

  it("fetch button calls fetchRepo and refreshes", async () => {
    vi.mocked(getRepo).mockResolvedValue(repo);
    vi.mocked(repoBranches).mockResolvedValue(branches);
    vi.mocked(repoTree).mockResolvedValue(treeRoot);
    vi.mocked(fetchRepo).mockResolvedValue({
      outcome: "fetched",
      fetch_status: "healthy",
      last_fetched_at: "2026-04-26T13:00:00Z",
    });

    renderAt();
    const btn = await screen.findByRole("button", { name: /立即 fetch/ });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(fetchRepo).toHaveBeenCalledWith("repo-aaa111");
    });
  });
});
