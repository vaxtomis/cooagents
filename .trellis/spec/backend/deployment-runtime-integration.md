# Deployment & Runtime Integration

> Executable contract for repo-local deployment commands, service lifecycle, and runtime integration boundaries.

---

## Scenario: Unified Repo-Local Deployment Core

### 1. Scope / Trigger

- Trigger: touching any repo-local deployment entrypoint, service lifecycle command, auth env bootstrap, skill deployment path, or OpenClaw/Hermes integration flow.
- Why this requires code-spec depth:
  - introduces command signatures under `scripts/deploy.py`
  - spans CLI -> config -> startup -> worker/runtime integration layers
  - mutates secrets and runtime config files
  - has both local and SSH-driven execution paths

### 2. Signatures

- Canonical CLI entrypoint:
  - `python scripts/deploy.py bootstrap`
  - `python scripts/deploy.py setup --admin-password <value> [--admin-username <value>] [--workspace-root <path>] [--runtime none|openclaw|hermes|both] [--replace-env] [--skip-start]`
  - `python scripts/deploy.py upgrade [--branch <name>] [--skip-pull]`
  - `python scripts/deploy.py service start|stop|restart|status [--force] [--ignore-missing]`
  - `python scripts/deploy.py integrate-runtime --runtime openclaw|hermes|both [--restart-service]`
  - `python scripts/deploy.py sync-skills`
- Compatibility wrapper:
  - `./scripts/bootstrap.sh` delegates to `python scripts/deploy.py bootstrap`

### 3. Contracts

#### Request / Command Contracts

- `setup`
  - owns repo-local bootstrap, auth env creation/update, workspace root persistence, optional notifier/runtime integration, and optional service start
- `upgrade`
  - owns repo refresh, dependency refresh, rebuild, and service restart
- `service`
  - manages the repo-local cooagents process through `.coop/cooagents.pid`
- `integrate-runtime`
  - configures OpenClaw and/or Hermes against the current repo without redefining the execution-host model
- `sync-skills`
  - deploys the current `skills/` bundle to configured runtime targets

#### Environment / File Contracts

- `.env` may contain:
  - `ADMIN_USERNAME`
  - `ADMIN_PASSWORD_HASH`
  - `JWT_SECRET`
  - `AGENT_API_TOKEN`
  - `OPENCLAW_HOOK_TOKEN`
  - `HERMES_WEBHOOK_SECRET`
- Service lifecycle files:
  - pid file: `.coop/cooagents.pid`
  - log file: `cooagents.log`
- `config/settings.yaml`
  - `security.workspace_root` is the canonical persisted workspace-root knob for repo-local deployment
  - `openclaw.hooks.*` stores OpenClaw notifier integration toggles
  - `hermes.webhook.*` stores Hermes notifier integration toggles

#### Architectural Boundary Contracts

- Execution plane:
  - `agent_hosts`
  - SSH reachability
  - `cooagents-worker`
  - remote CLI invocation
- Notification / interaction plane:
  - OpenClaw
  - Hermes
  - skill sync
  - hooks / webhooks
  - runtime-facing env injection
- Do not model OpenClaw/Hermes as task execution hosts.

### 4. Validation & Error Matrix

- Python < 3.11 -> fail setup/bootstrap early
- missing `git` / `node` / `npm` -> fail bootstrap early
- missing `web/package.json` or `web/package-lock.json` -> fail bootstrap
- missing auth env + no `--admin-password` during `setup` -> fail setup
- `/health` not returning `{status: "ok"}` after start/restart -> fail setup/upgrade/service restart
- dashboard root not returning HTML after start/restart -> fail setup/upgrade/service restart
- missing `AGENT_API_TOKEN` when integrating runtimes after setup -> fail runtime integration
- SSH runtime target unreachable during `sync-skills` -> record per-target failure, do not silently pretend success

### 5. Good / Base / Bad Cases

- Good:
  - repo already cloned
  - `python scripts/deploy.py setup --admin-password 'secret123'`
  - service becomes healthy and dashboard root returns HTML
- Base:
  - repo already configured
  - `python scripts/deploy.py upgrade --skip-pull`
  - dependencies rebuild and service restarts cleanly
- Bad:
  - editing `SKILL.md` to add new deployment logic without adding or changing `scripts/deploy.py`
  - wiring OpenClaw/Hermes into `agent_hosts`
  - adding a second deployment command surface that diverges from `scripts/deploy.py`

### 6. Tests Required

- Command-surface tests
  - `scripts/bootstrap.sh` delegates to unified CLI
  - setup Skill references `python scripts/deploy.py setup`
  - upgrade Skill references `python scripts/deploy.py upgrade`
- Env contract tests
  - `.env` read/write round-trip preserves shell-safe quoted values
  - auth bundle generation still emits all required keys
- Runtime integration tests
  - SSH skill deployment path uses asyncssh/scp rather than returning "not implemented"
- Documentation tests
  - README points to repo-first unified setup path

### 7. Wrong vs Correct

#### Wrong

```text
cooagents-setup SKILL.md contains the real deployment state machine,
while bootstrap.sh and upgrade logic each drift separately.
```

#### Correct

```text
scripts/deploy.py is the executable source of truth.
bootstrap.sh is a wrapper.
Skills collect minimal input and call the same CLI.
```

---

## Design Decision: Execution Plane vs Runtime Integration Plane

**Context**: both remote execution and OpenClaw/Hermes integration involve remote machines, SSH, env wiring, and startup behavior. The superficial overlap makes it easy to collapse them into one host abstraction.

**Decision**: keep the planes separate.

- Execution plane:
  - selects where cooagents tasks actually run
  - stays centered on `agent_hosts` and `cooagents-worker`
- Runtime integration plane:
  - selects where progress/interaction runtimes receive notifications and skills
  - stays centered on OpenClaw/Hermes-specific integration config

**Why**: OpenClaw/Hermes consume cooagents progress and provide interaction entrypoints, but they are not the task execution substrate.

#### Wrong

```text
Treat OpenClaw or Hermes as if they were interchangeable with agent_hosts.
```

#### Correct

```text
Remote workers execute tasks.
OpenClaw/Hermes consume progress and host Skills.
The deployment CLI may orchestrate both, but the models remain distinct.
```
