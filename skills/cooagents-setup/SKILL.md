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

安装逻辑由 `scripts/bootstrap.sh` 统一维护，你负责编排外围流程：定位代码 → 运行 bootstrap → 启动服务 → 健康检查 → 注册主机 →（可选）先配置同机 OpenClaw 自己的 hooks，再把 hooks 地址和专用 token 写回 cooagents。

遇到问题时参考 `references/troubleshooting.md`（使用 Read 工具读取）。

## B. 安装前准备

向用户确认以下信息：
- **repo_path**：cooagents 代码的本地路径（如 `/home/user/cooagents`）
- **repo_url**（可选）：远程仓库地址（如 `git@github.com:vaxtomis/cooagents.git`）

如果用户未提供，询问用户。

## C. 安装流程（4 阶段 + 1 可选阶段）

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

### 阶段 ⑤（可选）配置同机 OpenClaw hooks

仅当以下条件同时满足时执行：
- 用户确认 OpenClaw 与 cooagents 部署在同一台机器
- 用户希望 cooagents 通过 OpenClaw 主动推送通知
- 本机存在 `openclaw` CLI

如果任一条件不满足，跳过此阶段，不影响 cooagents 安装完成。

先检查 OpenClaw CLI 和配置文件位置：

```bash
exec openclaw --version
exec openclaw config file
```

如果 `openclaw --version` 失败，告知用户“OpenClaw 未安装或不在 PATH 中”，然后跳过此阶段。

生成 OpenClaw hooks 专用 token，并记住输出为 `{hooks_token}`：

```bash
exec python3 -c "import secrets; print(secrets.token_hex(32))"
```

禁止复用 `gateway.auth.token`。新版本 OpenClaw 要求 `hooks.token` 和 `gateway.auth.token` 不能相同；不要读取或复制 Gateway 鉴权 token 作为 hooks token，必须单独生成一个新的专用值。

配置 OpenClaw hooks ingress：

```bash
exec openclaw config set hooks.enabled true --strict-json
exec openclaw config set hooks.token "{hooks_token}"
exec openclaw config set hooks.defaultSessionKey "hook:ingress"
exec openclaw config set hooks.allowRequestSessionKey false --strict-json
exec openclaw config set hooks.allowedSessionKeyPrefixes '["hook:"]' --strict-json
```

检测 OpenClaw gateway 端口。若未设置则默认使用 `18789`：

```bash
exec openclaw config get gateway.port
```

- 如果返回数字，记为 `{gateway_port}`
- 如果返回 `Config path not found` 或空值，使用 `18789`

先验证 OpenClaw 的 `/hooks/agent` 已可用，再回写 cooagents 配置：

```bash
exec curl -s -X POST http://127.0.0.1:{gateway_port}/hooks/agent \
  -H "Authorization: Bearer {hooks_token}" \
  -H "Content-Type: application/json" \
  -d '{"message":"cooagents hook test","name":"cooagents-setup","wakeMode":"next-heartbeat","deliver":false}'
```

成功判定：返回 JSON 包含 `"ok": true`。如果返回 `401` / `404` / 连接失败，不要开启 cooagents hooks，告知用户需要先修复 OpenClaw 自己的 hooks 配置并保留当前安装结果。

当阶段 ② 创建了 venv 时，使用对应 Python 解释器更新 `config/settings.yaml`：

- **Linux / Darwin (macOS)：**
  ```bash
  exec cd {repo_path} && .venv/bin/python -c "from pathlib import Path; import yaml; p=Path('config/settings.yaml'); data=yaml.safe_load(p.read_text(encoding='utf-8')); data.setdefault('openclaw', {}).setdefault('hooks', {}); data['openclaw']['hooks'].update({'enabled': True, 'url': 'http://127.0.0.1:{gateway_port}/hooks/agent', 'token': '{hooks_token}'}); p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')"
  ```
- **Windows（Git Bash）：**
  ```bash
  exec cd {repo_path} && .venv/Scripts/python -c "from pathlib import Path; import yaml; p=Path('config/settings.yaml'); data=yaml.safe_load(p.read_text(encoding='utf-8')); data.setdefault('openclaw', {}).setdefault('hooks', {}); data['openclaw']['hooks'].update({'enabled': True, 'url': 'http://127.0.0.1:{gateway_port}/hooks/agent', 'token': '{hooks_token}'}); p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')"
  ```

当阶段 ② 回退为全局安装时，改用：

```bash
exec cd {repo_path} && python3 -c "from pathlib import Path; import yaml; p=Path('config/settings.yaml'); data=yaml.safe_load(p.read_text(encoding='utf-8')); data.setdefault('openclaw', {}).setdefault('hooks', {}); data['openclaw']['hooks'].update({'enabled': True, 'url': 'http://127.0.0.1:{gateway_port}/hooks/agent', 'token': '{hooks_token}'}); p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding='utf-8')"
```

回写后验证 cooagents 配置：

```bash
exec cat {repo_path}/config/settings.yaml
```

成功判定：`openclaw.hooks.enabled: true`，`url` 指向 `http://127.0.0.1:{gateway_port}/hooks/agent`，`token` 与 `{hooks_token}` 一致，且该值不是 OpenClaw 的 `gateway.auth.token`。

## D. 完成确认

所有阶段完成后，回复用户：

```
✅ cooagents 已启动
- 服务地址：http://127.0.0.1:8321
- 健康状态：ok
- 本地 Agent 主机：已注册（claude + codex, 共享并发上限 2）
- API 文档：http://127.0.0.1:8321/docs
- OpenClaw hooks：如已执行阶段 ⑤，则 OpenClaw 已先启用自己的 `/hooks/agent`，且 cooagents 已指向 http://127.0.0.1:{gateway_port}/hooks/agent

可以使用 /cooagents-workflow 开始创建任务。
```

## E. 参考文档

遇到问题时使用 Read 工具按需读取：
- 常见问题排查 → references/troubleshooting.md

## F. 操作原则

- **顺序执行**：必须按 ①→④ 顺序执行；阶段 ⑤ 仅在同机 OpenClaw 场景下作为可选收尾步骤
- **状态追踪**：记住阶段 ② bootstrap 的输出（venv 是否成功），阶段 ③ 的启动命令路径取决于此
- **OpenClaw 先就绪**：阶段 ⑤ 必须先打开并验证 OpenClaw 自己的 `/hooks/agent`，再回写 cooagents 的 hooks 配置
- **token 必须隔离**：`hooks.token` 必须单独生成，严禁与 `gateway.auth.token` 复用同一个值
- **失败即停**：阶段失败时先查 troubleshooting.md 尝试修复，修不了就告知用户
- **幂等安全**：重复执行不会破坏已有安装（DB 备份、主机检查）
- **最少交互**：能自动完成的不问用户，只在缺少必要信息时询问

## G. 首次安装（引导问题）

本 Skill 由 cooagents 启动时自动部署到 OpenClaw。但首次安装时 cooagents 尚未运行，Skill 也未部署。用户有以下方式获取此 Skill：

1. **手动放置（推荐）：** 从 cooagents 仓库复制 `skills/cooagents-setup/` 目录到 `~/.openclaw/skills/cooagents-setup/`，然后在 OpenClaw 中调用 `/cooagents-setup`。
2. **从已 clone 的仓库：** 用户先 clone 仓库，然后告诉 OpenClaw Agent 读取 `{repo_path}/skills/cooagents-setup/SKILL.md` 并按指示操作。
3. **后续自动：** 首次安装成功后，cooagents 启动时会自动将 Skill 部署到 OpenClaw，无需手动操作。
