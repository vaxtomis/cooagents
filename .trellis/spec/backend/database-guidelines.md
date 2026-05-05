# Database Guidelines

> Database patterns and conventions for this project.

---

## Overview

The project uses:

- SQLite
- `aiosqlite`
- one project-local wrapper: `src.database.Database`
- raw SQL, not an ORM

The source of truth for the schema is `db/schema.sql`.
Forward-only compatibility migrations live in `src/database.py::Database._migrate()`.

---

## Stack And Ownership

### Authoritative files

- `db/schema.sql`: tables, indexes, `CHECK` constraints, `FOREIGN KEY`s, seed rows
- `src/database.py`: connection setup, WAL mode, busy timeout, forward-only migration steps
- repo classes in `src/`: SQL CRUD for one table family

### Core DB surface

```python
await db.execute(sql, params)
await db.execute_rowcount(sql, params)
await db.fetchone(sql, params)
await db.fetchall(sql, params)

async with db.transaction():
    ...
```

Use these APIs instead of talking to `aiosqlite` directly from application code.

---

## Query Patterns

### Use parameterized SQL

Always bind user or runtime values through parameters:

```python
row = await db.fetchone(
    "SELECT * FROM repos WHERE id=?",
    (repo_id,),
)
```

Do not interpolate user-controlled values into SQL strings.
The only acceptable dynamic SQL in this repo is code-owned structure such as placeholder counts for `IN (?, ?, ?)` built from a trusted list length.

### Put SQL in repo classes or tightly scoped managers

Good:

```python
class RepoRegistryRepo:
    async def get(self, id: str) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM repos WHERE id=?", (id,))
        return dict(row) if row is not None else None
```

Bad:

```python
@router.get("/repos/{repo_id}")
async def get_repo(repo_id: str, request: Request):
    return await request.app.state.db.fetchone(
        f"SELECT * FROM repos WHERE id='{repo_id}'"
    )
```

### Use `execute_rowcount` for compare-and-swap or conditional updates

If correctness depends on whether an `UPDATE` matched a row, use `execute_rowcount`.
Examples include state transitions and conditional writes.

### Use explicit transactions for multi-statement invariants

Wrap logical units that must commit or roll back together:

```python
async with self.db.transaction():
    existing = await self.db.fetchone(...)
    if existing:
        await self.db.execute(...)
    else:
        await self.db.execute(...)
```

Repo classes already follow this pattern for `upsert()` and CAS-like behavior.

---

## Schema And Migration Rules

### `db/schema.sql` is the base shape

Every table and index must be representable from scratch by `db/schema.sql`.
A fresh database must become valid with `Database.connect()` alone.

### `_migrate()` is for changes `CREATE TABLE IF NOT EXISTS` cannot apply

Use `Database._migrate()` only for forward-only compatibility steps such as:

- `ALTER TABLE ... ADD COLUMN`
- column rename
- table rebuild for a changed `CHECK` constraint
- normalization of legacy enum values

Every migration step must be:

- idempotent
- gated by `PRAGMA table_info(...)` or `sqlite_master`
- safe to run on both fresh DBs and already-migrated DBs

Current examples already in the codebase:

- `repos.credential_ref -> ssh_key_path`
- `workspace_files` rebuild to add the `feedback` kind
- added `current_progress_json`, `session_anchor_path`, and `worktree_path`

### No external migration framework

Do not introduce Alembic-style parallel migration history unless the project explicitly adopts it.
The current operational model is:

`schema.sql` + `_migrate()`

---

## Contracts And Naming

### ID prefixes

Keep new primary keys aligned with current prefixes:

- `ws-` workspace
- `des-` design doc
- `desw-` design work
- `dev-` dev work
- `note-` iteration note
- `rev-` review
- `wf-` workspace file
- `repo-` repo registry row
- `ah-` agent host
- `ad-` agent dispatch

### Timestamps

Use UTC ISO 8601 strings from small local helpers such as `_now()`:

```python
return datetime.now(timezone.utc).isoformat()
```

Do not mix epoch ints and ISO strings in the same table family.

### Paths

- workspace file paths stored in DB are workspace-relative POSIX strings
- workspace slug is implicit through `workspace_id`
- do not store Windows backslashes or absolute paths in `workspace_files.relative_path`

### Enums And Frozen Sets

When a table uses a `CHECK` enum, keep the Python side in lockstep:

- `src.models` enums or Literals
- frozen validation sets in repo/storage code
- parity tests in `tests/`

Example: `workspace_files.kind` is enforced in both SQL and `src/storage/registry.py`.

---

## Invariant Strategy

Critical invariants are enforced twice:

1. Fast path or DTO validation for good API errors
2. SQLite `UNIQUE`, partial `UNIQUE`, `CHECK`, or `FOREIGN KEY` for race safety

Current examples:

- at most one active `dev_work` per `design_doc`
- at most one primary repo ref per `dev_work`
- `workspace_files.kind` restricted to a closed set
- `repo` deletion blocked while FK references exist

When adding a new cross-request invariant, prefer the same two-layer pattern.

---

## Good / Base / Bad Cases

### Good

- add a new enum value in `db/schema.sql`
- update the matching Python enum or frozen set
- add or update a migration if existing DBs need reshaping
- extend schema tests and route/repo tests together

### Base

- add a nullable column with `ALTER TABLE ... ADD COLUMN`
- leave existing rows `NULL`
- backfill lazily in the owning workflow code if needed

### Bad

- change only `schema.sql` and assume existing DBs will reshape themselves
- write direct SQL in route handlers
- bypass `WorkspaceFileRegistry` and mutate `workspace_files` plus local files separately
- add a user-visible invariant only in Python without a DB backstop

---

## Common Mistakes

### Common Mistake: forgetting the migration path

Symptom: fresh DB works, existing DB fails after deploy.

Fix: add an idempotent `_migrate()` step and a regression test in `tests/test_database.py`.

### Common Mistake: bypassing the single writer for workspace artifacts

Symptom: file exists on disk but metadata is missing or stale.

Fix: route all workspace artifact writes through `WorkspaceFileRegistry` or through a manager that delegates to it.

### Common Mistake: adding a DB enum without updating Python validators

Symptom: 500 or inconsistent 400/422 behavior across routes.

Fix: update the Pydantic model, any frozen validation set, and parity tests together.

---

## Tests Required

When you change schema or DB contracts, update tests at the same time:

- `tests/test_schema.py` for table shape, indexes, and invariant expectations
- `tests/test_database.py` for migration behavior and connection-level settings
- repo-level tests for CRUD and conflict behavior
- route tests for translated HTTP responses when DB errors are user-visible

If a change affects workspace artifact persistence, also update:

- `tests/test_workspace_file_registry.py`
- `tests/test_workspaces_route_files_endpoint.py`

---

## Wrong vs Correct

### Wrong

```python
await db.execute(f"UPDATE repos SET fetch_status='{status}' WHERE id='{repo_id}'")
```

### Correct

```python
await db.execute(
    "UPDATE repos SET fetch_status=?, updated_at=? WHERE id=?",
    (status, now, repo_id),
)
```

### Wrong

```python
Path(root / slug / "workspace.md").write_text(text)
await db.execute("INSERT INTO workspace_files ...")
```

### Correct

```python
await registry.put_markdown(
    workspace_row=ws,
    relative_path="workspace.md",
    text=text,
    kind="workspace_md",
)
```
