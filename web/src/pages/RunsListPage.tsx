import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import useSWR from "swr";
import { listRuns } from "../api/runs";
import { StageProgress } from "../components/StageProgress";
import { StatusBadge } from "../components/StatusBadge";
import { usePolling } from "../hooks/usePolling";
import { DASHBOARD_STAGE_FLOW, type RunRecord } from "../types";

const PAGE_SIZE = 10;
const DEFAULT_SORT_BY = "updated_at";
const DEFAULT_SORT_ORDER = "desc" as const;

const STATUS_OPTIONS = ["", "running", "completed", "failed", "cancelled"];
const SORT_OPTIONS = [
  { value: "updated_at", label: "更新时间" },
  { value: "created_at", label: "创建时间" },
  { value: "ticket", label: "Ticket" },
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
    <section className="rounded-[28px] border border-white/6 bg-panel p-6 shadow-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] uppercase tracking-[0.3em] text-muted/75">{kicker}</p>
          <h2 className="mt-2 text-lg font-semibold text-white">{title}</h2>
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
  return <p className="rounded-2xl border border-dashed border-white/8 bg-white/3 px-4 py-6 text-sm text-muted">{copy}</p>;
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 5 }, (_, index) => (
        <div className="h-28 animate-pulse rounded-[24px] border border-white/6 bg-panel-strong/70" key={index} />
      ))}
    </div>
  );
}

function RunRow({ run, onOpen }: { run: RunRecord; onOpen: (runId: string) => void }) {
  return (
    <article className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <p className="font-mono text-sm text-white">{run.ticket}</p>
            <StatusBadge status={run.status} />
            <StatusBadge label={run.current_stage} status={run.current_stage.includes("REVIEW") ? "review" : run.status} />
          </div>
          <p className="mt-3 text-sm text-muted">{run.description || "No summary provided for this run."}</p>
          <div className="mt-4">
            <StageProgress failedAtStage={run.failed_at_stage} stage={run.current_stage} />
          </div>
        </div>

        <div className="grid gap-3 text-sm text-muted sm:grid-cols-3 xl:min-w-[360px]">
          <div className="rounded-2xl border border-white/6 bg-black/18 px-3 py-3">
            <p className="text-[11px] uppercase tracking-[0.24em] text-muted/75">Stage</p>
            <p className="mt-2 font-mono text-xs text-white">{run.current_stage}</p>
          </div>
          <div className="rounded-2xl border border-white/6 bg-black/18 px-3 py-3">
            <p className="text-[11px] uppercase tracking-[0.24em] text-muted/75">Updated</p>
            <p className="mt-2 text-xs text-white">{formatTimestamp(run.updated_at)}</p>
          </div>
          <div className="flex flex-col justify-between rounded-2xl border border-white/6 bg-black/18 px-3 py-3">
            <div>
              <p className="text-[11px] uppercase tracking-[0.24em] text-muted/75">Repo</p>
              <p className="mt-2 truncate text-xs text-white">{run.repo_path}</p>
            </div>
            <button
              className="mt-4 rounded-full bg-white px-3 py-2 text-xs font-medium text-black transition hover:bg-white/90"
              onClick={() => onOpen(run.id)}
              type="button"
            >
              {`Open ${run.ticket}`}
            </button>
          </div>
        </div>
      </div>
    </article>
  );
}

export function RunsListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const polling = usePolling(15_000);
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
    ? "No runs matched the current query."
    : `Showing ${runs.data!.offset + 1}-${Math.min(runs.data!.offset + items.length, total)} of ${total} runs`;

  function commit(nextDraft: FilterDraft, nextPage: number) {
    setSearchParams(buildSearchParams(nextDraft, nextPage));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    commit(draft, 1);
  }

  return (
    <div className="space-y-4">
      <SectionPanel kicker="Query Controls" title="Run filters">
        <form className="grid gap-3 xl:grid-cols-[minmax(0,1.4fr)_repeat(4,minmax(0,0.8fr))_auto_auto]" onSubmit={handleSubmit}>
          <label className="space-y-2 text-sm text-muted">
            <span>Ticket</span>
            <input
              className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40"
              onChange={(event) => setDraft((current) => ({ ...current, ticket: event.target.value }))}
              placeholder="Search by ticket"
              type="search"
              value={draft.ticket}
            />
          </label>

          <label className="space-y-2 text-sm text-muted">
            <span>Status</span>
            <select
              className="w-full rounded-2xl border border-white/8 bg-panel-strong px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40 [&_option]:bg-panel-strong"
              onChange={(event) => setDraft((current) => ({ ...current, status: event.target.value }))}
              value={draft.status}
            >
              <option value="">All</option>
              {STATUS_OPTIONS.filter(Boolean).map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-2 text-sm text-muted">
            <span>Stage</span>
            <select
              className="w-full rounded-2xl border border-white/8 bg-panel-strong px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40 [&_option]:bg-panel-strong"
              onChange={(event) => setDraft((current) => ({ ...current, stage: event.target.value }))}
              value={draft.stage}
            >
              <option value="">All</option>
              {DASHBOARD_STAGE_FLOW.map((stage) => (
                <option key={stage} value={stage}>
                  {stage}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-2 text-sm text-muted">
            <span>Sort by</span>
            <select
              className="w-full rounded-2xl border border-white/8 bg-panel-strong px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40 [&_option]:bg-panel-strong"
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
            <span>Direction</span>
            <select
              className="w-full rounded-2xl border border-white/8 bg-panel-strong px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40 [&_option]:bg-panel-strong"
              onChange={(event) => setDraft((current) => ({ ...current, sortOrder: event.target.value === "asc" ? "asc" : "desc" }))}
              value={draft.sortOrder}
            >
              <option value="desc">desc</option>
              <option value="asc">asc</option>
            </select>
          </label>

          <button className="rounded-full bg-white px-4 py-3 text-sm font-medium text-black transition hover:bg-white/90" type="submit">
            Apply
          </button>
          <button
            className="rounded-full border border-white/10 bg-white/4 px-4 py-3 text-sm font-medium text-white transition hover:border-white/20 hover:bg-white/8"
            onClick={() => void runs.mutate()}
            type="button"
          >
            Refresh
          </button>
        </form>
      </SectionPanel>

      <SectionPanel kicker="Server-backed Query" title="Runs list">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/6 pb-4 text-sm text-muted">
          <p>{summary}</p>
          <div className="flex items-center gap-2">
            <button
              className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8 disabled:cursor-not-allowed disabled:opacity-40"
              disabled={page <= 1}
              onClick={() => commit(applied, page - 1)}
              type="button"
            >
              Previous
            </button>
            <span className="rounded-full border border-white/8 bg-black/18 px-3 py-2 text-xs text-muted">{`Page ${page} / ${totalPages}`}</span>
            <button
              className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8 disabled:cursor-not-allowed disabled:opacity-40"
              disabled={page >= totalPages || total === 0}
              onClick={() => commit(applied, page + 1)}
              type="button"
            >
              Next
            </button>
          </div>
        </div>

        <div className="mt-5">
          {runs.error ? (
            <div className="rounded-[24px] border border-danger/15 bg-danger/8 p-5">
              <h3 className="text-base font-semibold text-white">Runs data failed to load</h3>
              <p className="mt-2 text-sm text-muted">Retry the server query or adjust the current filters.</p>
              <button className="mt-4 rounded-full bg-white px-4 py-2 text-sm font-medium text-black" onClick={() => void runs.mutate()} type="button">
                Retry
              </button>
            </div>
          ) : !runs.data ? (
            <LoadingSkeleton />
          ) : items.length === 0 ? (
            <EmptyState copy="No runs matched the current filters yet." />
          ) : (
            <div className="space-y-3">
              {items.map((run) => (
                <RunRow key={run.id} onOpen={(runId) => navigate(`/runs/${runId}`)} run={run} />
              ))}
            </div>
          )}
        </div>
      </SectionPanel>
    </div>
  );
}
