import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";
import { listRepos, repoBranches } from "../api/repos";
import type { Repo, RepoBranches } from "../types";
import {
  RepoRefsEditor,
  type RepoRefsEditorMode,
  type RepoRefsEditorRow,
} from "./RepoRefsEditor";

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
}));

afterEach(() => {
  vi.clearAllMocks();
});

const repoHealthy: Repo = {
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

const repoHealthyB: Repo = {
  ...repoHealthy,
  id: "repo-bbb222",
  name: "backend",
  role: "backend",
};

const repoErrored: Repo = {
  ...repoHealthy,
  id: "repo-zzz999",
  name: "infra",
  role: "infra",
  fetch_status: "error",
  last_fetched_at: null,
  last_fetch_err: "ssh: Could not resolve host",
};

const branchesMain: RepoBranches = {
  default_branch: "main",
  branches: ["main", "develop", "release/2026.04"],
};

interface HarnessProps {
  initial?: RepoRefsEditorRow[];
  mode: RepoRefsEditorMode;
  minRows?: number;
  onChangeRecord?: (rows: RepoRefsEditorRow[]) => void;
}

function Harness({ initial, mode, minRows, onChangeRecord }: HarnessProps) {
  const [rows, setRows] = useState<RepoRefsEditorRow[]>(initial ?? []);
  return (
    <RepoRefsEditor
      minRows={minRows}
      mode={mode}
      onChange={(next) => {
        setRows(next);
        onChangeRecord?.(next);
      }}
      value={rows}
    />
  );
}

function renderEditor(props: HarnessProps) {
  return render(
    <SWRConfig
      value={{
        dedupingInterval: 0,
        provider: () => new Map(),
        revalidateOnFocus: false,
      }}
    >
      <Harness {...props} />
    </SWRConfig>,
  );
}

describe("RepoRefsEditor", () => {
  it("renders one empty row in dev mode by default", async () => {
    vi.mocked(listRepos).mockResolvedValue([repoHealthy]);
    renderEditor({ mode: "dev" });

    await waitFor(() => expect(listRepos).toHaveBeenCalled());
    expect(await screen.findByText("仓库 #1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /添加仓库/ })).toBeEnabled();
  });

  it("seeds mount_name from repo.name when picking a repo", async () => {
    const onChange = vi.fn();
    vi.mocked(listRepos).mockResolvedValue([repoHealthy]);
    vi.mocked(repoBranches).mockResolvedValue(branchesMain);
    renderEditor({ mode: "dev", onChangeRecord: onChange });

    await waitFor(() => expect(listRepos).toHaveBeenCalled());

    const repoSelect = await screen.findByLabelText("仓库选择 #1");
    fireEvent.change(repoSelect, { target: { value: "repo-aaa111" } });

    await waitFor(() => {
      // Expect the harness to have observed the seeded mount_name.
      const calls = onChange.mock.calls;
      expect(calls.length).toBeGreaterThan(0);
      const last = calls[calls.length - 1][0];
      expect(last[0].mount_name).toBe("frontend");
      expect(last[0].repo_id).toBe("repo-aaa111");
    });
  });

  it("hides unhealthy repos by default and shows them disabled when toggled", async () => {
    vi.mocked(listRepos).mockResolvedValue([repoHealthy, repoErrored]);
    renderEditor({ mode: "dev" });

    await waitFor(() => expect(listRepos).toHaveBeenCalled());
    const repoSelect = (await screen.findByLabelText(
      "仓库选择 #1",
    )) as HTMLSelectElement;

    // Errored option not in DOM until toggled.
    expect(
      Array.from(repoSelect.options).map((o) => o.value),
    ).not.toContain("repo-zzz999");

    const toggle = screen.getByLabelText(/显示未健康仓库/);
    fireEvent.click(toggle);

    await waitFor(() => {
      const select = screen.getByLabelText("仓库选择 #1") as HTMLSelectElement;
      const errOpt = Array.from(select.options).find(
        (o) => o.value === "repo-zzz999",
      );
      expect(errOpt).toBeDefined();
      expect(errOpt!.disabled).toBe(true);
    });
  });

  it("flags duplicate mount_name inline", async () => {
    vi.mocked(listRepos).mockResolvedValue([repoHealthy, repoHealthyB]);
    vi.mocked(repoBranches).mockResolvedValue(branchesMain);
    renderEditor({
      mode: "dev",
      initial: [
        {
          repo_id: "repo-aaa111",
          base_branch: "main",
          mount_name: "shared",
          base_rev_lock: false,
          is_primary: false,
        },
        {
          repo_id: "repo-bbb222",
          base_branch: "main",
          mount_name: "shared",
          base_rev_lock: false,
          is_primary: false,
        },
      ],
    });

    await waitFor(() => expect(listRepos).toHaveBeenCalled());
    const dupErrors = await screen.findAllByText("mount_name 重复");
    expect(dupErrors.length).toBeGreaterThanOrEqual(1);
  });

  it("removes a row when ✕ is clicked", async () => {
    const onChange = vi.fn();
    vi.mocked(listRepos).mockResolvedValue([repoHealthy, repoHealthyB]);
    vi.mocked(repoBranches).mockResolvedValue(branchesMain);
    renderEditor({
      mode: "dev",
      onChangeRecord: onChange,
      initial: [
        {
          repo_id: "repo-aaa111",
          base_branch: "main",
          mount_name: "frontend",
          base_rev_lock: false,
          is_primary: false,
        },
        {
          repo_id: "repo-bbb222",
          base_branch: "main",
          mount_name: "backend",
          base_rev_lock: false,
          is_primary: false,
        },
      ],
    });

    await waitFor(() => expect(listRepos).toHaveBeenCalled());
    const removeBtns = await screen.findAllByRole("button", { name: /移除仓库/ });
    fireEvent.click(removeBtns[0]);

    await waitFor(() => {
      const calls = onChange.mock.calls;
      const last = calls[calls.length - 1][0];
      expect(last).toHaveLength(1);
      expect(last[0].mount_name).toBe("backend");
    });
  });

  it("design mode hides mount_name and base_rev_lock", async () => {
    vi.mocked(listRepos).mockResolvedValue([repoHealthy]);
    renderEditor({
      mode: "design",
      minRows: 1,
    });

    await waitFor(() => expect(listRepos).toHaveBeenCalled());
    expect(screen.queryByLabelText("mount_name #1")).toBeNull();
    expect(screen.queryByText(/锁定 base_rev/)).toBeNull();
  });
});
