# Bootstrap Web Build Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make bootstrap, setup, and upgrade require a successful local web build so fresh installs and upgrades always produce a usable dashboard.

**Architecture:** Keep `scripts/bootstrap.sh` as the single installation entry point and move the dashboard build requirement into it. Update the setup and upgrade skills plus documentation so every installation path shares the same expectations and failure handling.

**Tech Stack:** Bash, FastAPI, pytest, Vite, npm, Markdown skill docs

---

### Task 1: Add a regression test for missing SPA assets

**Files:**
- Modify: `tests/test_api.py`
- Test: `tests/test_api.py`

**Step 1: Write the failing test**

Add a test that creates a temporary project root without `web/dist/index.html`, mounts the app, requests `/`, and asserts a `404` because the SPA should not mount.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py -k missing_dist -v`
Expected: FAIL because the behavior is not covered yet.

**Step 3: Write minimal implementation**

If needed, adjust the test helper or routing behavior so the missing-dist case is explicitly covered without changing the intended semantics.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_api.py -k missing_dist -v`
Expected: PASS

### Task 2: Make bootstrap build the dashboard locally

**Files:**
- Modify: `scripts/bootstrap.sh`

**Step 1: Write the failing verification**

Use the test outcome from Task 1 plus a manual bootstrap review to identify the missing web build stage.

**Step 2: Run verification to confirm the gap**

Run: inspect `scripts/bootstrap.sh`
Expected: No `npm` check, no `web` dependency install, no `web` build, no `web/dist/index.html` validation.

**Step 3: Write minimal implementation**

- Add an `npm --version` check
- Add a `web` build phase using `npm ci` and `npm run build`
- Fail if `web/dist/index.html` is absent
- Update output text to mention dashboard build

**Step 4: Run verification to verify behavior**

Run: `bash scripts/bootstrap.sh`
Expected: exits `0` only when backend setup and dashboard build both succeed

### Task 3: Update setup and upgrade skills

**Files:**
- Modify: `skills/cooagents-setup/SKILL.md`
- Modify: `skills/cooagents-upgrade/SKILL.md`
- Modify: `skills/cooagents-setup/references/troubleshooting.md`
- Modify: `skills/cooagents-upgrade/references/troubleshooting.md`

**Step 1: Write the failing verification**

Review the current skill instructions and note that they only require bootstrap success and `/health`.

**Step 2: Run verification to confirm the gap**

Run: inspect both skill files and troubleshooting references
Expected: no mention of local dashboard build or root-route HTML validation.

**Step 3: Write minimal implementation**

- State that bootstrap installs backend dependencies and builds the dashboard
- Add a root-route validation step after startup/restart
- Add troubleshooting for `npm ci`, `npm run build`, and missing `web/dist/index.html`

**Step 4: Run verification to verify behavior**

Run: inspect updated docs
Expected: setup and upgrade instructions align with bootstrap behavior

### Task 4: Update README

**Files:**
- Modify: `README.md`

**Step 1: Write the failing verification**

Review installation and skill sections for outdated bootstrap descriptions.

**Step 2: Run verification to confirm the gap**

Run: inspect `README.md`
Expected: bootstrap described without dashboard build requirements.

**Step 3: Write minimal implementation**

- Update install docs to mention local dashboard build
- Update skill summaries/tables so bootstrap includes dashboard build
- Clarify that a healthy install should expose both API docs and the dashboard root

**Step 4: Run verification to verify behavior**

Run: inspect `README.md`
Expected: documentation matches the updated bootstrap and skills

### Task 5: Verify end to end

**Files:**
- Verify: `tests/test_api.py`
- Verify: `scripts/bootstrap.sh`
- Verify: `web/package.json`

**Step 1: Run targeted backend tests**

Run: `pytest tests/test_api.py -k "spa or missing_dist" -v`
Expected: PASS

**Step 2: Run frontend build**

Run: `npm run build`
Workdir: `web`
Expected: PASS

**Step 3: Run bootstrap or equivalent verification**

Run: `bash scripts/bootstrap.sh`
Expected: PASS with dashboard build included
