# Directory Structure

> How backend code is organized in this project.

---

## Overview

`cooagents` is not a feature-folder FastAPI app. It is a control plane with a clear split between:

- HTTP adapters in `routes/`
- core domain and workflow logic in `src/`
- persistence contracts in `db/`
- regression coverage in `tests/`

Do not put business logic directly in route handlers. Routes should decode input, call a backend object, and shape the response.

---

## Directory Layout

```text
src/
  app.py                    # FastAPI composition, lifespan wiring, exception handlers
  models.py                 # Request/response DTOs, enums, shared validators
  exceptions.py             # Domain exceptions mapped at the app boundary
  auth.py                   # JWT/cookie auth and FastAPI auth dependency
  config.py                 # YAML/env loading via Pydantic settings models
  database.py               # aiosqlite wrapper + forward-only migrations

  workspace_manager.py      # Workspace DB + filesystem orchestration
  design_doc_manager.py     # DesignDoc artifact persistence
  dev_iteration_note_manager.py

  design_work_sm.py         # Long-running DesignWork state machine
  dev_work_sm.py            # Long-running DevWork state machine
  dev_work_steps.py         # Step-specific DevWork helpers

  storage/                  # FileStore abstraction + workspace_files registry
  repos/                    # Repo registry, fetcher, inspector, health loop
  agent_hosts/              # Agent host registry, SSH dispatcher, health loop
  agent_worker/             # Worker CLI used on remote agent hosts

routes/
  *.py                      # Thin HTTP adapters per resource
  _*.py                     # Route-only helpers shared across endpoints

db/
  schema.sql                # Authoritative SQLite schema and indexes

tests/
  test_*.py                 # Unit and route tests
  integration/              # External-service integration tests
```

---

## Ownership Rules

### `src/app.py` is the only composition root

Keep these concerns in `src/app.py`:

- FastAPI app creation
- middleware registration
- exception handler registration
- startup and shutdown wiring
- `app.state.*` dependency assembly
- router mounting

Do not import routers into manager or repo modules. The dependency direction is:

`src.app -> routes.* -> src.*`

### `routes/` are HTTP adapters

Routes may:

- accept Pydantic request DTOs
- do small request-local checks
- call managers, state machines, repos, or helpers
- translate read models into response DTOs

Routes should not:

- own long SQL sequences
- perform direct filesystem writes
- duplicate shared validation already encoded in `src.models`

Good pattern from the current codebase:

```python
@router.post("/dev-works", status_code=201)
async def create_dev_work(req: CreateDevWorkRequest, request: Request, response: Response):
    validated = await validate_dev_repo_refs(
        req.repo_refs,
        request.app.state.repo_registry_repo,
        request.app.state.repo_inspector,
    )
    dw = await request.app.state.dev_work_sm.create(
        workspace_id=req.workspace_id,
        design_doc_id=req.design_doc_id,
        repo_refs=validated,
        prompt=req.prompt,
        agent=req.agent.value,
    )
    response.headers["Location"] = f"/api/v1/dev-works/{dw['id']}"
    return _row_to_progress(dw)
```

Bad pattern for this repo:

```python
@router.post("/x")
async def create_x(request: Request):
    await request.app.state.db.execute("INSERT ...")
    Path("/tmp/file").write_text("...")
    return {"ok": True}
```

### `src/models.py` is the shared API and domain DTO layer

Put these in `src/models.py`:

- request models used by more than one route
- response DTOs
- enums that must stay aligned with DB `CHECK` constraints
- field and model validators at the payload boundary

Use route-local DTOs only when a model is truly local to one route module, such as `routes/auth.py:LoginRequest`.

### Repo classes own one persistence boundary

Classes such as `RepoRegistryRepo`, `AgentHostRepo`, and `WorkspaceFilesRepo` should:

- own SQL for one table family
- validate boundary inputs before writing
- return plain row dicts
- keep writes transaction-safe

They should not know about HTTP or FastAPI.

### Managers own filesystem + DB orchestration

Managers such as `WorkspaceManager` and `DesignDocManager` are the right place for operations that coordinate:

- DB rows
- workspace filesystem layout
- template rendering
- webhook emission
- registry writes

### State machines own long-running workflow logic

`DesignWorkStateMachine` and `DevWorkStateMachine` are the only place for:

- multi-step workflow transitions
- background driver scheduling
- LLM / worker dispatch orchestration
- workflow-level retry and escalation behavior

Do not smear step-transition logic into routes or repo classes.

---

## Placement Rules For New Code

When adding code:

- add a new route module under `routes/` only if a new API resource is needed
- add a new repo class under `src/...` when a table or persistence boundary needs dedicated CRUD
- add a manager when DB + filesystem + webhook coordination is required
- add a helper under `routes/_*.py` only when it is HTTP-specific and not useful outside route code
- add a new subpackage under `src/` only when there is a real boundary like `storage/`, `repos/`, or `agent_hosts/`

Do not create generic `utils.py` or `helpers.py` files at the repo root.
Prefer naming by domain: `path_validation.py`, `request_utils.py`, `git_utils.py`.

---

## Naming Conventions

- Python files use `snake_case.py`
- route files use plural resource names when they expose resource endpoints: `workspaces.py`, `repos.py`, `agent_hosts.py`
- route-only shared helpers use a leading underscore: `_repo_refs_validation.py`, `_metrics_common.py`
- repo classes end with `Repo`
- manager classes end with `Manager`
- looping background services end with `Loop`
- dispatcher classes end with `Dispatcher`
- workflow orchestrators end with `StateMachine`
- request DTOs use `Create*Request`, `Update*Request`, or `*ActionRequest`
- response/projection DTOs use `*Progress`, `*View`, `*Metrics`, `*Log`, `*Tree`

---

## Import-Cycle Rule

This repo deliberately duplicates a few regexes and enum-like constants across modules to avoid import cycles.
Examples:

- `src.config` duplicates regexes instead of importing `src.models`
- `src.models` keeps route-facing validators without importing route modules

Prefer small duplication over circular imports through `src.app`, `src.models`, or `src.config`.

---

## Examples To Follow

- `routes/dev_works.py` for a thin route module with batch-loading helpers
- `src/workspace_manager.py` for DB + filesystem orchestration
- `src/storage/registry.py` for a single writer over store + DB metadata
- `src/repos/registry.py` for a focused DB repo with boundary validation
- `src/database.py` for project-wide DB conventions and forward-only migration style
