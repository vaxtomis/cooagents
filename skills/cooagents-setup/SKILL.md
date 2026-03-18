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
