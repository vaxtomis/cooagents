# Quality Guidelines

> Code quality standards for backend development.

---

## Overview

Quality in this repo is enforced more by architecture and tests than by a heavy lint/type-check stack.
The key expectations are:

- preserve layer boundaries
- duplicate critical invariants at boundary and persistence layers
- write regression tests with the change
- keep filesystem, DB, and worker contracts explicit

---

## Required Patterns

### Keep route handlers thin

Routes should decode input, run minimal request-local checks, call a backend object, and shape the response.

Prefer:

- repo helpers for SQL
- managers for DB + filesystem orchestration
- state machines for multi-step workflow logic
- route-local helper functions for response projection and batch loading

### Reuse DTOs and enums

If a contract is visible at the API boundary or shared across modules, put it in `src/models.py`.
Keep Python validators aligned with SQLite `CHECK` constraints and route behavior.

### Use batch loaders on list endpoints

If a list route needs related rows, add a batch helper rather than querying per row.
Current patterns:

- `_load_repo_refs_batch` in `routes/design_works.py`
- `_load_worker_repos_batch` in `routes/dev_works.py`

### Preserve the single-writer pattern for workspace artifacts

The backend intentionally centralizes workspace file metadata and file writes through:

- `WorkspaceManager`
- `WorkspaceFileRegistry`
- `WorkspaceFilesRepo`

Do not introduce alternate write paths that bypass this chain.

### Duplicate high-risk invariants

For invariants that matter across requests or race windows:

1. validate early for a good client error
2. enforce again in the database or storage layer

Examples already present:

- unique active DevWork per design doc
- unique primary repo ref per DevWork
- CAS semantics for workspace file writes

### Add regression tests with the fix

The repo already carries many bug-driven tests with scenario comments.
Follow that style when fixing a bug or tightening a contract.

---

## Forbidden Patterns

### Do not put business logic in routes

Avoid:

- multi-step workflow logic
- direct filesystem writes
- large SQL sequences
- direct `app.state.db` mutation when a repo or manager already owns the boundary

### Do not bypass path and URL validators

Repo URLs, workspace-relative paths, and SSH key paths all have explicit validators and allow-lists.
Never reimplement a looser ad-hoc parser in a route or worker.

### Do not leak secrets or server-local details

Avoid exposing or logging:

- JWTs
- agent tokens
- webhook secrets
- OSS credentials
- `ssh_key` response fields
- raw request bodies containing credentials

### Do not add generic utility dumping grounds

Do not create `utils.py`, `helpers.py`, or `common.py` files with mixed responsibilities.
Name helpers by domain and keep them near the owning layer.

### Do not silently swallow exceptions

If the process continues after an exception, log it with enough identifiers to debug it.
If the client should see it, raise a project exception and let the boundary map it.

---

## Testing Requirements

### Route changes

Add or update FastAPI route tests using:

- per-test lightweight `FastAPI()` instances
- `httpx.AsyncClient` with `ASGITransport`
- local exception handlers in the test app

This is the dominant route-testing pattern in `tests/test_*_route.py`.

### DB or schema changes

Update:

- `tests/test_schema.py`
- `tests/test_database.py`

If a repo class owns the behavior, also add repo-specific tests.

### Storage and filesystem changes

Use:

- `tmp_path`
- `LocalFileStore`
- `FakeOSSStore` from `tests/conftest.py`

When touching worker writeback, CAS, or artifact indexing, cover both FS effects and DB metadata effects.

### Security or auth changes

Add explicit negative-path tests:

- unauthorized
- wrong token
- cross-origin rejection
- invalid origin or proxy handling
- oversized or malformed inputs

### Async workflow changes

When touching state machines or worker orchestration:

- prefer fake executors / fake LLM runners from `tests/conftest.py`
- keep tests deterministic and isolated from real binaries

---

## Code Review Checklist

- Is the code in the correct layer?
- Are request and response contracts explicit?
- Does any new invariant have both a boundary check and a persistence backstop where needed?
- Are file writes still going through the single-writer path?
- Are response and log surfaces free of secrets and unsafe local paths?
- Did the change update the tests at the same boundary it changed?
- Did a list endpoint avoid an avoidable N+1 query pattern?

---

## Common Mistakes

### Common Mistake: fixing only the happy path

Symptom: the main route works, but archived resources, stale hashes, missing rows, or duplicate refs still fail incorrectly.

Prevention: copy the repo's existing test style and add negative-path coverage alongside the happy path.

### Common Mistake: adding a runtime check without a durable invariant

Symptom: behavior is correct in single-request tests but races in production.

Prevention: follow the route-check plus SQLite-constraint pattern already used by `dev_works` and `dev_work_repos`.

### Common Mistake: introducing import cycles through `src.models` or `src.config`

Symptom: startup import failures or unexpectedly heavy import graphs.

Prevention: keep dependency direction simple and accept small constant duplication when the repo already does so intentionally.

---

## Wrong vs Correct

### Wrong

```python
async def create_repo(...):
    await request.app.state.db.execute("INSERT INTO repos ...")
    return {"ok": True}
```

### Correct

```python
async def create_repo(...):
    row = await request.app.state.repo_registry_repo.upsert(...)
    return dict(row)
```

### Wrong

```python
for row in rows:
    refs[row["id"]] = await _load_repo_refs(db, row["id"])
```

### Correct

```python
refs_by_id = await _load_repo_refs_batch(db, [r["id"] for r in rows])
```
