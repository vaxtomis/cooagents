import { useMemo, useState } from "react";
import useSWR from "swr";
import { Lock, Plus, X } from "lucide-react";
import { listRepos, repoBranches } from "../api/repos";
import { extractError } from "../lib/extractError";
import type { DevRepoRef, Repo } from "../types";

// Phase 7 form-side picker. Distinct from `pages/repo/BranchPicker.tsx`
// (which allows free-text for inspector navigation): here `<select>`-only
// forces operators onto the Phase 3 inspector's canonical branch list,
// which is the whole reason this PRD exists.

export type RepoRefsEditorMode = "design" | "dev";

// Internal row carries the DevWork superset; design-mode parents project to
// `{ repo_id, base_branch }` on submit.
export type RepoRefsEditorRow = DevRepoRef;

// Mirrors src/models.py::_REPO_NAME_PATTERN. Exported so the parent form
// validator (WorkspaceDetailPage) can reuse the same source of truth and
// not drift from the editor's inline error.
export const MOUNT_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$/;

interface Props {
  mode: RepoRefsEditorMode;
  value: RepoRefsEditorRow[];
  onChange: (next: RepoRefsEditorRow[]) => void;
  // Number of always-rendered placeholder rows. dev:1, design:0 by default.
  // Kept editor-internal so the parent never has to seed a row manually.
  minRows?: number;
}

interface RowEditorProps {
  row: RepoRefsEditorRow;
  index: number;
  mode: RepoRefsEditorMode;
  repos: Repo[];
  duplicate: boolean;
  invalidMount: boolean;
  onPatch: (patch: Partial<RepoRefsEditorRow>) => void;
  onRemove: () => void;
  removable: boolean;
}

function emptyRow(): RepoRefsEditorRow {
  return {
    repo_id: "",
    base_branch: "",
    mount_name: "",
    base_rev_lock: false,
    is_primary: false,
  };
}

export function RepoRefsEditor({ mode, value, onChange, minRows }: Props) {
  const reposQuery = useSWR(["repos"], listRepos, { revalidateOnFocus: false });
  const allRepos = useMemo(() => reposQuery.data ?? [], [reposQuery.data]);
  const healthyRepos = useMemo(
    () => allRepos.filter((r) => r.fetch_status === "healthy"),
    [allRepos],
  );

  const [showUnhealthy, setShowUnhealthy] = useState(false);

  const minRowCount = minRows ?? (mode === "dev" ? 1 : 0);

  // Collapse value into render rows: append empties so we always render
  // at least `minRowCount` rows, but never mutate `value` directly.
  const renderedRows = useMemo<RepoRefsEditorRow[]>(() => {
    if (value.length >= minRowCount) return value;
    const padding: RepoRefsEditorRow[] = [];
    for (let i = value.length; i < minRowCount; i += 1) padding.push(emptyRow());
    return [...value, ...padding];
  }, [value, minRowCount]);

  // Mount uniqueness — only meaningful in dev mode where mount_name exists.
  const duplicateMounts = useMemo(() => {
    if (mode !== "dev") return new Set<string>();
    const seen = new Map<string, number>();
    for (const row of renderedRows) {
      const m = row.mount_name.trim();
      if (!m) continue;
      seen.set(m, (seen.get(m) ?? 0) + 1);
    }
    return new Set(
      [...seen.entries()].filter(([, count]) => count > 1).map(([m]) => m),
    );
  }, [renderedRows, mode]);

  // Note: any mutating action (patch/add/remove) commits all of `renderedRows`
  // — including padding empties — back to the parent. The parent's submit
  // guard handles the "user never touched the form" case via length===0.
  function patchRow(index: number, patch: Partial<RepoRefsEditorRow>) {
    const next = renderedRows.map((row, i) => {
      if (i !== index) return row;
      const merged: RepoRefsEditorRow = { ...row, ...patch };
      // Auto-seed mount_name from repo.name when picking a repo for the first
      // time, but only in dev mode (design mode has no mount_name UI).
      if (mode === "dev" && patch.repo_id && !row.mount_name) {
        const repo = allRepos.find((r) => r.id === patch.repo_id);
        if (repo) merged.mount_name = repo.name;
      }
      // Switching repos invalidates the previously chosen branch — the new
      // repo's <select> would otherwise show a stale option that the
      // backend's validate_*_repo_refs would 400 on.
      if (
        patch.repo_id !== undefined &&
        patch.repo_id !== row.repo_id &&
        patch.base_branch === undefined
      ) {
        merged.base_branch = "";
      }
      return merged;
    });
    onChange(next);
  }

  function addRow() {
    onChange([...renderedRows, emptyRow()]);
  }

  function removeRow(index: number) {
    onChange(renderedRows.filter((_, i) => i !== index));
  }

  const hiddenCount = allRepos.length - healthyRepos.length;

  return (
    <div className="space-y-3 rounded-2xl border border-border bg-panel-strong/40 p-3">
      {reposQuery.isLoading ? (
        <p className="text-xs text-muted">加载仓库列表...</p>
      ) : null}
      {reposQuery.error ? (
        <p className="text-xs text-danger">
          {extractError(reposQuery.error, "加载仓库失败")}
        </p>
      ) : null}
      {!reposQuery.isLoading && !reposQuery.error && allRepos.length === 0 ? (
        <p className="text-xs text-muted">
          未注册任何仓库。请先在 <a className="underline hover:text-copy" href="/repos">/repos</a> 注册后再返回。
        </p>
      ) : null}

      <ul className="space-y-2">
        {renderedRows.map((row, index) => (
          <li key={index}>
            <RowEditor
              duplicate={
                mode === "dev" &&
                row.mount_name.trim() !== "" &&
                duplicateMounts.has(row.mount_name.trim())
              }
              index={index}
              invalidMount={
                mode === "dev" &&
                row.mount_name.trim() !== "" &&
                !MOUNT_NAME_RE.test(row.mount_name.trim())
              }
              mode={mode}
              onPatch={(patch) => patchRow(index, patch)}
              onRemove={() => removeRow(index)}
              removable={renderedRows.length > minRowCount}
              repos={showUnhealthy ? allRepos : healthyRepos}
              row={row}
            />
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <button
          className="inline-flex items-center gap-1.5 rounded-lg border border-border-strong bg-panel-strong/60 px-3 py-1.5 text-xs text-muted transition hover:border-accent/40 hover:text-accent disabled:opacity-50"
          disabled={allRepos.length === 0}
          onClick={addRow}
          type="button"
        >
          <Plus className="size-3.5" />
          添加仓库
        </button>
        {hiddenCount > 0 ? (
          <label className="flex items-center gap-2 text-[11px] text-muted-soft">
            <input
              checked={showUnhealthy}
              onChange={(event) => setShowUnhealthy(event.target.checked)}
              type="checkbox"
            />
            <span>显示未健康仓库（当前隐藏 {hiddenCount} 个）</span>
          </label>
        ) : null}
      </div>
    </div>
  );
}

function RowEditor({
  row,
  index,
  mode,
  repos,
  duplicate,
  invalidMount,
  onPatch,
  onRemove,
  removable,
}: RowEditorProps) {
  const branchesQuery = useSWR(
    row.repo_id ? ["repo-branches", row.repo_id] : null,
    () => repoBranches(row.repo_id),
    { revalidateOnFocus: false },
  );
  const branches = branchesQuery.data?.branches ?? [];

  return (
    <div className="space-y-2 rounded-xl border border-border bg-panel-strong/70 p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] uppercase tracking-[0.2em] text-muted-soft">
          仓库 #{index + 1}
        </p>
        {removable ? (
          <button
            aria-label={`移除仓库 #${index + 1}`}
            className="inline-flex items-center gap-1 rounded-md border border-border-strong bg-panel-strong/40 px-2 py-1 text-[11px] text-muted transition hover:border-danger/40 hover:text-danger"
            onClick={onRemove}
            type="button"
          >
            <X className="size-3" />
            移除
          </button>
        ) : null}
      </div>

      <label className="block space-y-1 text-xs text-muted">
        <span>repo</span>
        <select
          aria-label={`仓库选择 #${index + 1}`}
          className="w-full rounded-xl border border-border-strong bg-panel-strong px-3 py-2 text-sm text-copy outline-none [&_option]:bg-panel-strong"
          onChange={(event) => onPatch({ repo_id: event.target.value })}
          value={row.repo_id}
        >
          <option value="">请选择仓库</option>
          {repos.map((repo) => {
            const unhealthy = repo.fetch_status !== "healthy";
            const suffix = unhealthy ? ` (${repo.fetch_status})` : "";
            return (
              <option disabled={unhealthy} key={repo.id} value={repo.id}>
                {repo.name}
                {suffix}
              </option>
            );
          })}
        </select>
      </label>

      <div className="grid gap-2 md:grid-cols-2">
        <label className="space-y-1 text-xs text-muted">
          <span>base_branch</span>
          <select
            aria-label={`base_branch #${index + 1}`}
            className="w-full rounded-xl border border-border-strong bg-panel-strong px-3 py-2 text-sm text-copy outline-none disabled:opacity-50 [&_option]:bg-panel-strong"
            disabled={!row.repo_id || branchesQuery.isLoading || !!branchesQuery.error}
            onChange={(event) => onPatch({ base_branch: event.target.value })}
            value={row.base_branch}
          >
            <option value="">
              {row.repo_id
                ? branchesQuery.isLoading
                  ? "加载分支中..."
                  : "请选择分支"
                : "请先选择仓库"}
            </option>
            {branches.map((branch) => (
              <option key={branch} value={branch}>
                {branch}
              </option>
            ))}
          </select>
          {branchesQuery.error ? (
            <span className="block text-[11px] text-danger">
              {extractError(branchesQuery.error, "分支加载失败")}
            </span>
          ) : null}
        </label>

        {mode === "dev" ? (
          <label className="space-y-1 text-xs text-muted">
            <span>mount_name</span>
            <input
              aria-label={`mount_name #${index + 1}`}
              className="w-full rounded-xl border border-border-strong bg-panel px-3 py-2 font-mono text-sm text-copy outline-none"
              onChange={(event) => onPatch({ mount_name: event.target.value })}
              placeholder="frontend"
              value={row.mount_name}
            />
            {duplicate ? (
              <span className="block text-[11px] text-danger">
                mount_name 重复
              </span>
            ) : null}
            {invalidMount && !duplicate ? (
              <span className="block text-[11px] text-danger">
                mount_name 必须匹配 [A-Za-z0-9][A-Za-z0-9_.-]{"{0,62}"}
              </span>
            ) : null}
          </label>
        ) : null}
      </div>

      {mode === "dev" ? (
        <label className="flex items-center gap-2 text-xs text-muted">
          <input
            checked={!!row.base_rev_lock}
            onChange={(event) => onPatch({ base_rev_lock: event.target.checked })}
            type="checkbox"
          />
          <Lock className="size-3 text-muted-soft" />
          <span>
            锁定 base_rev（创建时快照 origin/
            {row.base_branch || "<base_branch>"} 的 SHA）
          </span>
        </label>
      ) : null}
    </div>
  );
}

