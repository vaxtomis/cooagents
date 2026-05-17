import { Check, FileText, Plus, Search, Upload, X } from "lucide-react";
import { useState, type ChangeEvent } from "react";
import useSWR from "swr";
import {
  listWorkspaceFiles,
  uploadWorkspaceFile,
  type ListWorkspaceFilesParams,
} from "../api/workspaces";
import { extractError } from "../lib/extractError";
import { formatBytes } from "../lib/formatBytes";

const SELECT_CLASSNAME =
  "rounded-xl border border-border-strong bg-panel-strong px-3 py-2 text-xs text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)] [&_option]:bg-panel-strong";
const INPUT_CLASSNAME =
  "w-full rounded-xl border border-border-strong bg-panel px-3 py-2 text-xs text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]";
const ICON_BUTTON_CLASSNAME =
  "inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-panel text-muted transition hover:border-accent/40 hover:text-copy disabled:opacity-40";

export interface WorkspaceFilePickerProps {
  workspaceId: string;
  value: string[];
  onChange: (paths: string[]) => void;
  label?: string;
  maxSelected?: number;
}

function normalizeSelection(paths: string[]) {
  const seen = new Set<string>();
  const next: string[] = [];
  for (const path of paths) {
    const trimmed = path.trim();
    if (!trimmed || seen.has(trimmed)) continue;
    seen.add(trimmed);
    next.push(trimmed);
  }
  return next;
}

function resultPath(uploaded: Awaited<ReturnType<typeof uploadWorkspaceFile>>) {
  return uploaded.attachment_path ?? uploaded.markdown_path;
}

export function WorkspaceFilePicker({
  workspaceId,
  value,
  onChange,
  label = "Workspace files",
  maxSelected,
}: WorkspaceFilePickerProps) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selected = normalizeSelection(value);
  const params: ListWorkspaceFilesParams = {
    selectable: true,
    limit: 50,
    ...(query.trim() ? { query: query.trim() } : {}),
    ...(kind ? { kind } : {}),
  };
  const filesQuery = useSWR(
    ["workspace-files-picker", workspaceId, query.trim(), kind],
    () => listWorkspaceFiles(workspaceId, params),
    { revalidateOnFocus: false },
  );
  const files = filesQuery.data?.files ?? [];

  function addPath(path: string) {
    if (maxSelected !== undefined && selected.length >= maxSelected) {
      setError(`Select at most ${maxSelected} files.`);
      return;
    }
    onChange(normalizeSelection([...selected, path]));
    setError(null);
  }

  function removePath(path: string) {
    onChange(selected.filter((item) => item !== path));
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const filesToUpload = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (filesToUpload.length === 0) return;
    if (
      maxSelected !== undefined &&
      selected.length + filesToUpload.length > maxSelected
    ) {
      setError(`Select at most ${maxSelected} files.`);
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const uploaded = await Promise.all(
        filesToUpload.map((file) => uploadWorkspaceFile(workspaceId, file)),
      );
      onChange(normalizeSelection([...selected, ...uploaded.map(resultPath)]));
      await filesQuery.mutate();
    } catch (err) {
      setError(extractError(err, "Workspace file upload failed"));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-3 rounded-2xl border border-border bg-panel-strong/55 p-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <p className="text-sm font-medium text-copy">{label}</p>
          <p className="mt-1 text-xs text-muted">
            Select existing Workspace files or upload new files.
          </p>
        </div>
        <label className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-border-dark/60 bg-panel px-3 py-2 text-xs font-medium text-copy-soft transition hover:border-accent/45 hover:text-copy">
          <Upload aria-hidden="true" className="h-4 w-4" />
          <span>{uploading ? "Uploading..." : "Upload and select"}</span>
          <input
            aria-label={`${label} upload`}
            className="sr-only"
            disabled={uploading}
            multiple
            onChange={(event) => void handleUpload(event)}
            type="file"
          />
        </label>
      </div>

      {selected.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {selected.map((path) => (
            <span
              className="inline-flex min-w-0 max-w-full items-center gap-2 rounded-xl border border-accent/25 bg-accent/10 px-2.5 py-1.5 text-xs text-copy-soft"
              key={path}
            >
              <FileText aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate font-mono">{path}</span>
              <button
                aria-label={`Remove ${path}`}
                className="text-muted transition hover:text-danger"
                onClick={() => removePath(path)}
                type="button"
              >
                <X aria-hidden="true" className="h-3.5 w-3.5" />
              </button>
            </span>
          ))}
        </div>
      ) : null}

      <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_10rem]">
        <label className="relative block">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted"
          />
          <input
            aria-label={`${label} search`}
            className={`${INPUT_CLASSNAME} pl-8`}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search files"
            value={query}
          />
        </label>
        <select
          aria-label={`${label} kind`}
          className={SELECT_CLASSNAME}
          onChange={(event) => setKind(event.target.value)}
          value={kind}
        >
          <option value="">All kinds</option>
          <option value="attachment">attachment</option>
          <option value="image">image</option>
          <option value="context">context</option>
          <option value="artifact">artifact</option>
          <option value="feedback">feedback</option>
          <option value="other">other</option>
        </select>
      </div>

      {filesQuery.error ? (
        <p className="text-xs text-danger">
          {extractError(filesQuery.error, "Workspace files failed to load")}
        </p>
      ) : null}
      {error ? <p className="text-xs text-danger">{error}</p> : null}

      <div className="max-h-56 space-y-2 overflow-y-auto pr-1">
        {filesQuery.data === undefined && !filesQuery.error ? (
          <div className="h-12 animate-pulse rounded-xl border border-border bg-panel/70" />
        ) : files.length === 0 ? (
          <p className="rounded-xl border border-border bg-panel/60 px-3 py-2 text-xs text-muted">
            No selectable files found.
          </p>
        ) : (
          files.map((file) => {
            const isSelected = selected.includes(file.relative_path);
            return (
              <div
                className="flex items-center justify-between gap-3 rounded-xl border border-border bg-panel/70 px-3 py-2 text-xs text-muted"
                key={file.relative_path}
              >
                <span className="flex min-w-0 items-center gap-2">
                  <FileText
                    aria-hidden="true"
                    className="h-4 w-4 shrink-0 text-copy-soft"
                  />
                  <span className="min-w-0">
                    <span className="block truncate font-mono text-copy-soft">
                      {file.relative_path}
                    </span>
                    <span className="block">
                      {file.kind} / {formatBytes(file.byte_size ?? 0)}
                    </span>
                  </span>
                </span>
                <button
                  aria-label={
                    isSelected
                      ? `Selected ${file.relative_path}`
                      : `Select ${file.relative_path}`
                  }
                  className={ICON_BUTTON_CLASSNAME}
                  disabled={isSelected}
                  onClick={() => addPath(file.relative_path)}
                  title={isSelected ? "Selected" : "Select"}
                  type="button"
                >
                  {isSelected ? (
                    <Check aria-hidden="true" className="h-3.5 w-3.5" />
                  ) : (
                    <Plus aria-hidden="true" className="h-3.5 w-3.5" />
                  )}
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
