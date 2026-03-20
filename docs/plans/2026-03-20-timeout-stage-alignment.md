# Timeout Stage Alignment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make execution timeouts start from real `running` time, persist per-job timeout budgets, and suppress stale timeout notifications after a run has already advanced.

**Architecture:** Extend `jobs` persistence with `timeout_sec` and `running_started_at`, then have the executor stamp the real running start when a prompt begins. Update the scheduler to enforce running timeouts from those persisted fields and to emit timeout notifications only when the timed-out job is still the active stage owner, carrying both `job_stage` and current run stage for notification rendering.

**Tech Stack:** Python 3.13, SQLite, pytest, asyncio

---

### Task 1: Persist job timeout metadata

**Files:**
- Modify: `db/schema.sql`
- Modify: `src/database.py`
- Modify: `src/job_manager.py`
- Test: `tests/test_job_manager.py`

### Task 2: Stamp real running start in executor

**Files:**
- Modify: `src/acpx_executor.py`
- Test: `tests/test_acpx_executor.py`

### Task 3: Enforce running timeout from running start and active job ownership

**Files:**
- Modify: `src/scheduler.py`
- Test: `tests/test_scheduler.py`

### Task 4: Render timeout notifications with current stage context

**Files:**
- Modify: `src/scheduler.py`
- Modify: `src/webhook_notifier.py`
- Test: `tests/test_scheduler.py`
- Test: `tests/test_openclaw_hooks.py`
