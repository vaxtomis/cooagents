# Error Handling

> How errors are handled in this project.

---

## Overview

The backend uses project-specific exceptions for domain and validation failures, then maps them to JSON responses in `src/app.py`.

Default pattern:

- Pydantic validation handles payload shape and returns 422
- domain code raises `BadRequestError`, `NotFoundError`, `ConflictError`, or `EtagMismatch`
- auth code raises `AuthError`
- `src/app.py` converts those into stable JSON responses

Keep HTTP translation at the app or route boundary, not inside repo and manager code.

---

## Error Types

Defined in `src/exceptions.py`:

- `NotFoundError` -> 404
- `ConflictError` -> 409
- `BadRequestError` -> 400
- `EtagMismatch(BadRequestError)` -> 412

Defined in `src/auth.py`:

- `AuthError` -> usually 401, sometimes 403 or 503 depending on the failure

Also handled in `src/app.py`:

- `RateLimitExceeded` -> 429
- `NotImplementedError` -> 501 with stack trace logged server-side

---

## HTTP Response Contract

### App-level mapping

Current global handlers return these shapes:

| Exception | Status | Body |
|-----------|--------|------|
| `BadRequestError` | 400 | `{"error":"bad_request","message":...}` |
| `NotFoundError` | 404 | `{"error":"not_found","message":...}` |
| `ConflictError` | 409 | `{"error":"conflict","message":...,"current_stage":...}` |
| `EtagMismatch` | 412 | `{"error":"etag_mismatch","message":...,"current_hash":...,"expected_hash":...}` |
| `AuthError` | `exc.status_code` | `{"error":"unauthenticated","message":...}` |
| `RateLimitExceeded` | 429 | `{"error":"rate_limited","message":"Too many requests"}` |
| `NotImplementedError` | 501 | `{"error":"not_implemented","message":...}` |

If you add a new exception type that should become a stable API contract, register it in `src/app.py`.

### Ordering matters for subclass handlers

`EtagMismatch` subclasses `BadRequestError`, so its handler must be registered before the 400 handler.
The current app already documents this. Keep that ordering if you refactor exception setup.

---

## Validation Layers

### 1. Pydantic model validation -> 422

Use Pydantic validators for:

- string shape
- numeric bounds
- enum membership
- same-payload cross-field invariants

Examples already in the repo:

- slug validation
- duplicate `mount_name` rejection inside `CreateDevWorkRequest`
- reserved webhook slug rejection

### 2. Route or service validation -> 400 / 404 / 409

Use explicit code checks when validation depends on runtime state:

- repo existence
- repo health before branch inspection
- workspace active vs archived
- allowed SSH key roots
- path and upload size checks

This layer should raise project exceptions, not return ad-hoc dicts.

### 3. DB constraints as the final safety net

Use SQLite constraints for race-sensitive or persistence-level invariants.
Catch and translate user-visible violations where needed.

Current example:

- `routes/dev_works.py` catches `sqlite3.IntegrityError` from the partial unique index and converts it into `ConflictError`

---

## Boundary Rules

### Raise domain exceptions from `src/*`

Repo, manager, state machine, storage, and validation helpers should raise:

- `BadRequestError`
- `NotFoundError`
- `ConflictError`
- `EtagMismatch`
- `AuthError` from auth-specific code

Do not import FastAPI's `HTTPException` deep into domain modules unless the failure is specifically an upstream HTTP-style translation owned by the route.

### `HTTPException` is route-level only

This repo uses `HTTPException` sparingly for true protocol translation.
The current example is `POST /repos/{id}/fetch`, which turns a git-remote failure into HTTP 502.

That is acceptable because the route is translating an upstream dependency failure for the client.
Do not use `HTTPException` as a replacement for project exceptions in generic business code.

---

## Message Rules

Error messages should:

- name the failing resource or field
- include the bad value when it is safe and useful
- stay short enough to surface in UI and tests
- avoid secrets and sensitive filesystem details

Good examples from the current codebase:

- `repo not registered: 'repo-x'`
- `workspace 'ws-1' is not active`
- `duplicate mount_name in repo_refs: 'frontend'`

Bad examples:

- dumping an entire request body
- returning raw upstream stderr when it may contain secrets or server paths

---

## Sanitization Rules

When persisting or exposing remote/system errors:

- trim them
- strip non-printable characters
- avoid multi-line dumps

The current codebase already does this for:

- agent host health errors
- repo fetch errors
- worker push errors

Follow the same pattern for any new persisted error field.

---

## Good / Base / Bad Cases

### Good

- invalid payload shape rejected by Pydantic as 422
- runtime state violation rejected with `BadRequestError` or `ConflictError`
- race window closed by DB constraint and translated to 409

### Base

- route performs a small pre-check, then delegates to repo or state machine
- route uses a project exception handler to shape the final JSON

### Bad

- business logic returns `{"error": ...}` instead of raising
- repo code raises `HTTPException`
- handler leaks `ssh_key`, JWTs, webhook secrets, or raw file content

---

## Tests Required

Any change to error behavior should add or update tests for:

- status code
- response body shape
- important message content
- ordering-sensitive behavior for subclasses when relevant

Current test patterns to follow:

- route smoke tests with per-test FastAPI apps
- auth tests for 401/403 semantics
- workspace file contract tests for 400/404/412 cases
- schema or repo tests for conflict behavior

---

## Wrong vs Correct

### Wrong

```python
if ws.get("status") != "active":
    return {"error": "workspace_not_active"}
```

### Correct

```python
if ws.get("status") != "active":
    raise BadRequestError(
        f"workspace {workspace_id!r} is not active "
        f"(status={ws.get('status')!r}); writes are rejected"
    )
```

### Wrong

```python
raise HTTPException(status_code=400, detail="repo not healthy")
```

### Correct

```python
raise BadRequestError(
    f"repo {repo_id!r} not healthy "
    f"(fetch_status={fetch_status!r}); "
    f"call POST /api/v1/repos/{repo_id}/fetch first"
)
```
