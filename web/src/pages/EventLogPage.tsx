import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import useSWR from "swr";
import { listEvents } from "../api/events";
import { usePolling } from "../hooks/usePolling";
import type { EventRecord } from "../types";

const PAGE_SIZE = 20;

type FilterDraft = {
  runId: string;
  level: string;
  spanType: string;
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
      <p className="text-[11px] uppercase tracking-[0.3em] text-muted/75">{kicker}</p>
      <h2 className="mt-2 text-lg font-semibold text-white">{title}</h2>
      <div className="mt-5">{children}</div>
    </section>
  );
}

function EmptyState({ copy }: { copy: string }) {
  return <p className="rounded-2xl border border-dashed border-white/8 bg-white/3 px-4 py-6 text-sm text-muted">{copy}</p>;
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 4 }, (_, index) => (
        <div key={index} className="h-28 animate-pulse rounded-[24px] border border-white/6 bg-panel-strong/70" />
      ))}
    </div>
  );
}

function parsePage(searchParams: URLSearchParams) {
  const raw = Number(searchParams.get("page") ?? "1");
  if (!Number.isFinite(raw) || raw < 1) {
    return 1;
  }
  return Math.floor(raw);
}

function readFilters(searchParams: URLSearchParams): FilterDraft {
  return {
    level: searchParams.get("level") ?? "",
    runId: searchParams.get("runId") ?? "",
    spanType: searchParams.get("spanType") ?? "",
  };
}

function buildSearchParams(filters: FilterDraft, page: number) {
  const next = new URLSearchParams();
  if (filters.runId.trim()) {
    next.set("runId", filters.runId.trim());
  }
  if (filters.level) {
    next.set("level", filters.level);
  }
  if (filters.spanType.trim()) {
    next.set("spanType", filters.spanType.trim());
  }
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

function stringifyPayload(payload: unknown) {
  if (typeof payload === "string") {
    return payload;
  }

  try {
    return JSON.stringify(payload ?? null, null, 2);
  } catch {
    return String(payload);
  }
}

export function EventLogPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const polling = usePolling(15_000);
  const page = parsePage(searchParams);
  const applied = readFilters(searchParams);
  const [draft, setDraft] = useState<FilterDraft>(() => applied);
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setDraft(applied);
  }, [applied.level, applied.runId, applied.spanType]);

  const eventsQuery = useSWR(
    ["events", applied.runId, applied.level, applied.spanType, page],
    () =>
      listEvents({
        level: applied.level ? (applied.level as "debug" | "info" | "warning" | "error") : undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
        runId: applied.runId || undefined,
        spanType: applied.spanType || undefined,
      }),
    polling,
  );

  const events = eventsQuery.data?.events ?? [];
  const pagination = eventsQuery.data?.pagination;
  const total = pagination?.total ?? events.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const summary = total === 0
    ? "No events matched the current query."
    : `Showing ${pagination!.offset + 1}-${Math.min(pagination!.offset + events.length, total)} of ${total} events`;

  const selectedLevels = useMemo(
    () => [
      { label: "All", value: "" },
      { label: "debug", value: "debug" },
      { label: "info", value: "info" },
      { label: "warning", value: "warning" },
      { label: "error", value: "error" },
    ],
    [],
  );

  function commit(nextDraft: FilterDraft, nextPage: number) {
    setSearchParams(buildSearchParams(nextDraft, nextPage));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    commit(draft, 1);
  }

  function updateDraft(field: keyof FilterDraft) {
    return (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      setDraft((current) => ({ ...current, [field]: event.target.value }));
    };
  }

  return (
    <div className="space-y-4">
      <SectionPanel kicker="Query Controls" title="Event filters">
        <form className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_200px_minmax(0,1fr)_auto_auto]" onSubmit={handleSubmit}>
          <label className="space-y-2 text-sm text-muted">
            <span>Run id</span>
            <input
              className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40"
              onChange={updateDraft("runId")}
              type="text"
              value={draft.runId}
            />
          </label>

          <label className="space-y-2 text-sm text-muted">
            <span>Level</span>
            <select
              className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40"
              onChange={updateDraft("level")}
              value={draft.level}
            >
              {selectedLevels.map((option) => (
                <option key={option.label} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-2 text-sm text-muted">
            <span>Span type</span>
            <input
              className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40"
              onChange={updateDraft("spanType")}
              type="text"
              value={draft.spanType}
            />
          </label>

          <button className="rounded-full bg-white px-4 py-3 text-sm font-medium text-black transition hover:bg-white/90" type="submit">
            Apply
          </button>
          <button
            className="rounded-full border border-white/10 bg-white/4 px-4 py-3 text-sm font-medium text-white transition hover:border-white/20 hover:bg-white/8"
            onClick={() => void eventsQuery.mutate()}
            type="button"
          >
            Refresh
          </button>
        </form>
      </SectionPanel>

      <SectionPanel kicker="Server-backed Query" title="Event stream">
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
          {eventsQuery.error ? (
            <div className="rounded-[24px] border border-danger/15 bg-danger/8 p-5">
              <h3 className="text-base font-semibold text-white">Events failed to load</h3>
              <p className="mt-2 text-sm text-muted">Retry the event query while keeping the current filters.</p>
              <button className="mt-4 rounded-full bg-white px-4 py-2 text-sm font-medium text-black" onClick={() => void eventsQuery.mutate()} type="button">
                Retry
              </button>
            </div>
          ) : !eventsQuery.data ? (
            <LoadingSkeleton />
          ) : events.length === 0 ? (
            <EmptyState copy="No events matched the current filters." />
          ) : (
            <div className="space-y-3">
              {events.map((event) => {
                const eventKey = String(event.id ?? `${event.event_type}-${event.created_at}`);
                const expanded = expandedIds[eventKey] ?? false;

                return (
                  <article className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4" key={eventKey}>
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <p className="font-mono text-sm text-white">{event.event_type}</p>
                        <p className="mt-1 text-xs text-muted">
                          {(event.level ?? "info")} · {(event.span_type ?? "system")} · {(event.source ?? "engine")}
                        </p>
                      </div>
                      <span className="text-xs text-muted">{formatTimestamp(event.created_at)}</span>
                    </div>

                    <div className="mt-4 flex flex-wrap gap-2">
                      {event.run_id ? (
                        <Link
                          className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8"
                          to={`/runs/${event.run_id}`}
                        >
                          {`Open ${event.run_id}`}
                        </Link>
                      ) : null}
                      <button
                        className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8"
                        onClick={() => setExpandedIds((current) => ({ ...current, [eventKey]: !expanded }))}
                        type="button"
                      >
                        {expanded ? `Hide payload ${eventKey}` : `Expand payload ${eventKey}`}
                      </button>
                    </div>

                    {expanded ? (
                      <pre className="mt-4 overflow-x-auto rounded-2xl bg-black/30 p-4 text-xs text-white whitespace-pre-wrap">{stringifyPayload(event.payload)}</pre>
                    ) : null}
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </SectionPanel>
    </div>
  );
}
