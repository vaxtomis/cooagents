import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import useSWR from "swr";
import { getJobDiagnosis, getRunTrace, getTraceLookup } from "../api/diagnostics";
import { listEvents } from "../api/events";
import { usePolling } from "../hooks/usePolling";
import type { EventRecord } from "../types";

const PAGE_SIZE = 20;
const TRACE_LIMIT = 200;

const LEVEL_ORDER = {
  debug: 0,
  error: 3,
  info: 1,
  warning: 2,
} as const;

type FilterDraft = {
  runId: string;
  level: string;
  spanType: string;
  jobId: string;
  eventType: string;
  traceId: string;
};

type DiagnosticRow = {
  event: EventRecord;
  kind: "Anomaly" | "Context";
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
  return (
    <p className="rounded-2xl border border-dashed border-white/8 bg-white/3 px-4 py-6 text-sm text-muted">
      {copy}
    </p>
  );
}

function LoadingSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className="h-28 animate-pulse rounded-[24px] border border-white/6 bg-panel-strong/70"
        />
      ))}
    </div>
  );
}

function SummaryMetric({
  label,
  value,
}: {
  label: string;
  value: ReactNode;
}) {
  return (
    <div className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4">
      <p className="text-xs uppercase tracking-[0.22em] text-muted/75">{label}</p>
      <p className="mt-3 text-lg font-semibold text-white">{value}</p>
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

function normalizeLevel(value: string | null | undefined) {
  if (!value) {
    return "";
  }

  return value in LEVEL_ORDER ? value : "";
}

function readFilters(searchParams: URLSearchParams): FilterDraft {
  const runId = searchParams.get("runId") ?? "";

  return {
    eventType: searchParams.get("eventType") ?? "",
    jobId: searchParams.get("jobId") ?? "",
    level: normalizeLevel(searchParams.get("level")) || (runId ? "warning" : ""),
    runId,
    spanType: searchParams.get("spanType") ?? "",
    traceId: searchParams.get("traceId") ?? "",
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
  if (filters.jobId.trim()) {
    next.set("jobId", filters.jobId.trim());
  }
  if (filters.eventType.trim()) {
    next.set("eventType", filters.eventType.trim());
  }
  if (filters.traceId.trim()) {
    next.set("traceId", filters.traceId.trim());
  }
  if (!filters.runId.trim() && page > 1) {
    next.set("page", String(page));
  }

  return next;
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return "Unavailable";
  }

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

function getLevelRank(level: string | null | undefined) {
  if (!level) {
    return LEVEL_ORDER.info;
  }

  return LEVEL_ORDER[level as keyof typeof LEVEL_ORDER] ?? LEVEL_ORDER.info;
}

function countSuspiciousJobs(events: EventRecord[]) {
  return new Set(
    events
      .filter((event) => getLevelRank(event.level) >= LEVEL_ORDER.warning && event.job_id)
      .map((event) => event.job_id as string),
  ).size;
}

function buildDiagnosticRows(events: EventRecord[], filters: FilterDraft): DiagnosticRow[] {
  const scopedEvents = events.filter((event) => {
    if (filters.jobId && event.job_id !== filters.jobId) {
      return false;
    }
    if (filters.spanType && event.span_type !== filters.spanType) {
      return false;
    }
    if (filters.traceId && event.trace_id !== filters.traceId) {
      return false;
    }
    return true;
  });

  if (scopedEvents.length === 0) {
    return [];
  }

  const anomalyThreshold = getLevelRank(filters.level || "warning");
  const eventType = filters.eventType.trim();
  const markers = new Map<number, "Anomaly" | "Context">();

  scopedEvents.forEach((event, index) => {
    const matchesEventType = !eventType || event.event_type === eventType;
    if (!matchesEventType || getLevelRank(event.level) < anomalyThreshold) {
      return;
    }

    for (let offset = -2; offset <= 2; offset += 1) {
      const target = index + offset;
      if (target < 0 || target >= scopedEvents.length) {
        continue;
      }

      if (offset === 0) {
        markers.set(target, "Anomaly");
      } else if (!markers.has(target)) {
        markers.set(target, "Context");
      }
    }
  });

  if (markers.size === 0) {
    return [];
  }

  return scopedEvents
    .map((event, index) => {
      const kind = markers.get(index);
      return kind ? { event, kind } : null;
    })
    .filter((entry): entry is DiagnosticRow => entry !== null);
}

function GlobalEventCard({
  event,
  expanded,
  eventKey,
  onToggle,
}: {
  event: EventRecord;
  expanded: boolean;
  eventKey: string;
  onToggle: (eventKey: string) => void;
}) {
  return (
    <article className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4">
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
          onClick={() => onToggle(eventKey)}
          type="button"
        >
          {expanded ? `Hide payload ${eventKey}` : `Expand payload ${eventKey}`}
        </button>
      </div>

      {expanded ? (
        <pre className="mt-4 overflow-x-auto rounded-2xl bg-black/30 p-4 text-xs text-white whitespace-pre-wrap">
          {stringifyPayload(event.payload)}
        </pre>
      ) : null}
    </article>
  );
}

function DiagnosticEventCard({
  row,
  eventKey,
  expanded,
  onOpenJob,
  onOpenTrace,
  onToggle,
}: {
  row: DiagnosticRow;
  eventKey: string;
  expanded: boolean;
  onOpenJob: (jobId: string) => void;
  onOpenTrace: (traceId: string) => void;
  onToggle: (eventKey: string) => void;
}) {
  const { event, kind } = row;
  const kindClasses =
    kind === "Anomaly"
      ? "border-warning/20 bg-warning/10 text-warning"
      : "border-white/8 bg-white/4 text-muted";

  return (
    <article className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-2.5 py-1 text-[11px] font-medium ${kindClasses}`}>
              {kind}
            </span>
            <p className="font-mono text-sm text-white">{event.event_type}</p>
          </div>
          <p className="mt-2 text-xs text-muted">
            {(event.level ?? "info")} · {(event.span_type ?? "system")} · {(event.source ?? "engine")}
          </p>
          {event.job_id ? (
            <p className="mt-2 text-xs text-muted">{`job ${event.job_id}`}</p>
          ) : null}
        </div>
        <span className="text-xs text-muted">{formatTimestamp(event.created_at)}</span>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {event.job_id ? (
          <button
            className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8"
            onClick={() => onOpenJob(event.job_id as string)}
            type="button"
          >
            {`Open job ${event.job_id}`}
          </button>
        ) : null}
        {event.trace_id ? (
          <button
            className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8"
            onClick={() => onOpenTrace(event.trace_id as string)}
            type="button"
          >
            {`Open trace ${event.trace_id}`}
          </button>
        ) : null}
        <button
          className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8"
          onClick={() => onToggle(eventKey)}
          type="button"
        >
          {expanded ? `Hide payload ${eventKey}` : `Expand payload ${eventKey}`}
        </button>
      </div>

      {expanded ? (
        <pre className="mt-4 overflow-x-auto rounded-2xl bg-black/30 p-4 text-xs text-white whitespace-pre-wrap">
          {stringifyPayload(event.payload)}
        </pre>
      ) : null}
    </article>
  );
}

export function EventLogPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const polling = usePolling(15_000);
  const page = parsePage(searchParams);
  const applied = readFilters(searchParams);
  const isDiagnosticMode = Boolean(applied.runId.trim());
  const [draft, setDraft] = useState<FilterDraft>(() => applied);
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setDraft(applied);
  }, [
    applied.eventType,
    applied.jobId,
    applied.level,
    applied.runId,
    applied.spanType,
    applied.traceId,
  ]);

  const eventsQuery = useSWR(
    !isDiagnosticMode ? ["events", applied.level, applied.spanType, page] : null,
    () =>
      listEvents({
        level: applied.level ? (applied.level as "debug" | "info" | "warning" | "error") : undefined,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
        runId: undefined,
        spanType: applied.spanType || undefined,
      }),
    polling,
  );

  const diagnosticQuery = useSWR(
    isDiagnosticMode ? ["run-trace", applied.runId, applied.spanType] : null,
    () =>
      getRunTrace(applied.runId, {
        level: applied.level === "debug" ? "debug" : "info",
        limit: TRACE_LIMIT,
        spanType: applied.spanType || undefined,
      }),
    polling,
  );

  const jobDiagnosisQuery = useSWR(
    isDiagnosticMode && applied.jobId ? ["job-diagnosis", applied.jobId] : null,
    () => getJobDiagnosis(applied.jobId),
    { revalidateOnFocus: false },
  );

  const traceLookupQuery = useSWR(
    isDiagnosticMode && applied.traceId ? ["trace-lookup", applied.traceId] : null,
    () => getTraceLookup(applied.traceId, "info"),
    { revalidateOnFocus: false },
  );

  const globalEvents = eventsQuery.data?.events ?? [];
  const globalPagination = eventsQuery.data?.pagination;
  const total = globalPagination?.total ?? globalEvents.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const globalSummary =
    total === 0
      ? "No events matched the current query."
      : `Showing ${globalPagination!.offset + 1}-${Math.min(globalPagination!.offset + globalEvents.length, total)} of ${total} events`;

  const diagnosticRows = useMemo(
    () => buildDiagnosticRows(diagnosticQuery.data?.events ?? [], applied),
    [applied, diagnosticQuery.data?.events],
  );

  const suspiciousJobs = useMemo(
    () => countSuspiciousJobs(diagnosticQuery.data?.events ?? []),
    [diagnosticQuery.data?.events],
  );

  const latestDiagnosticEvent = diagnosticQuery.data?.events.at(-1)?.created_at ?? null;

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

  function commit(nextDraft: FilterDraft, nextPage = 1) {
    setSearchParams(buildSearchParams(nextDraft, nextPage));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    commit(draft, isDiagnosticMode ? 1 : 1);
  }

  function updateDraft(field: keyof FilterDraft) {
    return (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      setDraft((current) => ({ ...current, [field]: event.target.value }));
    };
  }

  function toggleExpanded(eventKey: string) {
    setExpandedIds((current) => ({ ...current, [eventKey]: !current[eventKey] }));
  }

  function openJob(jobId: string) {
    commit({ ...applied, jobId });
  }

  function openTrace(traceId: string) {
    commit({ ...applied, traceId });
  }

  function clearTrace() {
    commit({ ...applied, traceId: "" });
  }

  return (
    <div className="space-y-4">
      <SectionPanel kicker="Query Controls" title="Event filters">
        <form
          className={`grid gap-3 ${isDiagnosticMode ? "xl:grid-cols-[minmax(0,1.1fr)_160px_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto]" : "xl:grid-cols-[minmax(0,1fr)_200px_minmax(0,1fr)_auto_auto]"}`}
          onSubmit={handleSubmit}
        >
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
            <span>{isDiagnosticMode ? "Threshold" : "Level"}</span>
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

          {isDiagnosticMode ? (
            <>
              <label className="space-y-2 text-sm text-muted">
                <span>Job id</span>
                <input
                  className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40"
                  onChange={updateDraft("jobId")}
                  type="text"
                  value={draft.jobId}
                />
              </label>

              <label className="space-y-2 text-sm text-muted">
                <span>Event type</span>
                <input
                  className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none transition focus:border-accent/40"
                  onChange={updateDraft("eventType")}
                  type="text"
                  value={draft.eventType}
                />
              </label>
            </>
          ) : null}

          <button
            className="rounded-full bg-white px-4 py-3 text-sm font-medium text-black transition hover:bg-white/90"
            type="submit"
          >
            Apply
          </button>

          {!isDiagnosticMode ? (
            <button
              className="rounded-full border border-white/10 bg-white/4 px-4 py-3 text-sm font-medium text-white transition hover:border-white/20 hover:bg-white/8"
              onClick={() => void eventsQuery.mutate()}
              type="button"
            >
              Refresh
            </button>
          ) : null}
        </form>
      </SectionPanel>

      {!isDiagnosticMode ? (
        <SectionPanel kicker="Server-backed Query" title="Event stream">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/6 pb-4 text-sm text-muted">
            <p>{globalSummary}</p>
            <div className="flex items-center gap-2">
              <button
                className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8 disabled:cursor-not-allowed disabled:opacity-40"
                disabled={page <= 1}
                onClick={() => commit(applied, page - 1)}
                type="button"
              >
                Previous
              </button>
              <span className="rounded-full border border-white/8 bg-black/18 px-3 py-2 text-xs text-muted">
                {`Page ${page} / ${totalPages}`}
              </span>
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
                <p className="mt-2 text-sm text-muted">
                  Retry the event query while keeping the current filters.
                </p>
                <button
                  className="mt-4 rounded-full bg-white px-4 py-2 text-sm font-medium text-black"
                  onClick={() => void eventsQuery.mutate()}
                  type="button"
                >
                  Retry
                </button>
              </div>
            ) : !eventsQuery.data ? (
              <LoadingSkeleton />
            ) : globalEvents.length === 0 ? (
              <EmptyState copy="No events matched the current filters." />
            ) : (
              <div className="space-y-3">
                {globalEvents.map((event) => {
                  const eventKey = String(event.id ?? `${event.event_type}-${event.created_at}`);
                  return (
                    <GlobalEventCard
                      event={event}
                      eventKey={eventKey}
                      expanded={expandedIds[eventKey] ?? false}
                      key={eventKey}
                      onToggle={toggleExpanded}
                    />
                  );
                })}
              </div>
            )}
          </div>
        </SectionPanel>
      ) : (
        <>
          <SectionPanel kicker="Run-first Troubleshooting" title="Run diagnosis">
            {diagnosticQuery.error ? (
              <div className="rounded-[24px] border border-danger/15 bg-danger/8 p-5">
                <h3 className="text-base font-semibold text-white">Run diagnosis failed to load</h3>
                <p className="mt-2 text-sm text-muted">
                  Retry the trace query while keeping the current run filters.
                </p>
                <button
                  className="mt-4 rounded-full bg-white px-4 py-2 text-sm font-medium text-black"
                  onClick={() => void diagnosticQuery.mutate()}
                  type="button"
                >
                  Retry
                </button>
              </div>
            ) : !diagnosticQuery.data ? (
              <LoadingSkeleton />
            ) : (
              <div className="space-y-5">
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  <SummaryMetric label="Status" value={diagnosticQuery.data.status} />
                  <SummaryMetric
                    label="Failed at stage"
                    value={diagnosticQuery.data.failed_at_stage ?? "Still running"}
                  />
                  <SummaryMetric label="Errors" value={diagnosticQuery.data.summary.errors} />
                  <SummaryMetric label="Warnings" value={diagnosticQuery.data.summary.warnings} />
                  <SummaryMetric label="Suspicious jobs" value={suspiciousJobs} />
                  <SummaryMetric label="Last event" value={formatTimestamp(latestDiagnosticEvent)} />
                </div>

                <div className="space-y-3">
                  {diagnosticRows.length === 0 ? (
                    <div className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-5">
                      <p className="text-sm text-white">No warnings or errors for this run</p>
                      <p className="mt-2 text-sm text-muted">
                        Broaden the threshold to inspect the surrounding info events.
                      </p>
                      {applied.level !== "info" ? (
                        <button
                          className="mt-4 rounded-full border border-white/10 bg-white/4 px-4 py-2 text-sm font-medium text-white transition hover:border-white/20 hover:bg-white/8"
                          onClick={() => commit({ ...applied, level: "info" })}
                          type="button"
                        >
                          Show info context
                        </button>
                      ) : null}
                    </div>
                  ) : (
                    diagnosticRows.map((row) => {
                      const eventKey = String(
                        row.event.id ?? `${row.event.event_type}-${row.event.created_at}`,
                      );
                      return (
                        <DiagnosticEventCard
                          eventKey={eventKey}
                          expanded={expandedIds[eventKey] ?? false}
                          key={`${row.kind}-${eventKey}`}
                          onOpenJob={openJob}
                          onOpenTrace={openTrace}
                          onToggle={toggleExpanded}
                          row={row}
                        />
                      );
                    })
                  )}
                </div>
              </div>
            )}
          </SectionPanel>

          {(applied.jobId || applied.traceId) ? (
            <div className="grid gap-4 xl:grid-cols-2">
              {applied.jobId ? (
                <SectionPanel kicker="Job Drilldown" title="Job diagnosis">
                  {jobDiagnosisQuery.error ? (
                    <EmptyState copy="Job diagnosis failed to load." />
                  ) : !jobDiagnosisQuery.data ? (
                    <LoadingSkeleton rows={2} />
                  ) : (
                    <div className="space-y-4">
                      <div className="grid gap-3 md:grid-cols-2">
                        <SummaryMetric label="Job id" value={jobDiagnosisQuery.data.job_id} />
                        <SummaryMetric label="Status" value={jobDiagnosisQuery.data.status} />
                        <SummaryMetric
                          label="Agent"
                          value={`${jobDiagnosisQuery.data.agent_type} / ${jobDiagnosisQuery.data.host_id ?? "host unavailable"}`}
                        />
                        <SummaryMetric
                          label="Turns"
                          value={jobDiagnosisQuery.data.diagnosis.turn_count ?? 0}
                        />
                      </div>

                      <div className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4">
                        <p className="text-sm font-medium text-white">Error summary</p>
                        <p className="mt-3 text-sm text-muted">
                          {jobDiagnosisQuery.data.diagnosis.error_summary ?? "No error summary available"}
                        </p>
                      </div>

                      <div className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4">
                        <p className="text-sm font-medium text-white">Failure context</p>
                        <p className="mt-3 text-sm text-muted">
                          {`Stage ${jobDiagnosisQuery.data.diagnosis.failure_context.stage_at_failure ?? "unknown"} / host ${jobDiagnosisQuery.data.diagnosis.failure_context.host_status_at_failure ?? "unknown"} / retries ${jobDiagnosisQuery.data.diagnosis.failure_context.retry_count ?? 0}`}
                        </p>
                      </div>

                      <div className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4">
                        <p className="text-sm font-medium text-white">Last output excerpt</p>
                        <pre className="mt-3 overflow-x-auto whitespace-pre-wrap rounded-2xl bg-black/30 p-4 text-xs text-white">
                          {jobDiagnosisQuery.data.diagnosis.last_output_excerpt ??
                            "No output excerpt available"}
                        </pre>
                      </div>
                    </div>
                  )}
                </SectionPanel>
              ) : null}

              {applied.traceId ? (
                <SectionPanel kicker="Trace Drilldown" title={`Trace ${applied.traceId}`}>
                  <div className="mb-4 flex justify-end">
                    <button
                      className="rounded-full border border-white/10 bg-white/4 px-3 py-2 text-xs font-medium text-white transition hover:border-white/20 hover:bg-white/8"
                      onClick={clearTrace}
                      type="button"
                    >
                      Close trace
                    </button>
                  </div>

                  {traceLookupQuery.error ? (
                    <EmptyState copy="Trace lookup failed to load." />
                  ) : !traceLookupQuery.data ? (
                    <LoadingSkeleton rows={2} />
                  ) : (
                    <div className="space-y-4">
                      <div className="grid gap-3 md:grid-cols-2">
                        <SummaryMetric label="Origin" value={traceLookupQuery.data.origin} />
                        <SummaryMetric label="Errors" value={traceLookupQuery.data.error_count} />
                        <SummaryMetric
                          label="Affected jobs"
                          value={traceLookupQuery.data.affected_jobs.join(", ") || "None"}
                        />
                        <SummaryMetric
                          label="Duration"
                          value={`${traceLookupQuery.data.total_duration_ms ?? 0} ms`}
                        />
                      </div>

                      <div className="space-y-3">
                        {traceLookupQuery.data.events.map((event) => {
                          const eventKey = String(
                            event.id ?? `${event.event_type}-${event.created_at}`,
                          );

                          return (
                            <article
                              className="rounded-[24px] border border-white/6 bg-panel-strong/80 p-4"
                              key={eventKey}
                            >
                              <div className="flex flex-wrap items-start justify-between gap-3">
                                <div>
                                  <p className="font-mono text-sm text-white">{event.event_type}</p>
                                  <p className="mt-1 text-xs text-muted">
                                    {(event.level ?? "info")} · {(event.span_type ?? "system")}
                                  </p>
                                </div>
                                <span className="text-xs text-muted">
                                  {formatTimestamp(event.created_at)}
                                </span>
                              </div>
                            </article>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </SectionPanel>
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
