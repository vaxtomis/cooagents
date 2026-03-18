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

**Metadata:**

```yaml
name: cooagents-setup
description: 安装并启动 cooagents 服务 — 检测环境、安装依赖、启动服务器、注册本地 Agent 主机。当用户提及安装、部署、启动 cooagents 时触发。
user-invocable: true
metadata:
  openclaw:
    emoji: "🔧"
    always: false
    requires: { bins: ["curl"] }
```

### 2. Installation Flow (6 Stages)

SKILL.md instructs the Agent to execute these stages in order:

| Stage | Action | Success Criteria |
|-------|--------|------------------|
| ① Locate code | If `repo_path` provided, verify it exists and contains `src/app.py`. If path doesn't exist and `repo_url` provided, `git clone repo_url repo_path`. If neither, ask user. | Directory exists with `src/app.py` |
| ② Check environment | `python3 --version` (≥3.11), `git --version`, `node --version` | All three commands succeed, Python ≥ 3.11 |
| ③ Install acpx | `acpx --version` — skip if present. Otherwise `npm install -g acpx@latest`. Fallback: note `npx acpx@latest` as runtime alternative. | `acpx --version` succeeds or npx fallback noted |
| ④ Install dependencies | `cd {repo_path} && pip install -r requirements.txt` | Exit code 0 |
| ⑤ Initialize & start | `mkdir -p .coop/runs .coop/jobs` → init DB via `python3 -c "..."` → `nohup uvicorn src.app:app --host 127.0.0.1 --port 8321 &` | Process started |
| ⑥ Health check | Poll `curl -s http://127.0.0.1:8321/health` every 3s, up to 30s | Returns `{"status":"ok"}` |

Each stage failure triggers the Agent to consult `references/troubleshooting.md` for resolution. If unresolvable, the Agent reports the issue to the user with context.

### 3. Agent Host Registration

After health check passes, the Skill instructs the Agent to register a local host:

1. `exec curl -s http://127.0.0.1:8321/api/v1/agent-hosts` — check if `local` host already exists
2. If not present: `exec curl -s -X POST http://127.0.0.1:8321/api/v1/agent-hosts -H "Content-Type: application/json" -d '{"id":"local","host":"local","agent_type":"both","max_concurrent":2}'`
3. Skip if already registered (idempotent)

### 4. Completion Message

Agent replies with a confirmation:

```
✅ cooagents 已启动
- 服务地址：http://127.0.0.1:8321
- 健康状态：ok
- 本地 Agent 主机：已注册（claude + codex, 并发数 2）
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
| `pip install` fails | Suggest venv: `python3 -m venv .venv && source .venv/bin/activate` |
| Port 8321 occupied | `lsof -i :8321` to identify, prompt user to resolve |
| Health check timeout (30s) | Check uvicorn logs, common causes: DB init failure, import error |
| `git clone` fails | Network / SSH key issue, prompt user to check credentials |

### 6. Deployment

Deployed alongside `cooagents-workflow` by `skill_deployer.py` on cooagents startup. For first-time installation (chicken-and-egg), the Skill must be placed manually in OpenClaw's skills directory, or the Agent reads SKILL.md directly from the cloned repo.

### 7. Scope

**In scope:**
- Local machine installation only
- Clone or locate existing repo
- acpx installation via npm
- Service startup and health verification
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
