# Dashboard Spec Gap Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Align the merged dashboard implementation with the approved dashboard spec by fixing the remaining Dashboard KPI and Run Detail layout/history gaps.

**Architecture:** Keep the existing FastAPI and React data flow intact. Limit changes to the existing frontend pages and tests: update Dashboard KPI derivation, add Run Detail approval history and stage history views, and reorganize the lower detail surface into real tabs without changing backend contracts.

**Tech Stack:** React 18, TypeScript, Vitest, Testing Library, SWR

---

### Task 1: Lock the expected Dashboard KPI behavior in tests

**Files:**
- Modify: `web/src/pages/DashboardPage.test.tsx`

**Step 1: Write the failing test**
- Update the dashboard test fixture to include merge-stage and completed runs.
- Assert the five stat cards render `运行中 / 待审批 / 合并中 / 失败 / 已完成` with the expected counts.

**Step 2: Run test to verify it fails**

Run: `npm --prefix web run test -- src/pages/DashboardPage.test.tsx`
Expected: FAIL because the current page still renders the old KPI labels and counts.

**Step 3: Write minimal implementation**
- Update `web/src/pages/DashboardPage.tsx` KPI calculations and labels.

**Step 4: Run test to verify it passes**

Run: `npm --prefix web run test -- src/pages/DashboardPage.test.tsx`
Expected: PASS

### Task 2: Lock the expected Run Detail approval and tab behavior in tests

**Files:**
- Modify: `web/src/pages/RunDetailPage.test.tsx`

**Step 1: Write the failing test**
- Add approvals and steps to the mocked run payload.
- Assert the detail page renders an approval history card.
- Assert the lower content uses tabs for `Artifacts / Agent输出 / 事件追踪 / Stage历史`.
- Assert `Stage历史` shows step transitions after selecting that tab.

**Step 2: Run test to verify it fails**

Run: `npm --prefix web run test -- src/pages/RunDetailPage.test.tsx`
Expected: FAIL because approval history and stage history tabs are not rendered yet.

**Step 3: Write minimal implementation**
- Update `web/src/pages/RunDetailPage.tsx` to render the approval card and tabbed lower sections.

**Step 4: Run test to verify it passes**

Run: `npm --prefix web run test -- src/pages/RunDetailPage.test.tsx`
Expected: PASS

### Task 3: Run focused regression

**Files:**
- Verify: `web/src/pages/DashboardPage.tsx`
- Verify: `web/src/pages/RunDetailPage.tsx`
- Verify: `web/src/pages/DashboardPage.test.tsx`
- Verify: `web/src/pages/RunDetailPage.test.tsx`

**Step 1: Run focused tests**

Run: `npm --prefix web run test -- src/pages/DashboardPage.test.tsx src/pages/RunDetailPage.test.tsx`
Expected: PASS

**Step 2: Run build**

Run: `npm --prefix web run build`
Expected: PASS
