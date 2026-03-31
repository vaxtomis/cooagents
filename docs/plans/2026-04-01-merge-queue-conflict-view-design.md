# Merge Queue Conflict View Design

**Date:** 2026-04-01

**Scope:** Upgrade `Merge Queue` so operators can inspect conflict items, fetch the latest conflict file list, and requeue a run after resolving conflicts externally.

## Goals

- Keep the existing merge queue list and row actions intact.
- Add a conflict-focused detail mode for queue items with `status === "conflict"`.
- Reuse existing backend APIs and avoid adding new endpoints.
- Let operators requeue a resolved run directly from the queue view.

## Approved Direction

- The page remains a two-column queue/detail layout.
- Conflict support is read-first, not an in-browser merge tool.
- Conflict details come from `GET /api/v1/runs/{run_id}/conflicts`.
- Requeue uses `POST /api/v1/runs/{run_id}/resolve-conflict`.

## Data Flow

- Queue list:
  - `GET /api/v1/repos/merge-queue`
- Selected conflict details:
  - `GET /api/v1/runs/{run_id}/conflicts`
- Requeue action:
  - `POST /api/v1/runs/{run_id}/resolve-conflict`

## UI Behavior

- Non-conflict items keep the current summary detail view.
- Conflict items show:
  - conflict status
  - conflict file count
  - conflict file list
  - `Resolve and requeue` action
- If the conflict detail fetch fails, the page falls back to the queue item's `conflict_files`.
- After resolve succeeds, queue and enriched run data are revalidated.

## Error Handling

- Conflict-detail failures are isolated to the detail pane.
- `Resolve and requeue` surfaces the backend message and `current_stage` on conflict errors.
- If neither live conflict data nor queue data provides files, the UI shows `No conflict files reported`.

## Testing Strategy

- Extend `MergeQueuePage` tests for:
  - conflict detail fetch on selection
  - fallback to queue item conflict files
  - successful resolve and requeue
  - failed resolve messaging
- Re-run focused shell and page tests plus frontend build.
