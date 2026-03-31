# Merge Queue Conflict View Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add conflict inspection and resolve-requeue actions to the merge queue without changing the overall page structure.

**Architecture:** Keep the existing queue page and extend the detail pane. Fetch live conflict file data only for the selected conflict item, fall back to queue metadata when needed, and revalidate queue state after a successful resolve.

**Tech Stack:** React 18, TypeScript, SWR, Vitest, Testing Library, Vite

---

### Task 1: Add failing tests for conflict inspection and requeue

**Files:**
- Modify: `web/src/pages/MergeQueuePage.test.tsx`

**Step 1: Write the failing test**

- Extend the existing test coverage to assert:
  - conflict detail fetch for a selected conflict row
  - fallback to queue conflict files when live fetch fails
  - successful `Resolve and requeue`
  - failed `Resolve and requeue`

**Step 2: Run test to verify it fails**

Run: `npm --prefix web run test -- src/pages/MergeQueuePage.test.tsx`
Expected: FAIL because the page does not yet fetch conflict details or expose resolve actions.

### Task 2: Add merge queue conflict API helpers and minimal types

**Files:**
- Modify: `web/src/api/repos.ts`

**Step 1: Add minimal implementation**

- Add `getRunConflicts(runId)`
- Add `resolveRunConflict(runId, by)`

**Step 2: Re-run the page test**

Run: `npm --prefix web run test -- src/pages/MergeQueuePage.test.tsx`
Expected: Still FAIL until the page implementation is updated.

### Task 3: Implement conflict detail mode in the queue page

**Files:**
- Modify: `web/src/pages/MergeQueuePage.tsx`

**Step 1: Add minimal implementation**

- Fetch conflict details for the selected conflict item.
- Show a conflict-focused detail panel.
- Add `Resolve and requeue`.
- Fallback to queue item conflict files when detail fetch fails.

**Step 2: Re-run the page test**

Run: `npm --prefix web run test -- src/pages/MergeQueuePage.test.tsx`
Expected: PASS

### Task 4: Verify regressions and build

**Files:**
- Test: `web/src/App.test.tsx`
- Test: `web/src/pages/EventLogPage.test.tsx`
- Test: `web/src/pages/MergeQueuePage.test.tsx`

**Step 1: Run focused regression tests**

Run: `npm --prefix web run test -- src/App.test.tsx src/pages/EventLogPage.test.tsx src/pages/MergeQueuePage.test.tsx`
Expected: PASS

**Step 2: Run build**

Run: `npm --prefix web run build`
Expected: PASS
