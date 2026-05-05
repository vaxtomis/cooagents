# Backend Development Guidelines

> Project-specific backend rules for the current `cooagents` codebase.

---

## Overview

The backend is a single FastAPI control plane with:

- route adapters in `routes/`
- domain services, state machines, storage, and registry code in `src/`
- SQLite schema in `db/schema.sql`
- pytest coverage in `tests/`

The conventions below describe how the code works today. Follow them before adding new backend code.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [DesignDoc Contracts](./design-doc-contracts.md) | Executable Markdown contract shared by DesignWork output and DevWork Step1 revalidation | Complete |
| [Directory Structure](./directory-structure.md) | Module ownership, placement rules, naming | Complete |
| [Database Guidelines](./database-guidelines.md) | SQLite, raw SQL, migrations, invariants | Complete |
| [Error Handling](./error-handling.md) | Domain exceptions, HTTP mapping, validation layers | Complete |
| [Quality Guidelines](./quality-guidelines.md) | Required patterns, forbidden patterns, testing | Complete |
| [Logging Guidelines](./logging-guidelines.md) | Logging API, levels, sanitization, secret safety | Complete |
| [Deployment & Runtime Integration](./deployment-runtime-integration.md) | Repo-local deployment CLI, service lifecycle, notifier/runtime boundaries | Complete |

---

## Pre-Development Checklist

Read these before writing backend code:

1. Always read [Directory Structure](./directory-structure.md)
2. Always read [Quality Guidelines](./quality-guidelines.md)
3. If you touch DesignWork prompt composition, DesignDoc Markdown shape, or DevWork Step1 design-doc ingestion, read [DesignDoc Contracts](./design-doc-contracts.md)
4. If you touch `db/schema.sql`, repo classes, migrations, or SQL queries, read [Database Guidelines](./database-guidelines.md)
5. If you touch a route, auth flow, validation rule, or error response, read [Error Handling](./error-handling.md)
6. If you touch background loops, retry paths, worker code, or any secret-bearing integration, read [Logging Guidelines](./logging-guidelines.md)
7. If you touch setup/bootstrap/upgrade/service commands, skill deployment, or OpenClaw/Hermes integration, read [Deployment & Runtime Integration](./deployment-runtime-integration.md)

Also read the shared thinking guides from `.trellis/spec/guides/`.

---

## Quality Check

Before finishing backend work, confirm:

- new code lives in the correct layer (`routes/`, repo, manager, state machine, storage)
- request and response shapes are encoded in Pydantic models where the API boundary needs them
- DB invariants are enforced both at the boundary and in SQLite when the risk is high
- filesystem and workspace artifact writes still flow through `WorkspaceManager` / `WorkspaceFileRegistry`
- tests cover the changed contract at the same layer where it was changed

---

## Scope Note

These docs cover the Python backend and worker-side Python code in `src/agent_worker/`.
They do not define frontend conventions for `web/`.
