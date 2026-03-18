# cooagents-setup Skill Design

## Summary

Add a `cooagents-setup` OpenClaw Skill that guides the Agent through installing and starting the cooagents service on the local machine. Pure Skill — no new Python code, no script changes. The Agent executes shell commands via `exec` following the SKILL.md instructions.

## Motivation

Currently the `cooagents-workflow` Skill assumes the cooagents service is already running at `http://127.0.0.1:8321`. There's no automated way for OpenClaw to bootstrap the service from scratch. Users must manually clone the repo, install dependencies, configure hosts, and start the server before the workflow Skill becomes useful.

A setup Skill closes this gap: given a repo path or URL, OpenClaw can autonomously install and start cooagents, making the system self-bootstrapping.

## Design

### 1. Skill Structure

```
skills/cooagents-setup/
├── SKILL.md                    # Core installation logic (injected into Agent prompt)
└── references/
    └── troubleshooting.md      # Common issue resolution (Agent reads on demand)
```

**Metadata (JSON-in-YAML format, matching `cooagents-workflow`):**

```yaml
metadata:
  {
    "openclaw":
      {
        "emoji": "🔧",
        "always": false,
        "requires": { "bins": ["curl"] }
      }
  }
```

### 2. Installation Flow (6 Stages)

SKILL.md instructs the Agent to execute these stages in order:

| Stage | Action | Success Criteria |
|-------|--------|------------------|
| ① Locate code | If `repo_path` provided, verify it exists and contains `src/app.py` and `config/settings.yaml`. If path doesn't exist and `repo_url` provided, `git clone repo_url repo_path`. If neither, ask user. | Directory exists with `src/app.py` and `config/settings.yaml` |
| ② Check environment | `python3 --version` (≥3.11), `git --version`, `node --version` | All three commands succeed, Python ≥ 3.11 |
| ③ Install acpx | `acpx --version` — skip if present. Otherwise `npm install -g acpx@latest`. Fallback: note `npx acpx@latest` as runtime alternative. | `acpx --version` succeeds or npx fallback noted |
| ④ Install dependencies | `cd {repo_path} && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt` (venv is the primary path; bare `pip install` as fallback if venv fails) | Exit code 0 |
| ⑤ Initialize & start | See §2.1 and §2.2 below | Process started |
| ⑥ Health check | Poll `curl -s http://127.0.0.1:8321/health` every 3s, up to 30s | Response JSON includes `"status": "ok"` |

Each stage failure triggers the Agent to consult `references/troubleshooting.md` for resolution. If unresolvable, the Agent reports the issue to the user with context.

#### 2.1 Stage ⑤ — DB Initialization

The exact DB init command (matching `scripts/bootstrap.sh`):

```bash
mkdir -p .coop/runs .coop/jobs

python3 -c "
import sqlite3, pathlib
db_path = '.coop/state.db'
backup = db_path + '.bak'
p = pathlib.Path(db_path)
if p.exists():
    import shutil
    shutil.copy2(db_path, backup)
    print(f'  Backed up existing DB to {backup}')
conn = sqlite3.connect(db_path)
conn.executescript(pathlib.Path('db/schema.sql').read_text())
conn.close()
print('  Database initialized.')
"
```

#### 2.2 Stage ⑤ — Platform-Aware Startup

The startup command depends on the platform:

- **Linux / macOS:** `nohup .venv/bin/uvicorn src.app:app --host 127.0.0.1 --port 8321 > cooagents.log 2>&1 &`
- **Windows (Git Bash / PowerShell):** `start /b .venv/Scripts/python -m uvicorn src.app:app --host 127.0.0.1 --port 8321 > cooagents.log 2>&1`

The Agent determines the platform by checking the output of `uname -s` (if available) or `echo %OS%`.

If a venv was not created in Stage ④ (fallback path), use `uvicorn` / `python -m uvicorn` directly.

### 3. Agent Host Registration

After health check passes, the Skill instructs the Agent to register a local host:

1. `exec curl -s http://127.0.0.1:8321/api/v1/agent-hosts` — check if `local` host already exists
2. If not present: `exec curl -s -X POST http://127.0.0.1:8321/api/v1/agent-hosts -H "Content-Type: application/json" -d '{"id":"local","host":"local","agent_type":"both","max_concurrent":2}'`
3. If POST fails (duplicate/integrity error), treat as "already registered" and continue
4. Skip if already registered (idempotent)

**Note on `agent_type: "both"`:** This means the host can accept either `claude` or `codex` agent jobs. The `max_concurrent: 2` limit is shared across both agent types.

**Note on persistence:** API-registered hosts are stored in the DB and are available until the DB is reset. For persistent host configuration across DB resets, users should edit `config/agents.yaml`. The setup Skill uses the API for simplicity.

### 4. Completion Message

Agent replies with a confirmation:

```
✅ cooagents 已启动
- 服务地址：http://127.0.0.1:8321
- 健康状态：ok
- 本地 Agent 主机：已注册（claude + codex, 共享并发上限 2）
- API 文档：http://127.0.0.1:8321/docs

可以使用 /cooagents-workflow 开始创建任务。
```

### 5. Troubleshooting Reference

`references/troubleshooting.md` covers:

| Problem | Resolution |
|---------|------------|
| `python3` not found | Platform-specific install commands (brew / apt / winget) |
| Python < 3.11 | Prompt user to upgrade |
| `node` / `npm` not found | Platform-specific Node.js install commands |
| `npm install -g` permission denied | Suggest `sudo npm install -g` or `npx acpx@latest` fallback |
| `pip install` fails | Suggest venv (if not already using one); on modern Linux (PEP 668), venv is required |
| `nohup` not found (Windows) | Use `start /b python -m uvicorn ...` instead |
| Port 8321 occupied | `lsof -i :8321` (Linux/macOS) or `netstat -ano \| findstr 8321` (Windows) to identify, prompt user |
| Health check timeout (30s) | Check `cooagents.log`, common causes: DB init failure, import error |
| `git clone` fails | Network / SSH key issue, prompt user to check credentials |
| `config/settings.yaml` missing | Must clone from repo; manual directory creation is not supported |

### 6. Deployment & First-Time Bootstrap

Deployed alongside `cooagents-workflow` by `skill_deployer.py` on cooagents startup. However, for first-time installation, cooagents is not yet running so automatic deployment hasn't happened.

**First-time installation paths:**

1. **Manual placement (recommended):** User copies `skills/cooagents-setup/` from the repo to `~/.openclaw/skills/cooagents-setup/`. Then invokes `/cooagents-setup` in OpenClaw.
2. **From cloned repo:** User first clones the repo manually, then tells OpenClaw to read `{repo_path}/skills/cooagents-setup/SKILL.md` and follow the instructions.
3. **After first setup:** Subsequent installs/upgrades are automatic — cooagents startup deploys the Skill via `skill_deployer.py`.

### 7. Scope

**In scope:**
- Local machine installation only
- Clone or locate existing repo
- acpx installation via npm
- Virtual environment creation (primary path)
- Service startup and health verification (platform-aware)
- Local agent host registration

**Out of scope:**
- Remote/SSH installation (future)
- Custom port or host configuration (uses defaults)
- SSL/TLS setup
- systemd/launchd service registration
- `cooagents-workflow` Skill deployment verification (handled automatically by cooagents on startup)

### 8. Files Changed

| File | Change |
|------|--------|
| `skills/cooagents-setup/SKILL.md` | Create — core installation logic |
| `skills/cooagents-setup/references/troubleshooting.md` | Create — common issue resolution |
