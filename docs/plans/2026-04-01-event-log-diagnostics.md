# Event Log Diagnostics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn `/events` into a run-first troubleshooting page with anomaly-first diagnostics, job drilldown, and trace drilldown while preserving global browsing.

**Architecture:** Keep one route and split the page into global mode and run diagnostic mode based on URL state. Reuse the existing `events`, `run trace`, `job diagnosis`, and `trace lookup` endpoints, deriving the anomaly-first timeline in the frontend to avoid unnecessary backend changes.

**Tech Stack:** React 18, TypeScript, React Router, SWR, Vitest, Testing Library, Vite

---

### Task 1: Extend typed diagnostics clients and models

**Files:**
- Modify: `web/src/api/diagnostics.ts`
- Modify: `web/src/types/index.ts`
- Test: `web/src/pages/EventLogPage.test.tsx`

**Step 1: Write the failing test**

- Add a test that expects the page to request `getRunTrace`, `getJobDiagnosis`, and `getTraceLookup` in diagnostic mode.

**Step 2: Run test to verify it fails**

Run: `npm --prefix web run test -- src/pages/EventLogPage.test.tsx`
Expected: FAIL because the diagnostics APIs are not yet wired into the page test.

**Step 3: Write minimal implementation**

- Add `getJobDiagnosis(jobId)` to `web/src/api/diagnostics.ts`.
- Add `getTraceLookup(traceId, level?)` to `web/src/api/diagnostics.ts`.
- Add the response types needed for job diagnosis and trace lookup to `web/src/types/index.ts`.

**Step 4: Run test to verify it passes**

Run: `npm --prefix web run test -- src/pages/EventLogPage.test.tsx`
Expected: PASS after the page is updated in later tasks.

**Step 5: Commit**

```bash
git add web/src/api/diagnostics.ts web/src/types/index.ts web/src/pages/EventLogPage.test.tsx
git commit -m "feat: add event diagnostics data clients"
```

### Task 2: Rework Event Log page for global and diagnostic modes

**Files:**
- Modify: `web/src/pages/EventLogPage.tsx`
- Test: `web/src/pages/EventLogPage.test.tsx`

**Step 1: Write the failing test**

- Add diagnostic-mode assertions for:
  - summary rendering
  - anomaly/context grouping
  - empty anomaly fallback
  - job diagnosis open
  - trace drawer open

**Step 2: Run test to verify it fails**

Run: `npm --prefix web run test -- src/pages/EventLogPage.test.tsx`
Expected: FAIL because the current page only supports global event browsing.

**Step 3: Write minimal implementation**

- Keep the existing global mode when `runId` is missing.
- Add diagnostic mode when `runId` exists.
- Fetch run trace in diagnostic mode with default `warning` threshold.
- Derive anomaly/context rows in the frontend.
- Add URL-backed `jobId`, `eventType`, and `traceId` filters.
- Open job diagnosis and trace drilldown panels on demand.

**Step 4: Run test to verify it passes**

Run: `npm --prefix web run test -- src/pages/EventLogPage.test.tsx`
Expected: PASS

**Step 5: Commit**

```bash
git add web/src/pages/EventLogPage.tsx web/src/pages/EventLogPage.test.tsx
git commit -m "feat: add run diagnostics mode to event log"
```

### Task 3: Verify regressions and build

**Files:**
- Test: `web/src/App.test.tsx`
- Test: `web/src/pages/DashboardPage.test.tsx`
- Test: `web/src/pages/RunsListPage.test.tsx`
- Test: `web/src/pages/RunDetailPage.test.tsx`
- Test: `web/src/pages/EventLogPage.test.tsx`

**Step 1: Run focused regression tests**

Run: `npm --prefix web run test -- src/App.test.tsx src/pages/DashboardPage.test.tsx src/pages/RunsListPage.test.tsx src/pages/RunDetailPage.test.tsx src/pages/EventLogPage.test.tsx`
Expected: PASS

**Step 2: Run build**

Run: `npm --prefix web run build`
Expected: PASS

**Step 3: Commit**

```bash
git add web/src/App.test.tsx web/src/pages/EventLogPage.tsx web/src/pages/EventLogPage.test.tsx web/src/api/diagnostics.ts web/src/types/index.ts docs/plans/2026-04-01-event-log-diagnostics-design.md docs/plans/2026-04-01-event-log-diagnostics.md
git commit -m "feat: upgrade event log diagnostics"
```
