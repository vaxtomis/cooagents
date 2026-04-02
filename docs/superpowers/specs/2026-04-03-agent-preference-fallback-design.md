# Agent Preference & Fallback for Design/Dev Phases

**Date:** 2026-04-03
**Status:** Approved

## Problem

Both design and dev phases are hardcoded to specific agent types (`claude` for design, `codex` for dev). Users cannot choose which agent to use, and there is no fallback when the assigned agent type has no available hosts.

## Solution

Add a preference-based agent selection with automatic fallback for both phases.

## Design

### 1. Configuration (`config/settings.yaml`)

New fields under the top level:

```yaml
preferred_design_agent: "claude"   # default
preferred_dev_agent: "claude"      # default
```

Valid values: `"claude"`, `"codex"`.

### 2. Data Model

#### `CreateRunRequest` (`src/models.py`)

```python
design_agent: str | None = None   # omit to use config default
dev_agent: str | None = None      # omit to use config default
```

#### `runs` table (`db/schema.sql`)

```sql
design_agent TEXT DEFAULT 'claude' CHECK(design_agent IN ('claude','codex')),
dev_agent    TEXT DEFAULT 'claude' CHECK(dev_agent IN ('claude','codex')),
```

### 3. Run Creation (`state_machine.py` — `create_run()`)

- Accept `design_agent` and `dev_agent` parameters.
- When `None`, read from config `preferred_design_agent` / `preferred_dev_agent`.
- Store resolved values into the `runs` row.

### 4. Dispatch Logic (`state_machine.py`)

Both `_tick_design_queued()` and `_tick_dev_queued()` change from hardcoded agent type to:

1. Read preferred agent from `run["design_agent"]` / `run["dev_agent"]`.
2. Call `select_host(preferred)`.
3. If no host available, determine fallback (`"claude"` if preferred is `"codex"`, vice versa).
4. Call `select_host(fallback)`.
5. On fallback success:
   - Emit `agent.fallback` event with `{"preferred", "actual", "stage"}`.
   - Send notification via `notify_channel` informing the user which agent was actually used.
6. If both fail, emit `host.unavailable` (existing behavior).

### 5. Running Phases (`_tick_design_running()`, `_tick_dev_running()`)

Replace hardcoded agent type strings in `close_session()` and `send_followup()` with the actual agent type from the job record's `agent_type` field.

### 6. Executor (`acpx_executor.py`)

No changes needed. `_resolve_agent()` and `_get_allowed_tools()` already route correctly based on the passed `agent_type` parameter.

### 7. Route Layer (`routes/runs.py`)

Pass `design_agent` and `dev_agent` from the request to `sm.create_run()`.

## Files to Modify

| File | Change |
|------|--------|
| `config/settings.yaml` | Add `preferred_design_agent`, `preferred_dev_agent` |
| `src/models.py` | Add `design_agent`, `dev_agent` to `CreateRunRequest` |
| `src/config.py` | Parse new config fields |
| `db/schema.sql` | Add columns to `runs` table |
| `routes/runs.py` | Pass new fields to `create_run()` |
| `src/state_machine.py` | `create_run()`, `_tick_design_queued()`, `_tick_dev_queued()`, `_tick_design_running()`, `_tick_dev_running()` |

## Constraints

- Agent type values restricted to `"claude"` and `"codex"` via DB CHECK constraint.
- Fallback is always the opposite agent: `claude` <-> `codex`.
- Fallback triggers a user notification via `notify_channel`.
