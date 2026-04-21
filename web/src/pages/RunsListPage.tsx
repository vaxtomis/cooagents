import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import useSWR from "swr";
import { createRun, createRunWithRequirement, listRuns } from "../api/runs";
import { StageProgress } from "../components/StageProgress";
import { StatusBadge } from "../components/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import { DASHBOARD_STAGE_FLOW, type CreateRunPayload, type RunRecord } from "../types";

const PAGE_SIZE = 10;
const DEFAULT_SORT_BY = "updated_at";
const DEFAULT_SORT_ORDER = "desc" as const;
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // 10 MB — must match backend limit
const ALLOWED_UPLOAD_EXT = /\.(md|docx)$/i;

const STATUS_OPTIONS = ["", "running", "completed", "failed", "cancelled"];
const SORT_OPTIONS = [
  { value: "updated_at", label: "更新时间" },
  { value: "created_at", label: "创建时间" },
  { value: "ticket", label: "工单" },
  { value: "current_stage", label: "阶段" },
  { value: "status", label: "状态" },
];

type FilterDraft = {
  ticket: string;
  status: string;
  stage: string;
  sortBy: string;
  sortOrder: "asc" | "desc";
};

function SectionPanel({
  title,
  kicker,
  children,
}: {
  title: string;
  kicker: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-[32px] border border-border bg-panel p-6 shadow-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] uppercase tracking-[0.3em] text-muted-soft">{kicker}</p>
          <h2 className="mt-2 font-serif text-[1.6rem] font-medium leading-snug tracking-tight text-copy">{title}</h2>
        </div>
      </div>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function parsePage(searchParams: URLSearchParams) {
  const raw = Number(searchParams.get("page") ?? "1");
  if (!Number.isFinite(raw) || raw < 1) {
    return 1;
  }
  return Math.floor(raw);
}

function readDraft(searchParams: URLSearchParams): FilterDraft {
  const sortOrder = searchParams.get("sortOrder") === "asc" ? "asc" : DEFAULT_SORT_ORDER;

  return {
    ticket: searchParams.get("ticket") ?? "",
    status: searchParams.get("status") ?? "",
    stage: searchParams.get("stage") ?? "",
    sortBy: searchParams.get("sortBy") ?? DEFAULT_SORT_BY,
    sortOrder,
  };
}

function buildSearchParams(draft: FilterDraft, page: number) {
  const next = new URLSearchParams();

  if (draft.ticket.trim()) {
    next.set("ticket", draft.ticket.trim());
  }
  if (draft.status) {
    next.set("status", draft.status);
  }
  if (draft.stage) {
    next.set("stage", draft.stage);
  }

  next.set("sortBy", draft.sortBy);
  next.set("sortOrder", draft.sortOrder);
  next.set("page", String(page));
  return next;
}

function formatTimestamp(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("zh-CN", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
  }).format(date);
}

function EmptyState({ copy }: { copy: string }) {
  return <p className="rounded-2xl border border-dashed border-border bg-panel-strong/40 px-4 py-6 text-sm text-muted">{copy}</p>;
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 5 }, (_, index) => (
        <div className="h-28 animate-pulse rounded-2xl border border-border bg-panel-strong/70" key={index} />
      ))}
    </div>
  );
}

function RunRow({ run, onOpen }: { run: RunRecord; onOpen: (runId: string) => void }) {
  return (
    <article className="rounded-2xl border border-border bg-panel-strong/80 p-4">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <p className="font-mono text-sm text-copy">{run.ticket}</p>
            <StatusBadge status={run.status} />
            <StatusBadge label={run.current_stage} status={run.current_stage.includes("REVIEW") ? "review" : run.status} />
          </div>
          <p className="mt-3 text-sm text-muted">{run.description || "暂无运行摘要。"}</p>
          <div className="mt-4">
            <StageProgress failedAtStage={run.failed_at_stage} stage={run.current_stage} />
          </div>
        </div>

        <div className="grid gap-3 text-sm text-muted sm:grid-cols-3 xl:min-w-[360px]">
          <div className="rounded-2xl border border-border bg-panel px-3 py-3">
            <p className="text-[11px] uppercase tracking-[0.24em] text-muted-soft">阶段</p>
            <p className="mt-2 font-mono text-xs text-copy">{run.current_stage}</p>
          </div>
          <div className="rounded-2xl border border-border bg-panel px-3 py-3">
            <p className="text-[11px] uppercase tracking-[0.24em] text-muted-soft">更新时间</p>
            <p className="mt-2 text-xs text-copy">{formatTimestamp(run.updated_at)}</p>
          </div>
          <div className="flex flex-col justify-between rounded-2xl border border-border bg-panel px-3 py-3">
            <div>
              <p className="text-[11px] uppercase tracking-[0.24em] text-muted-soft">仓库</p>
              <p className="mt-2 truncate text-xs text-copy">{run.repo_path}</p>
            </div>
            <button
              className="mt-4 rounded-xl bg-copy px-3 py-2 text-xs font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90"
              onClick={() => onOpen(run.id)}
              type="button"
            >
              {`打开 ${run.ticket}`}
            </button>
          </div>
        </div>
      </div>
    </article>
  );
}

function CreateRunDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (runId: string) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const pickFile = useCallback((picked: File | null | undefined) => {
    if (!picked) return;
    if (!ALLOWED_UPLOAD_EXT.test(picked.name)) {
      setError("仅支持 .md 或 .docx 文件");
      return;
    }
    if (picked.size > MAX_UPLOAD_BYTES) {
      setError(`文件不得超过 ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB`);
      return;
    }
    setError(null);
    setFile(picked);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    pickFile(e.dataTransfer.files[0]);
  }, [pickFile]);

  if (!open) return null;

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    const fd = new FormData(e.currentTarget);
    const ticket = (fd.get("ticket") as string).trim();
    const repo_path = (fd.get("repo_path") as string).trim();
    if (!ticket || !repo_path) {
      setError("工单和仓库路径为必填项");
      setSubmitting(false);
      return;
    }

    try {
      let result: { id: string };
      if (file) {
        const upload = new FormData();
        upload.append("file", file);
        upload.append("ticket", ticket);
        upload.append("repo_path", repo_path);
        const desc = (fd.get("description") as string)?.trim();
        if (desc) upload.append("description", desc);
        const da = fd.get("design_agent") as string;
        if (da) upload.append("design_agent", da);
        const dva = fd.get("dev_agent") as string;
        if (dva) upload.append("dev_agent", dva);
        result = await createRunWithRequirement(upload);
      } else {
        const payload: CreateRunPayload = { ticket, repo_path };
        const desc = (fd.get("description") as string)?.trim();
        if (desc) payload.description = desc;
        const da = fd.get("design_agent") as string;
        if (da) payload.design_agent = da;
        const dva = fd.get("dev_agent") as string;
        if (dva) payload.dev_agent = dva;
        result = await createRun(payload);
      }
      onCreated(result.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-copy/40 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-lg rounded-[32px] border border-border bg-panel p-6 shadow-panel" onClick={(e) => e.stopPropagation()}>
        <h2 className="font-serif text-xl font-medium leading-tight tracking-tight text-copy">创建任务</h2>
        <form className="mt-5 space-y-4" onSubmit={handleSubmit}>
          <label className="block space-y-1 text-sm text-muted">
            <span>工单 <span className="text-red-400">*</span></span>
            <input name="ticket" required className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]" placeholder="PROJ-123" />
          </label>
          <label className="block space-y-1 text-sm text-muted">
            <span>仓库路径 <span className="text-red-400">*</span></span>
            <input name="repo_path" required className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]" placeholder="/path/to/repo" />
          </label>
          <label className="block space-y-1 text-sm text-muted">
            <span>描述</span>
            <textarea name="description" rows={2} className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]" />
          </label>

          {/* File drop zone */}
          <div className="space-y-1 text-sm text-muted">
            <span>需求文档（可选，上传后跳过需求阶段）</span>
            <div
              className={`flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-4 py-6 transition ${file ? "border-accent/50 bg-accent/5" : "border-border-strong bg-panel hover:border-copy/20"}`}
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={handleDrop}
            >
              {file ? (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-copy">{file.name}</span>
                  <button type="button" className="text-xs text-red-400 hover:underline" onClick={(e) => { e.stopPropagation(); setFile(null); }}>移除</button>
                </div>
              ) : (
                <>
                  <p className="text-muted">拖拽文件到此处或点击选择</p>
                  <p className="mt-1 text-xs text-muted/60">支持 .md 和 .docx 文件</p>
                </>
              )}
              <input ref={fileRef} type="file" accept=".md,.docx" className="hidden" onChange={(e) => pickFile(e.target.files?.[0])} />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <label className="block space-y-1 text-sm text-muted">
              <span>设计 Agent</span>
              <select name="design_agent" className="w-full rounded-xl border border-border-strong bg-panel-strong px-4 py-3 text-sm text-copy outline-none [&_option]:bg-panel-strong">
                <option value="">默认</option>
                <option value="claude">Claude</option>
                <option value="codex">Codex</option>
              </select>
            </label>
            <label className="block space-y-1 text-sm text-muted">
              <span>开发 Agent</span>
              <select name="dev_agent" className="w-full rounded-xl border border-border-strong bg-panel-strong px-4 py-3 text-sm text-copy outline-none [&_option]:bg-panel-strong">
                <option value="">默认</option>
                <option value="claude">Claude</option>
                <option value="codex">Codex</option>
              </select>
            </label>
          </div>

          {error && <p className="rounded-xl bg-red-500/10 px-4 py-2 text-sm text-red-400">{error}</p>}

          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="rounded-lg border border-border-strong bg-panel-strong/50 px-5 py-2.5 text-sm font-medium text-copy transition hover:bg-panel-strong hover:shadow-[0_0_0_1px_var(--color-ring-warm)]">取消</button>
            <button type="submit" disabled={submitting} className="rounded-xl bg-copy px-5 py-2.5 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] hover:bg-copy/90 disabled:opacity-50">
              {submitting ? "创建中..." : "创建"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function RunsListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const polling = usePolling(15_000);
  const [showCreate, setShowCreate] = useState(false);
  const page = parsePage(searchParams);
  const applied = readDraft(searchParams);
  const [draft, setDraft] = useState<FilterDraft>(() => applied);

  useEffect(() => {
    setDraft(applied);
  }, [applied.stage, applied.status, applied.sortBy, applied.sortOrder, applied.ticket]);

  const runs = useSWR(
    ["runs", applied.ticket, applied.status, applied.stage, applied.sortBy, applied.sortOrder, page],
    () =>
      listRuns({
        currentStage: applied.stage || undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
        sortBy: applied.sortBy,
        sortOrder: applied.sortOrder,
        status: applied.status || undefined,
        ticket: applied.ticket || undefined,
      }),
    polling,
  );

  const items = runs.data?.items ?? [];
  const total = runs.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const summary = total === 0
    ? "当前查询未匹配到任何运行记录。"
    : `显示第 ${runs.data!.offset + 1}-${Math.min(runs.data!.offset + items.length, total)} 条，共 ${total} 条`;

  function commit(nextDraft: FilterDraft, nextPage: number) {
    setSearchParams(buildSearchParams(nextDraft, nextPage));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    commit(draft, 1);
  }

  return (
    <div className="space-y-4">
      <SectionPanel kicker="查询条件" title="筛选条件">
        <form className="grid gap-3 xl:grid-cols-[minmax(0,1.4fr)_repeat(4,minmax(0,0.8fr))_auto_auto]" onSubmit={handleSubmit}>
          <label className="space-y-2 text-sm text-muted">
            <span>工单</span>
            <input
              className="w-full rounded-xl border border-border-strong bg-panel px-4 py-3 text-sm text-copy outline-none transition focus:border-[color:var(--color-focus)] focus:shadow-[0_0_0_3px_rgba(56,152,236,0.18)]"
              onChange={(event) => setDraft((current) => ({ ...current, ticket: event.target.value }))}
              placeholder="按工单搜索"
              type="search"
              value={draft.ticket}
            />
          </label>

          <label className="space-y-2 text-sm text-muted">
            <span>状态</span>
            <select
              className="w-full rounded-xl border border-border-strong bg-panel-strong px-4 py-3 text-sm text-copy outline-none transition focus:border-accent/40 [&_option]:bg-panel-strong"
              onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value }))}
              value={draft.status}
            >
              <option value="">全部</option>
              {STATUS_OPTIONS.filter(Boolean).map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-2 text-sm text-muted">
            <span>阶段</span>
            <select
              className="w-full rounded-xl border border-border-strong bg-panel-strong px-4 py-3 text-sm text-copy outline-none transition focus:border-accent/40 [&_option]:bg-panel-strong"
              onChange={(event) => setDraft((current) => ({ ...current, stage: event.target.value }))}
              value={draft.stage}
            >
              <option value="">全部</option>
              {DASHBOARD_STAGE_FLOW.map((stage) => (
                <option key={stage} value={stage}>
                  {stage}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-2 text-sm text-muted">
            <span>排序字段</span>
            <select
              className="w-full rounded-xl border border-border-strong bg-panel-strong px-4 py-3 text-sm text-copy outline-none transition focus:border-accent/40 [&_option]:bg-panel-strong"
              onChange={(event) => setDraft((current) => ({ ...current, sortBy: event.target.value }))}
              value={draft.sortBy}
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-2 text-sm text-muted">
            <span>排序方向</span>
            <select
              className="w-full rounded-xl border border-border-strong bg-panel-strong px-4 py-3 text-sm text-copy outline-none transition focus:border-accent/40 [&_option]:bg-panel-strong"
              onChange={(event) => setDraft((current) => ({ ...current, sortOrder: event.target.value === "asc" ? "asc" : "desc" }))}
              value={draft.sortOrder}
            >
              <option value="desc">降序</option>
              <option value="asc">升序</option>
            </select>
          </label>

          <button className="rounded-xl bg-copy px-4 py-3 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90" type="submit">
            查询
          </button>
          <button
            className="rounded-lg border border-border-strong bg-panel-strong/50 px-4 py-3 text-sm font-medium text-copy transition hover:border-[color:var(--color-ring-warm)] hover:bg-panel-strong hover:shadow-[0_0_0_1px_var(--color-ring-warm)]"
            onClick={() => void runs.mutate()}
            type="button"
          >
            刷新
          </button>
          <button
            className="rounded-xl bg-accent px-4 py-3 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-accent)] transition hover:bg-accent-soft"
            onClick={() => setShowCreate(true)}
            type="button"
          >
            创建任务
          </button>
        </form>
      </SectionPanel>

      <SectionPanel kicker="服务端查询" title="运行列表">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4 text-sm text-muted">
          <p>{summary}</p>
          <div className="flex items-center gap-2">
            <button
              className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-2 text-xs font-medium text-copy transition hover:border-[color:var(--color-ring-warm)] hover:bg-panel-strong hover:shadow-[0_0_0_1px_var(--color-ring-warm)] disabled:cursor-not-allowed disabled:opacity-40"
              disabled={page <= 1}
              onClick={() => commit(applied, page - 1)}
              type="button"
            >
              上一页
            </button>
            <span className="rounded-full border border-border bg-panel px-3 py-2 text-xs text-muted">{`第 ${page} / ${totalPages} 页`}</span>
            <button
              className="rounded-lg border border-border-strong bg-panel-strong/50 px-3 py-2 text-xs font-medium text-copy transition hover:border-[color:var(--color-ring-warm)] hover:bg-panel-strong hover:shadow-[0_0_0_1px_var(--color-ring-warm)] disabled:cursor-not-allowed disabled:opacity-40"
              disabled={page >= totalPages || total === 0}
              onClick={() => commit(applied, page + 1)}
              type="button"
            >
              下一页
            </button>
          </div>
        </div>

        <div className="mt-5">
          {runs.error ? (
            <div className="rounded-2xl border border-danger/15 bg-danger/8 p-5">
              <h3 className="font-serif text-lg font-medium leading-tight tracking-tight text-copy">运行数据加载失败</h3>
              <p className="mt-2 text-sm text-muted">请重试查询或调整筛选条件。</p>
              <button className="mt-4 rounded-xl bg-copy px-4 py-2 text-sm font-medium text-ink-invert shadow-[0_0_0_1px_var(--color-copy)] transition hover:bg-copy/90" onClick={() => void runs.mutate()} type="button">
                重试
              </button>
            </div>
          ) : !runs.data ? (
            <LoadingSkeleton />
          ) : items.length === 0 ? (
            <EmptyState copy="当前筛选条件未匹配到任何运行记录。" />
          ) : (
            <div className="space-y-3">
              {items.map((run) => (
                <RunRow key={run.id} onOpen={(runId) => navigate(`/runs/${runId}`)} run={run} />
              ))}
            </div>
          )}
        </div>
      </SectionPanel>

      <CreateRunDialog
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={(runId) => {
          setShowCreate(false);
          navigate(`/runs/${runId}`);
        }}
      />
    </div>
  );
}
