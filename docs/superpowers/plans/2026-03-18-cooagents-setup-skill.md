# cooagents-setup Skill Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a `cooagents-setup` OpenClaw Skill that guides the Agent to install and start the cooagents service on a local machine.

**Architecture:** Two Markdown files — `SKILL.md` (core installation logic injected into Agent prompt) and `references/troubleshooting.md` (error resolution guide). No Python code. Follows the same structure and conventions as `cooagents-workflow`.

**Tech Stack:** Markdown (OpenClaw Skill format), shell commands via Agent `exec`

---

### Task 1: Create `SKILL.md`

**Files:**
- Create: `skills/cooagents-setup/SKILL.md`

Reference the existing skill for format/conventions: `skills/cooagents-workflow/SKILL.md`

- [ ] **Step 1: Create the SKILL.md file**

```markdown
---
name: cooagents-setup
description: 安装并启动 cooagents 服务 — 检测环境、安装依赖、启动服务器、注册本地 Agent 主机。当用户提及安装、部署、启动 cooagents 时触发。
user-invocable: true
metadata:
  {
    "openclaw":
      {
        "emoji": "🔧",
        "always": false,
        "requires": { "bins": ["curl"] }
      }
  }
---

## A. 角色定义

你是 cooagents 的安装向导。你通过 `exec` 工具执行 shell 命令，按顺序完成 6 个安装阶段，将 cooagents 服务部署到本机。

每个阶段完成后确认成功再进入下一阶段。遇到问题时参考 `references/troubleshooting.md`（使用 Read 工具读取）。

## B. 安装前准备

向用户确认以下信息：
- **repo_path**：cooagents 代码的本地路径（如 `/home/user/cooagents`）
- **repo_url**（可选）：远程仓库地址（如 `git@github.com:vaxtomis/cooagents.git`）

如果用户未提供，询问用户。

## C. 安装流程（6 阶段）

### 阶段 ① 定位代码

判断 `repo_path` 是否存在：

```bash
# 检查目录是否存在
exec ls {repo_path}/src/app.py
```

- **存在且包含 `src/app.py` 和 `config/settings.yaml`**：继续下一阶段
- **不存在且有 `repo_url`**：
  ```bash
  exec git clone {repo_url} {repo_path}
  ```
- **不存在且无 `repo_url`**：询问用户提供代码路径或仓库地址

### 阶段 ② 检测环境

依次检查三个工具：

```bash
exec python3 --version
# 要求：≥ 3.11，否则参考 troubleshooting.md

exec git --version

exec node --version
# 如果 node 不存在，参考 troubleshooting.md
```

三个命令都成功才进入下一阶段。

### 阶段 ③ 安装 acpx

```bash
exec acpx --version
```

- **已安装**：跳过
- **未安装**：
  ```bash
  exec npm install -g acpx@latest
  ```
  如果权限不足，参考 troubleshooting.md（sudo 或 npx 替代方案）。
  安装后再次验证：
  ```bash
  exec acpx --version
  ```

### 阶段 ④ 安装依赖

优先使用 venv：

```bash
exec cd {repo_path} && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

如果 venv 创建失败，回退到全局安装：

```bash
exec cd {repo_path} && pip install -r requirements.txt
```

成功判定：退出码为 0。

### 阶段 ⑤ 初始化并启动

**5a. 创建运行时目录和初始化数据库：**

```bash
exec cd {repo_path} && mkdir -p .coop/runs .coop/jobs
```

```bash
exec cd {repo_path} && python3 -c "
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

**5b. 启动服务（平台相关）：**

先检测平台：

```bash
exec uname -s 2>/dev/null || echo Windows
```

- **Linux / Darwin (macOS)：**
  ```bash
  exec cd {repo_path} && nohup .venv/bin/uvicorn src.app:app --host 127.0.0.1 --port 8321 > cooagents.log 2>&1 &
  ```

- **Windows（Git Bash）：**
  ```bash
  exec cd {repo_path} && (.venv/Scripts/python -m uvicorn src.app:app --host 127.0.0.1 --port 8321 > cooagents.log 2>&1 &)
  ```
  如果使用 CMD 或 PowerShell，参考 troubleshooting.md 第 6 节。

**venv 路径选择：** 如果阶段 ④ 未创建 venv（回退到全局安装），将 `.venv/bin/uvicorn` 替换为 `uvicorn`，`.venv/Scripts/python` 替换为 `python3`。Agent 需要记住阶段 ④ 的结果来决定使用哪个路径。

### 阶段 ⑥ 健康检查

轮询服务直到就绪（最多 30 秒，每 3 秒一次）：

```bash
exec curl -s http://127.0.0.1:8321/health
```

成功判定：返回的 JSON 中包含 `"status": "ok"`。

如果 30 秒内未就绪，检查日志：

```bash
exec cat {repo_path}/cooagents.log
```

参考 troubleshooting.md 排查。

## D. 注册本地 Agent 主机

健康检查通过后，注册本地主机：

```bash
# 1. 检查是否已注册
exec curl -s http://127.0.0.1:8321/api/v1/agent-hosts
```

如果返回列表中不包含 `"id": "local"` 的条目：

```bash
# 2. 注册本地主机
exec curl -s -X POST http://127.0.0.1:8321/api/v1/agent-hosts \
  -H "Content-Type: application/json" \
  -d '{"id":"local","host":"local","agent_type":"both","max_concurrent":2}'
```

如果 POST 失败（如重复注册），视为已注册，继续。

`agent_type: "both"` 表示该主机同时接受 claude 和 codex 两种 Agent 任务，共享 `max_concurrent: 2` 并发上限。

## E. 完成确认

所有阶段完成后，回复用户：

```
✅ cooagents 已启动
- 服务地址：http://127.0.0.1:8321
- 健康状态：ok
- 本地 Agent 主机：已注册（claude + codex, 共享并发上限 2）
- API 文档：http://127.0.0.1:8321/docs

可以使用 /cooagents-workflow 开始创建任务。
```

## F. 参考文档

遇到问题时使用 Read 工具按需读取：
- 常见问题排查 → references/troubleshooting.md

## G. 操作原则

- **顺序执行**：必须按 ①→⑥ 顺序，每阶段成功后才进入下一阶段
- **状态追踪**：记住阶段 ④ 是否成功创建了 venv，阶段 ⑤ 的启动命令路径取决于此
- **失败即停**：阶段失败时先查 troubleshooting.md 尝试修复，修不了就告知用户
- **幂等安全**：重复执行不会破坏已有安装（DB 备份、主机检查）
- **最少交互**：能自动完成的不问用户，只在缺少必要信息时询问

## H. 首次安装（引导问题）

本 Skill 由 cooagents 启动时自动部署到 OpenClaw。但首次安装时 cooagents 尚未运行，Skill 也未部署。用户有以下方式获取此 Skill：

1. **手动放置（推荐）：** 从 cooagents 仓库复制 `skills/cooagents-setup/` 目录到 `~/.openclaw/skills/cooagents-setup/`，然后在 OpenClaw 中调用 `/cooagents-setup`。
2. **从已 clone 的仓库：** 用户先 clone 仓库，然后告诉 OpenClaw Agent 读取 `{repo_path}/skills/cooagents-setup/SKILL.md` 并按指示操作。
3. **后续自动：** 首次安装成功后，cooagents 启动时会自动将 Skill 部署到 OpenClaw，无需手动操作。
```

- [ ] **Step 2: Verify file structure matches cooagents-workflow convention**

Check that the frontmatter format (name, description, user-invocable, metadata) matches `skills/cooagents-workflow/SKILL.md` exactly.

- [ ] **Step 3: Commit**

```bash
git add skills/cooagents-setup/SKILL.md
git commit -m "feat: add cooagents-setup skill for automated installation"
```

---

### Task 2: Create `references/troubleshooting.md`

**Files:**
- Create: `skills/cooagents-setup/references/troubleshooting.md`

- [ ] **Step 1: Create the troubleshooting reference file**

```markdown
# 常见问题排查

本文件提供安装过程中常见问题的诊断和修复方法。Agent 在某个安装阶段失败时，按问题类型查找对应解决方案。

---

## 1. python3 不存在

**症状：** `python3: command not found`

**修复：**

| 平台 | 命令 |
|------|------|
| macOS | `brew install python@3.13` |
| Ubuntu/Debian | `sudo apt update && sudo apt install python3.13` |
| Windows | `winget install Python.Python.3.13` 或从 python.org 下载 |

安装后验证：`python3 --version`

---

## 2. Python 版本低于 3.11

**症状：** `python3 --version` 返回 3.10 或更低

**修复：** 提示用户升级 Python。不要尝试自动安装多版本管理器（pyenv 等），让用户选择适合的升级方式。

---

## 3. node / npm 不存在

**症状：** `node: command not found` 或 `npm: command not found`

**修复：**

| 平台 | 命令 |
|------|------|
| macOS | `brew install node` |
| Ubuntu/Debian | `curl -fsSL https://deb.nodesource.com/setup_lts.x \| sudo -E bash - && sudo apt install nodejs` |
| Windows | `winget install OpenJS.NodeJS.LTS` 或从 nodejs.org 下载 |

安装后验证：`node --version && npm --version`

---

## 4. npm install -g 权限不足

**症状：** `EACCES: permission denied` 或类似权限错误

**修复方案（二选一）：**

1. 使用 sudo：`sudo npm install -g acpx@latest`
2. 不全局安装，运行时使用 npx 替代：`npx acpx@latest`
   - 注意：如果选择 npx 方案，后续 cooagents 配置中 agent 执行器需确保 npx 可用

---

## 5. pip install 失败

**症状：** `pip install -r requirements.txt` 报错

**常见原因和修复：**

| 原因 | 修复 |
|------|------|
| 系统 Python 限制（PEP 668 externally-managed） | 必须使用 venv：`python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt` |
| pip 版本过旧 | `pip install --upgrade pip` 后重试 |
| 缺少编译工具（C 扩展） | macOS: `xcode-select --install`；Linux: `sudo apt install build-essential` |

---

## 6. nohup 不存在（Windows）

**症状：** `nohup: command not found`

**修复：** Windows 下使用以下替代命令启动服务：

```bash
start /b .venv/Scripts/python -m uvicorn src.app:app --host 127.0.0.1 --port 8321 > cooagents.log 2>&1
```

或在 PowerShell 中：

```powershell
Start-Process -NoNewWindow -FilePath ".venv\Scripts\python" -ArgumentList "-m uvicorn src.app:app --host 127.0.0.1 --port 8321" -RedirectStandardOutput cooagents.log -RedirectStandardError cooagents-err.log
```

---

## 7. 端口 8321 被占用

**症状：** `[Errno 98] Address already in use` 或 `[WinError 10048]`

**诊断：**

| 平台 | 命令 |
|------|------|
| Linux/macOS | `lsof -i :8321` |
| Windows | `netstat -ano \| findstr 8321` |

**修复：** 告知用户端口被占用，请用户终止占用进程或选择其他端口。如果是之前的 cooagents 实例，用户可以先终止它再重新启动。

---

## 8. 健康检查超时（30 秒内未返回 200）

**症状：** 多次 `curl http://127.0.0.1:8321/health` 无响应或返回错误

**诊断：**

```bash
cat {repo_path}/cooagents.log
```

**常见原因：**

| 原因 | 修复 |
|------|------|
| DB 初始化失败 | 检查 `.coop/state.db` 是否存在，重新执行 DB 初始化命令 |
| import 错误（缺少依赖） | 重新执行 `pip install -r requirements.txt` |
| uvicorn 未安装 | 确认 venv 激活后安装：`pip install uvicorn[standard]` |
| 配置文件缺失 | 确认 `config/settings.yaml` 存在（必须从 git repo clone，不支持手动创建目录结构） |

---

## 9. git clone 失败

**症状：** `fatal: repository not found` 或 `Permission denied (publickey)`

**修复：**

| 原因 | 修复 |
|------|------|
| 仓库地址错误 | 确认 URL 正确 |
| SSH key 未配置 | `ssh -T git@github.com` 测试连接；需要配置 SSH key |
| 网络问题 | 检查网络连接；尝试 HTTPS 地址替代 SSH |

---

## 10. config/settings.yaml 缺失

**症状：** 阶段 ① 检查时 `config/settings.yaml` 不存在

**原因：** 用户手动创建了目录结构而非从 git clone

**修复：** 必须从 git 仓库 clone 完整代码。手动创建目录结构不受支持。
```

- [ ] **Step 2: Commit**

```bash
git add skills/cooagents-setup/references/troubleshooting.md
git commit -m "feat: add troubleshooting reference for cooagents-setup skill"
```

---

### Task 3: Verify deployment via skill_deployer

**Files:**
- None (verification only)

- [ ] **Step 1: Verify skill directory is discoverable**

Confirm `skills/cooagents-setup/SKILL.md` exists and `skill_deployer.py` will pick it up (it scans for directories containing `SKILL.md` under `skills/`).

```bash
ls skills/cooagents-setup/SKILL.md
ls skills/cooagents-setup/references/troubleshooting.md
```

Both files should exist.

- [ ] **Step 2: Run existing tests to verify no regressions**

```bash
pytest tests/test_skill_deployer.py -v
```

Expected: all PASS (existing deployer tests should still work — the deployer discovers any skill directory with SKILL.md, so the new skill is automatically included).

- [ ] **Step 3: Commit (if any fixes needed)**

No commit expected unless tests reveal issues.
