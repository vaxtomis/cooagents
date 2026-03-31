# Event Log Diagnostics Design

**Date:** 2026-04-01

**Scope:** Upgrade `/events` into a run-first troubleshooting surface while preserving the existing global event browser.

## Goals

- Keep `/events` usable as a global event browser.
- Add a `run_id` driven diagnostic mode optimized for fault investigation.
- Surface anomalies first, then let operators drill into `job_id` and `trace_id`.
- Reuse the existing diagnostics APIs where possible and avoid backend changes unless the frontend hits a hard data gap.

## Approved Direction

- Primary entry is `run_id`.
- Default diagnostic view is anomaly-first, not flat chronological browsing.
- `job_id` is the secondary drilldown.
- `trace_id` is a tertiary deep-dive, not the first screen.

## Page Modes

### Global Mode

- Activated when the page URL has no `runId`.
- Continues to use `GET /api/v1/events`.
- Keeps the existing filter and pagination behavior for broad event browsing.

### Run Diagnostic Mode

- Activated when the page URL includes `runId`.
- Uses `GET /api/v1/runs/{run_id}/trace` as the primary data source.
- Shows:
  - run-level diagnosis summary
  - anomaly-first timeline
  - job diagnosis panel
  - trace drilldown drawer

## Information Hierarchy

1. Run diagnosis summary
   - current stage
   - failed stage
   - error count
   - warning count
   - suspicious job count
   - recent event timing
2. Anomaly-first timeline
   - default threshold is `warning`
   - anomaly events are highlighted
   - nearby context events are included for local causality
3. Drilldowns
   - click `job_id` to open job diagnosis
   - click `trace_id` to open trace drawer

## Data Flow

- Global mode:
  - `GET /api/v1/events`
- Run diagnostic mode:
  - `GET /api/v1/runs/{run_id}/trace`
  - `GET /api/v1/jobs/{job_id}/diagnosis`
  - `GET /api/v1/traces/{trace_id}`

### Diagnostic Filters

- URL-backed:
  - `runId`
  - `level`
  - `spanType`
  - `jobId`
  - `eventType`
  - `traceId`
- Default trace request:
  - `level=warning`
  - `limit=200`
  - no span type filter

### Timeline Construction

- Frontend derives the anomaly-first list from `run trace` events.
- Include all warning/error events.
- For each anomaly, include up to two events before and after as context.
- Deduplicate by event identity and render in chronological order.
- Mark each row as `Anomaly` or `Context`.

## Error Handling

- Missing `runId` in diagnostic mode shows a `Run not found` state.
- Trace fetch failures preserve filters and offer retry.
- Empty anomaly results show a clear `No warnings or errors for this run` state and allow widening to `info`.
- Job diagnosis or trace drilldown failures are isolated to their panel/drawer.
- Payload rendering falls back to raw strings if JSON formatting fails.

## Testing Strategy

- Extend `EventLogPage` tests to cover:
  - global mode regression
  - run diagnostic mode activation
  - anomaly + context rendering
  - empty anomaly state with level widening
  - job diagnosis panel
  - trace drawer
- Re-run existing shell and page tests to confirm routing and layout remain stable.
- Re-run frontend build.

## Out of Scope

- Live tailing for diagnostics mode
- Backend-side context-window query APIs
- Saved filters or bookmarks
- Cross-run comparison tooling
