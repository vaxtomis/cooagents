---
name: cooagents-upgrade
description: Upgrade an existing cooagents repo by calling the unified deployment CLI. Use when the user wants upgrade, update, or repair after changing repo contents.
user-invocable: true
required_environment_variables:
  - name: AGENT_API_TOKEN
    prompt: "Existing cooagents service token"
    help: "Used only when the operator wants to inspect live API state separately. The upgrade command itself does not require interactive login."
    required_for: "cooagents-upgrade"
metadata:
  {
    "openclaw":
      {
        "emoji": "upgrade",
        "always": false,
        "requires": { "bins": ["curl"] }
      },
    "hermes":
      {
        "tags": ["cooagents", "upgrade", "update"]
      }
  }
---

## Role

You are the upgrade wrapper for cooagents.
Do not manually replay the old multi-stage shell flow when the unified deployment CLI can do it directly.

## Input

Collect:

- `repo_path`: local path to the existing cooagents repo

If the repo path is missing, ask for it.

## Flow

Run the unified upgrade command:

```bash
exec cd {repo_path} && python scripts/deploy.py upgrade
```

The CLI is responsible for:

- optional `git pull origin main`
- dependency refresh
- web rebuild
- database re-initialization / migration path
- service restart
- health and dashboard validation

Do not duplicate those steps in this Skill unless the CLI fails and you are diagnosing.

## Success Criteria

Treat upgrade as successful only when the CLI completes successfully.
The command itself validates:

- `/health` returns status `ok`
- `/` returns HTML

## Follow-up

If the user also changed local notifier/runtime integration after the upgrade:

```bash
exec cd {repo_path} && python scripts/deploy.py integrate-runtime --runtime {runtime} --restart-service
```

If the user wants an explicit skill sync without waiting for the next service startup:

```bash
exec cd {repo_path} && python scripts/deploy.py sync-skills
```

## Troubleshooting

Read only when needed:

- `references/troubleshooting.md`

## Notes

- This Skill is now a thin wrapper over `python scripts/deploy.py upgrade`.
- The canonical flow is repo-first and CLI-first.
