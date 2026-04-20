---
name: cooagents-setup
description: 安装并启动 cooagents 服务，检测环境、安装依赖、构建 Dashboard、启动服务并注册本地 Agent 主机。当用户提及安装、部署、启动 cooagents 时触发。
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

安装逻辑由 `scripts/bootstrap.sh` 统一维护，你负责编排外围流程：定位代码 → 运行 bootstrap → 启动服务 → 健康检查 → 校验 Dashboard 根路径 → 注册主机 →（可选）先配置同机 OpenClaw 自己的 hooks，再把 hooks 地址和专用 token 写回 cooagents。

遇到问题时参考 `references/troubleshooting.md`（使用 Read 工具读取）。

## B. 安装前准备

向用户确认以下信息：
- **repo_path**：cooagents 代码的本地路径（如 `/home/user/cooagents`）
- **repo_url**（可选）：远程仓库地址（如 `git@github.com:vaxtomis/cooagents.git`）
- **workspace_root**（可选，默认 `~/cooagents-workspace`）：cooagents 管理的项目仓库存放位置。后续 `POST /runs` / `POST /repos/ensure` 的 `repo_path` 必须位于此目录下
- **admin_username**（默认 `admin`）：Dashboard 登录用户名
- **admin_password**：Dashboard 登录密码（强密码，≥8 位）

如果用户未提供密码，使用 `read -s -p "Password: "` 交互式收集，不要回显。

## C. 安装流程（5 阶段 + 1 可选阶段）

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

脚本会依次完成：
- Python ≥3.11 校验
- git / node / npm 检查
- acpx 安装（已有则跳过）
- venv 创建 + `pip install -r requirements.txt`
- 在 `web/` 目录执行 `npm ci` 与 `npm run build`
- 校验 `web/dist/index.html` 已生成
- 运行时目录创建
- 数据库初始化

- **退出码 0**：继续下一阶段
- **非 0**：根据输出中的 `ERROR:` 或 `WARN:` 信息，参考 troubleshooting.md 排查

**记住脚本输出：**
- 如果输出包含 `venv + deps`，说明 venv 创建成功
- 如果输出包含 `deps (global)`，说明回退到全局安装
- 如果输出包含 `web dashboard`，说明 Dashboard 已完成构建

阶段 ③ 的启动命令路径取决于 venv 是否成功。

### 阶段 ③ 生成认证配置（必需，否则启动失败）

cooagents 公网部署要求以下环境变量 — 缺失任一项服务启动即拒绝：
- `ADMIN_USERNAME` — Dashboard 登录用户名
- `ADMIN_PASSWORD_HASH` — argon2 密码哈希
- `JWT_SECRET` — JWT 签名密钥（≥32 字符）
- `AGENT_API_TOKEN` — OpenClaw / 其他本地 agent 调用 cooagents API 的服务令牌

1. 用 repo 内提供的脚本一次性生成四个值（venv 成功时用 venv Python）：

   ```bash
   # Linux / Darwin（venv）
   exec cd {repo_path} && .venv/bin/python scripts/generate_password_hash.py --username {admin_username} --password '{admin_password}'
   # Windows Git Bash（venv）
   exec cd {repo_path} && .venv/Scripts/python scripts/generate_password_hash.py --username {admin_username} --password '{admin_password}'
   # 全局安装回退
   exec cd {repo_path} && python3 scripts/generate_password_hash.py --username {admin_username} --password '{admin_password}'
   ```

2. 脚本会输出 4 行 `KEY=VALUE`。把它们写入 `{repo_path}/.env`(文件若不存在先创建,权限 600):

   ```bash
   exec cd {repo_path} && umask 077 && { echo 'ADMIN_USERNAME=...'; echo 'ADMIN_PASSWORD_HASH=...'; echo 'JWT_SECRET=...'; echo 'AGENT_API_TOKEN=...'; } > .env
   exec chmod 600 {repo_path}/.env
   ```

   把脚本输出里的 4 个值逐行填进去。**绝对不要把 .env 提交到 git**（repo 的 .gitignore 已包含）。

3. 记下 `AGENT_API_TOKEN` 的值 `{agent_api_token}` — 阶段 ⑥ 会回写到 OpenClaw 自己的环境变量。

4. 创建 workspace 根目录(后续任务的 repo_path 必须在此目录下):

   ```bash
   exec mkdir -p {workspace_root}
   ```

   默认 `{workspace_root} = ~/cooagents-workspace`。如果用户选了其他路径,同时修改 `config/settings.yaml` 里的 `security.workspace_root`。

### 阶段 ④ 启动服务

先检测平台：

```bash
exec uname -s 2>/dev/null || echo Windows
```

**公网部署必须经反向代理 (Nginx/Caddy) 提供 HTTPS**。cooagents 本身只监听 `127.0.0.1:8321`；反代负责终止 HTTPS 并转发到该端口。

**venv 创建成功时：**

- **Linux / Darwin（macOS）：**
  ```bash
  exec cd {repo_path} && set -a && . ./.env && set +a && nohup .venv/bin/uvicorn src.app:app --host 127.0.0.1 --port 8321 > cooagents.log 2>&1 &
  ```
- **Windows（Git Bash）：**
  ```bash
  exec cd {repo_path} && (set -a && . ./.env && set +a && .venv/Scripts/python -m uvicorn src.app:app --host 127.0.0.1 --port 8321 > cooagents.log 2>&1 &)
  ```

如果使用 CMD 或 PowerShell，参考 troubleshooting.md 的 Windows 启动章节。

**venv 未创建时（全局安装）：**
- 将 `.venv/bin/uvicorn` 替换为 `uvicorn`
- 将 `.venv/Scripts/python` 替换为 `python3`

生产环境建议改用 systemd,EnvironmentFile 指向 `.env`,这样进程重启无需手动 source。

然后轮询健康检查（最多 30 秒，每 3 秒一次）：

```bash
exec curl -s http://127.0.0.1:8321/health
```

成功判定：返回的 JSON 中包含 `"status": "ok"`。

然后验证 Dashboard 根路径：

```bash
exec curl -s http://127.0.0.1:8321/
```

成功判定：响应内容包含 `<html`。如果 `/health` 正常但根路径没有返回 HTML，视为安装失败，检查 bootstrap 输出和日志，并参考 troubleshooting.md。

如果 30 秒内未就绪，检查日志：

```bash
exec cat {repo_path}/cooagents.log
```

参考 troubleshooting.md 排查。

### 阶段 ⑤ 注册本地 Agent 主机

```bash
exec curl -s -H "X-Agent-Token: $AGENT_API_TOKEN" http://127.0.0.1:8321/api/v1/agent-hosts
```

如果返回列表中不包含 `"id": "local"` 的条目：

```bash
exec curl -s -X POST http://127.0.0.1:8321/api/v1/agent-hosts \
  -H "X-Agent-Token: $AGENT_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id":"local","host":"local","agent_type":"both","max_concurrent":2}'
```

如果 POST 失败（如重复注册），视为已注册，继续。

`agent_type: "both"` 表示该主机同时接收 claude 和 codex 两种 Agent 任务，共享 `max_concurrent: 2` 并发上限。

### 阶段 ⑥（可选）配置同机 OpenClaw hooks + 注入 AGENT_API_TOKEN

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

如果 `openclaw --version` 失败，告知用户 “OpenClaw 未安装或不在 PATH 中”，然后跳过此阶段。

生成 OpenClaw hooks 专用 token，并记住输出值 `{hooks_token}`：

```bash
exec python3 -c "import secrets; print(secrets.token_hex(32))"
```

禁止复用 `gateway.auth.token`。新版本 OpenClaw 要求 `hooks.token` 和 `gateway.auth.token` 不能相同，不要读取或复制 Gateway 鉴权 token 作为 hooks token，必须单独生成一个新的专用值。

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

- **Linux / Darwin（macOS）：**
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

**注入 AGENT_API_TOKEN 到 OpenClaw**：

OpenClaw agent 调用 cooagents API 必须携带 `X-Agent-Token` header。把阶段 ③ 生成的 `{agent_api_token}` 写入 OpenClaw 的环境配置：

```bash
exec openclaw config set env.AGENT_API_TOKEN "{agent_api_token}"
```

如果 OpenClaw 版本不支持 `env.*` 键,改为写到 OpenClaw 进程的 systemd EnvironmentFile 或启动脚本里(`Environment=AGENT_API_TOKEN={agent_api_token}`)。注入后让 OpenClaw 重启一次,使环境变量生效。

验证 OpenClaw 可以拿到该变量:

```bash
exec openclaw exec 'echo $AGENT_API_TOKEN' 2>/dev/null | head -1
```

输出应是阶段 ③ 生成的 token。如果为空,查看 OpenClaw 版本与文档,手动调整。

## D. 完成确认

所有阶段完成后，回复用户：

```
✅ cooagents 已启动
- 服务地址：http://127.0.0.1:8321（公网访问需经反向代理终止 HTTPS）
- 健康状态：ok
- Dashboard：http://127.0.0.1:8321/（返回 HTML，首次访问需登录）
- 登录凭据：用户名 {admin_username} / 已由用户提供的密码
- 本地 Agent 主机：已注册（claude + codex, 共享并发上限 2）
- AGENT_API_TOKEN：已写入 {repo_path}/.env 并注入到 OpenClaw
- OpenClaw hooks：如已执行阶段 ⑥，则 OpenClaw 已启用自己的 `/hooks/agent`，且 cooagents 已指向 http://127.0.0.1:{gateway_port}/hooks/agent

可以使用 /cooagents-workflow 开始创建任务。任务的 repo_path 必须位于 {workspace_root} 下，repo_url 仅支持 github.com 和 gitee.com。
```

## E. 参考文档

遇到问题时使用 Read 工具按需读取：
- 常见问题排查 → `references/troubleshooting.md`

## F. 操作原则

- **顺序执行**：必须按 ①→⑤ 顺序执行；阶段 ⑥ 仅在同机 OpenClaw 场景下作为可选收尾步骤
- **状态追踪**：记住阶段 ② bootstrap 的输出（venv 是否成功、Dashboard 是否完成构建），阶段 ④ 的启动命令路径取决于此
- **认证必填**：阶段 ③ 生成的 4 个 env 变量缺一不可，服务启动时会校验；`.env` 文件权限必须是 600
- **密码不回显**：从用户处收集密码时使用 `read -s`，命令行历史不得留存明文
- **OpenClaw 先就绪**：阶段 ⑥ 必须先打开并验证 OpenClaw 自己的 `/hooks/agent`，再回写 cooagents 的 hooks 配置和 `AGENT_API_TOKEN`
- **token 必须隔离**：`hooks.token` 必须单独生成，严禁与 `gateway.auth.token` 或 `AGENT_API_TOKEN` 复用同一个值
- **失败即停**：阶段失败时先查 troubleshooting.md 尝试修复，修不了就告知用户
- **幂等安全**：重复执行不会破坏已有安装（DB 备份、主机检查）；已存在 `.env` 时提示用户确认是否覆盖,不静默重写
- **最少交互**：能自动完成的不问用户，只在缺少必要信息时询问

## G. 首次安装（引导问题）

本 Skill 由 cooagents 启动时自动部署到 OpenClaw。但首次安装时 cooagents 尚未运行，Skill 也未部署。用户有以下方式获取此 Skill：

1. **手动放置（推荐）：** 从 cooagents 仓库复制 `skills/cooagents-setup/` 目录到 `~/.openclaw/skills/cooagents-setup/`，然后在 OpenClaw 中调用 `/cooagents-setup`。
2. **从已 clone 的仓库：** 用户先 clone 仓库，然后告诉 OpenClaw Agent 读取 `{repo_path}/skills/cooagents-setup/SKILL.md` 并按指示操作。
3. **后续自动：** 首次安装成功后，cooagents 启动时会自动将 Skill 部署到 OpenClaw，无需手动操作。
