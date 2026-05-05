---
name: cooagents-setup
description: Install and start cooagents from a cloned repo by calling the unified deployment CLI. Use when the user wants setup, install, bootstrap, or first start.
user-invocable: true
required_environment_variables:
  - name: AGENT_API_TOKEN
    prompt: "Optional pre-existing cooagents service token"
    help: "Leave empty on first install. The setup command will generate and write it into .env when needed."
    optional: true
metadata:
  {
    "openclaw":
      {
        "emoji": "setup",
        "always": false,
        "requires": { "bins": ["curl"] }
      },
    "hermes":
      {
        "tags": ["cooagents", "setup", "install"]
      }
  }
---

## Role

You are the setup wrapper for cooagents.
Do not manually replay the old long bootstrap procedure.
Use the repo-local deployment CLI as the single source of truth.

## Inputs

Collect only the minimum required inputs:

- `repo_path`: local path to the cloned cooagents repo
- `repo_url`: optional, only needed when the repo does not exist yet
- `admin_username`: optional, default `admin`
- `admin_password`: required only when auth env is missing or must be replaced
- `workspace_root`: optional, default `~/cooagents-workspace`
- `runtime`: `none`, `openclaw`, `hermes`, or `both`

If the repo is missing and `repo_url` is present, clone it first.
If the repo is missing and `repo_url` is absent, ask only for the missing repo location or repo URL.

## Flow

1. Confirm the repo exists:

```bash
exec ls {repo_path}/src/app.py {repo_path}/config/settings.yaml
```

2. If it does not exist and `repo_url` is available, clone it:

```bash
exec git clone {repo_url} {repo_path}
```

3. Run the unified setup command from inside the repo:

```bash
exec cd {repo_path} && python scripts/deploy.py setup --admin-username {admin_username} --admin-password '{admin_password}' --workspace-root '{workspace_root}' --runtime {runtime}
```

Use `runtime=none` when the user only wants cooagents itself.
Use `runtime=openclaw`, `runtime=hermes`, or `runtime=both` only when the user explicitly wants notifier/runtime integration on the same machine.

4. The CLI is responsible for:

- dependency install and web build
- database initialization
- auth env generation and `.env` updates
- service start/restart
- health and dashboard validation
- optional OpenClaw/Hermes local integration

Do not duplicate those steps in this Skill unless the CLI fails and you are diagnosing.

## Success Criteria

Treat setup as successful only when the CLI completes successfully.
The command itself validates:

- `/health` returns status `ok`
- `/` returns HTML

## Follow-up

If the user later changes notifier/runtime integration, use:

```bash
exec cd {repo_path} && python scripts/deploy.py integrate-runtime --runtime {runtime} --restart-service
```

If the user needs an explicit manual skill sync, use:

```bash
exec cd {repo_path} && python scripts/deploy.py sync-skills
```

## Troubleshooting

Read only when needed:

- `references/troubleshooting.md`
- `references/hermes-integration.md`

## Notes

- Repo-first is the canonical happy path.
- This Skill is now a thin wrapper over `python scripts/deploy.py setup`.
- If the runtime has not installed this Skill yet, the host agent may read this file directly from the cloned repo path and follow it from there.
