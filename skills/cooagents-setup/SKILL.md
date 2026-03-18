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

你是 cooagents 的安装向导。你通过 `exec` 工具执行 shell 命令，将 cooagents 服务部署到本机。

安装逻辑由 `scripts/bootstrap.sh` 统一维护，你负责编排外围流程：定位代码 → 运行 bootstrap → 启动服务 → 健康检查 → 注册主机。

遇到问题时参考 `references/troubleshooting.md`（使用 Read 工具读取）。

## B. 安装前准备

向用户确认以下信息：
- **repo_path**：cooagents 代码的本地路径（如 `/home/user/cooagents`）
- **repo_url**（可选）：远程仓库地址（如 `git@github.com:vaxtomis/cooagents.git`）

如果用户未提供，询问用户。

## C. 安装流程（4 阶段）

### 阶段 ① 定位代码

判断 `repo_path` 是否存在：

```bash
exec ls {repo_path}/src/app.py {repo_path}/config/settings.yaml
```

- **两个文件都存在**：继续下一阶段
- **不存在且有 `repo_url`**：
  ```bash
  exec git clone {repo_url} {repo_path}
  ```
- **不存在且无 `repo_url`**：询问用户提供代码路径或仓库地址

### 阶段 ② 运行 bootstrap.sh

```bash
exec cd {repo_path} && bash scripts/bootstrap.sh
```

脚本会依次完成：Python ≥3.11 校验、git/node 检查、acpx 安装、venv + pip 依赖、运行时目录创建、数据库初始化。

- **退出码 0**：继续下一阶段
- **非 0**：根据输出中的 `ERROR:` 或 `WARN:` 信息，参考 troubleshooting.md 排查

**记住脚本输出：** 如果输出包含 `venv + deps`，说明 venv 创建成功；如果包含 `deps (global)`，说明回退到全局安装。阶段 ③ 的启动命令路径取决于此。

### 阶段 ③ 启动服务

先检测平台：

```bash
exec uname -s 2>/dev/null || echo Windows
```

**venv 创建成功时：**

- **Linux / Darwin (macOS)：**
  ```bash
  exec cd {repo_path} && nohup .venv/bin/uvicorn src.app:app --host 127.0.0.1 --port 8321 > cooagents.log 2>&1 &
  ```
- **Windows（Git Bash）：**
  ```bash
  exec cd {repo_path} && (.venv/Scripts/python -m uvicorn src.app:app --host 127.0.0.1 --port 8321 > cooagents.log 2>&1 &)
  ```
  如果使用 CMD 或 PowerShell，参考 troubleshooting.md 第 6 节。

**venv 未创建（全局安装）时：** 将 `.venv/bin/uvicorn` 替换为 `uvicorn`，`.venv/Scripts/python` 替换为 `python3`。

然后轮询健康检查（最多 30 秒，每 3 秒一次）：

```bash
exec curl -s http://127.0.0.1:8321/health
```

成功判定：返回的 JSON 中包含 `"status": "ok"`。

如果 30 秒内未就绪，检查日志：

```bash
exec cat {repo_path}/cooagents.log
```

参考 troubleshooting.md 排查。

### 阶段 ④ 注册本地 Agent 主机

```bash
exec curl -s http://127.0.0.1:8321/api/v1/agent-hosts
```

如果返回列表中不包含 `"id": "local"` 的条目：

```bash
exec curl -s -X POST http://127.0.0.1:8321/api/v1/agent-hosts \
  -H "Content-Type: application/json" \
  -d '{"id":"local","host":"local","agent_type":"both","max_concurrent":2}'
```

如果 POST 失败（如重复注册），视为已注册，继续。

`agent_type: "both"` 表示该主机同时接受 claude 和 codex 两种 Agent 任务，共享 `max_concurrent: 2` 并发上限。

## D. 完成确认

所有阶段完成后，回复用户：

```
✅ cooagents 已启动
- 服务地址：http://127.0.0.1:8321
- 健康状态：ok
- 本地 Agent 主机：已注册（claude + codex, 共享并发上限 2）
- API 文档：http://127.0.0.1:8321/docs

可以使用 /cooagents-workflow 开始创建任务。
```

## E. 参考文档

遇到问题时使用 Read 工具按需读取：
- 常见问题排查 → references/troubleshooting.md

## F. 操作原则

- **顺序执行**：必须按 ①→④ 顺序，每阶段成功后才进入下一阶段
- **状态追踪**：记住阶段 ② bootstrap 的输出（venv 是否成功），阶段 ③ 的启动命令路径取决于此
- **失败即停**：阶段失败时先查 troubleshooting.md 尝试修复，修不了就告知用户
- **幂等安全**：重复执行不会破坏已有安装（DB 备份、主机检查）
- **最少交互**：能自动完成的不问用户，只在缺少必要信息时询问

## G. 首次安装（引导问题）

本 Skill 由 cooagents 启动时自动部署到 OpenClaw。但首次安装时 cooagents 尚未运行，Skill 也未部署。用户有以下方式获取此 Skill：

1. **手动放置（推荐）：** 从 cooagents 仓库复制 `skills/cooagents-setup/` 目录到 `~/.openclaw/skills/cooagents-setup/`，然后在 OpenClaw 中调用 `/cooagents-setup`。
2. **从已 clone 的仓库：** 用户先 clone 仓库，然后告诉 OpenClaw Agent 读取 `{repo_path}/skills/cooagents-setup/SKILL.md` 并按指示操作。
3. **后续自动：** 首次安装成功后，cooagents 启动时会自动将 Skill 部署到 OpenClaw，无需手动操作。
