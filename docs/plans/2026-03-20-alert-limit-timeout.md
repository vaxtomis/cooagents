# Alert Limit And Timeout Alignment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Cap repeated workflow alerts at 3 sends and align timeout handling with config-driven execution limits.

**Architecture:** Reuse the existing `events` table as the source of truth for resend counting. Add a small shared matcher for event payload keys, then wire it into the scheduler and state machine so timeout/reminder behavior stays local to event producers.

**Tech Stack:** Python, asyncio, SQLite, pytest, aiosqlite

---

### Task 1: Add failing tests for capped repeat alerts

**Files:**
- Modify: `tests/test_state_machine.py`
- Modify: `tests/test_scheduler.py`

**Step 1: Write the failing test**

- Add a state machine test that ticks `DESIGN_QUEUED` without hosts 4 times and expects only 3 `host.unavailable` events/webhook sends.
- Add a scheduler test that emits `review.reminder` 4 times for the same run/stage and expects only 3 persisted events/webhook sends.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_state_machine.py tests/test_scheduler.py -k "host_unavailable or review_reminder" -v`

**Step 3: Write minimal implementation**

- Add shared event-counting helper.
- Add limited emit/notify helpers in state machine and scheduler.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_state_machine.py tests/test_scheduler.py -k "host_unavailable or review_reminder" -v`

### Task 2: Add failing tests for scheduler timeout transition

**Files:**
- Modify: `tests/test_scheduler.py`

**Step 1: Write the failing test**

- Add a scheduler test with a timed-out `DESIGN_RUNNING` job and assert the job becomes `timeout`, `job.timeout` is emitted once, and the run transitions to `FAILED`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_scheduler.py -k timeout -v`

**Step 3: Write minimal implementation**

- Preserve `timeout` as the final job status when scheduler cancels a timed-out session.
- Tick the state machine immediately after marking timeout.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_scheduler.py -k timeout -v`

### Task 3: Add failing tests for config-driven execution timeouts

**Files:**
- Modify: `tests/test_state_machine.py`

**Step 1: Write the failing test**

- Add a state machine test that injects custom timeout config and asserts design dispatch/follow-up pass that value to the executor.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_state_machine.py -k configured_execution_timeout -v`

**Step 3: Write minimal implementation**

- Replace hardcoded `1800/3600` execution literals in `StateMachine` with config lookups and fallbacks.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_state_machine.py -k configured_execution_timeout -v`
