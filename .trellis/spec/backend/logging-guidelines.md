# Logging Guidelines

> How logging is done in this project.

---

## Overview

The project uses the Python standard library `logging` module.

Current patterns:

- library modules define `logger = logging.getLogger(__name__)`
- the worker CLI uses a dedicated logger name: `logging.getLogger("cooagents-worker")`
- library code does not configure global logging
- the worker CLI is allowed to call `logging.basicConfig(...)` because it is an entry point

Do not introduce `structlog`, `loguru`, or per-module logging configuration unless the project explicitly adopts them.

---

## Logger Construction

Preferred pattern for modules:

```python
import logging

logger = logging.getLogger(__name__)
```

CLI entry points may use a stable service name when that helps operators separate logs:

```python
logger = logging.getLogger("cooagents-worker")
```

Do not call `logging.basicConfig()` from reusable library modules under `src/`.

---

## Log Levels

### `debug`

Use for noisy internal signals that help during development or incident debugging:

- local file writes
- OSS object operations
- malformed optional heartbeat JSON that should degrade gracefully

These messages should be safe to omit in normal production verbosity.

### `info`

Use for successful lifecycle milestones:

- config sync results
- startup cleanup summaries
- health or fetch loop outcomes
- skill deployment summaries
- worker materialize and push summaries

### `warning`

Use for degraded but recoverable states:

- legacy schema detected
- missing optional files such as `workspace.md`
- archived or drifted resources
- timeouts that lead to escalation rather than process death
- stale or malformed optional state that can be ignored safely

### `error` / `exception`

Use `logger.exception(...)` when you are inside `except` and want the traceback.
Use `logger.error(...)` only when a traceback is not useful or would be misleading.

In this repo, `logger.exception(...)` is the default for catch-and-continue paths.

---

## Message Format

Use parameterized logging, not string interpolation:

```python
logger.warning("workspace reconcile: fs_only=%s db_only=%s", fs_only, db_only)
```

Prefer including stable identifiers in the message:

- `workspace_id`
- `repo_id`
- `host_id`
- `design_work_id`
- `dev_work_id`
- `mount_name`

This matters because many workflows are asynchronous and multi-step.

---

## What To Log

Log:

- startup and shutdown steps that affect system readiness
- background loop progress and failures
- sync/reconcile results
- dispatch lifecycle failures and degraded fallbacks
- recoverable data inconsistencies
- external dependency failures after sanitization

If a catch block intentionally continues instead of re-raising, it should almost always log.

---

## What Not To Log

Never log:

- JWTs
- `AGENT_API_TOKEN`
- webhook secrets
- OSS credentials
- raw uploaded file bytes
- full markdown or artifact content
- full request bodies that may contain credentials

Avoid logging server-local secret-bearing paths such as private key locations unless there is a deliberate operational reason and the value is already considered safe.

Also avoid exposing those fields in API responses. The route layer already strips `ssh_key` from agent host responses.

---

## Sanitization Rules

When a remote or OS error may be persisted or surfaced:

- strip non-printable characters
- trim it to a bounded length
- avoid multiline dumps

The existing codebase already does this for:

- agent host health errors
- repo fetch errors
- worker push errors

Follow the same pattern for any new persisted error field.

---

## Library vs Entry-Point Rule

- `src/*` library code: no `basicConfig`, no handler setup, no global level changes
- CLI entry points such as `src/agent_worker/cli.py`: may configure logging for standalone execution

This keeps backend embedding safe under Uvicorn while still making the worker usable by itself.

---

## Wrong vs Correct

### Wrong

```python
logger.info(f"fetch failed for {repo_id}: {exc}")
```

### Correct

```python
logger.exception("repo fetch %s failed", repo_id)
```

### Wrong

```python
logger.info("agent token=%s", agent_token)
```

### Correct

```python
logger.info("worker materialize: pulled=%d", len(mat.pulled))
```

### Wrong

```python
logging.basicConfig(level=logging.DEBUG)
```

Place this only in an entry point, not in reusable backend modules.
