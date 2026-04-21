---
name: cooagents-setup
description: 安装并启动 cooagents 服务，检测环境、安装依赖、构建 Dashboard、启动服务并注册本地 Agent 主机。当用户提及安装、部署、启动 cooagents 时触发。
user-invocable: true
required_environment_variables:
  - name: AGENT_API_TOKEN
    prompt: "cooagents 服务令牌（由本 Skill 阶段 ③ 自动生成并写入）"
    help: "首次安装时留空即可；安装过程会生成并写回 {repo_path}/.env 及宿主 Agent 的 env。"
    optional: true
metadata:
  {
    "openclaw":
      {
        "emoji": "🔧",
        "always": false,
        "requires": { "bins": ["curl"] }
      },
    "hermes":
      {
        "tags": ["cooagents", "setup", "install"]
      }
  }
---

## A. 角色定义

你是 cooagents 的安装向导。你通过 `exec` 工具执行 shell 命令，将 cooagents 服务部署到本机。

该 Skill 同时适配 **OpenClaw** 与 **Hermes Agent** 两种宿主。阶段 ① 会自动识别当前运行环境并把结果记为 `{runtime}`，后续阶段会据此选择该宿主对应的配置写入方式。

安装逻辑由 `scripts/bootstrap.sh` 统一维护，你负责编排外围流程：识别宿主 runtime → 定位代码 → 运行 bootstrap → 启动服务 → 健康检查 → 校验 Dashboard 根路径 → 注册主机 →（可选）按 `{runtime}` 回写通知通道和 `AGENT_API_TOKEN`。

遇到问题时参考 `references/troubleshooting.md`（使用 Read 工具读取）。Hermes 专属细节参考 `references/hermes-integration.md`。

## B. 安装前准备

向用户确认以下信息：
- **repo_path**：cooagents 代码的本地路径（如 `/home/user/cooagents`）
- **repo_url**（可选）：远程仓库地址（如 `git@github.com:vaxtomis/cooagents.git`）
- **workspace_root**（可选，默认 `~/cooagents-workspace`）：cooagents 管理的项目仓库存放位置。后续 `POST /runs` / `POST /repos/ensure` 的 `repo_path` 必须位于此目录下
- **admin_username**（默认 `admin`）：Dashboard 登录用户名
- **admin_password**：Dashboard 登录密码（强密码，≥8 位）

如果用户未提供密码，使用 `read -s -p "Password: "` 交互式收集，不要回显。

## C. 安装流程（6 阶段 + 1 可选阶段）

### 阶段 ⓪ 识别宿主 Agent runtime

执行以下命令探测当前环境：

```bash
exec command -v openclaw 2>/dev/null
exec command -v hermes 2>/dev/null
```

判定 `{runtime}`：

- **只有 `openclaw` 可用** → `{runtime} = "openclaw"`
- **只有 `hermes` 可用** → `{runtime} = "hermes"`
- **两者都可用** → 询问用户："本机同时检测到 OpenClaw 和 Hermes，cooagents 希望把通知接入到哪一个？（openclaw / hermes / both）"；记住用户选择
- **都不可用** → `{runtime} = "none"`（仍可继续安装 cooagents 本体，阶段 ⑥ 跳过）

记住 `{runtime}`，阶段 ⑥ 分支会用到。

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

### 阶段 ⑥（可选）按 `{runtime}` 配置通知通道 + 注入 AGENT_API_TOKEN

仅当以下条件同时满足时执行：
- `{runtime}` ∈ {`openclaw`, `hermes`, `both`}
- cooagents 与宿主 Agent 在同一台机器
- 用户希望 cooagents 主动推送审批/完成通知

任一条件不满足则跳过本阶段，不影响 cooagents 本体安装完成。

根据 `{runtime}` 选择分支：
- `openclaw` / `both` → 执行 **C-6A OpenClaw 分支**
- `hermes` / `both` → 执行 **C-6B Hermes 分支**
- `none` → 跳过本阶段

#### C-6A OpenClaw 分支

先检查 OpenClaw CLI 和配置文件位置：

```bash
exec openclaw --version
exec openclaw config file
```

如果 `openclaw --version` 失败，告知用户 “OpenClaw 未安装或不在 PATH 中”，然后跳过此分支。

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

#### C-6B Hermes 分支

先检查 Hermes CLI 和安装目录：

```bash
exec hermes --version
exec hermes config path
```

如果 `hermes --version` 失败，告知用户 “Hermes 未安装或不在 PATH 中”，然后跳过此分支。

Hermes 没有 OpenClaw 风格的 `/hooks/agent` 接收端。推送通道复用 cooagents 自带的通用 webhook —— Hermes 侧的 `gateway/platforms/webhook.py` 会用 HMAC-SHA256 校验签名并把 payload 渲染成 Agent prompt。详细背景参考 `references/hermes-integration.md`。

**1. 生成 Hermes webhook HMAC secret**，并记住输出 `{hermes_secret}`：

```bash
exec python3 -c "import secrets; print(secrets.token_hex(32))"
```

**2. 把 secret 写入 Hermes 环境**（同时对 cooagents 的 `config/settings.yaml` 生效所需的 env var）：

```bash
# Hermes 侧
exec sh -c 'printf "HERMES_WEBHOOK_SECRET=%s\n" "{hermes_secret}" >> "$(hermes config env-path)"'
exec chmod 600 "$(hermes config env-path)"
```

**3. 在 Hermes `config.yaml` 中注册 cooagents webhook 路由**。让用户在 `hermes config edit` 中把下面的 YAML 片段合入 `platforms.webhook.extra.routes`（同名路由保留更严格的配置即可）：

```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: 127.0.0.1
      port: 8644
      routes:
        cooagents:
          events: ["*"]
          secret: "${HERMES_WEBHOOK_SECRET}"
          skills: ["cooagents-workflow"]
          prompt: |
            cooagents 推送事件：{event_type}
            run_id: {run_id}
            ticket: {ticket}

            payload: {payload}
          deliver: "log"
```

保存后重启 Hermes gateway：

```bash
exec hermes gateway restart 2>/dev/null || { echo "请手动重启 Hermes gateway"; }
```

**4. 把 `{hermes_secret}` 回写到 cooagents 的 `.env`**，这样 `webhook.secret` 的 `$ENV:HERMES_WEBHOOK_SECRET` 引用可以解析：

```bash
exec cd {repo_path} && printf "\nHERMES_WEBHOOK_SECRET=%s\n" "{hermes_secret}" >> .env && chmod 600 .env
```

**5. 启用 cooagents `hermes.enabled` 并生成 webhook 订阅**。使用阶段 ② 的 Python 解释器更新 `config/settings.yaml`：

```bash
# venv 成功时（Linux/macOS）
exec cd {repo_path} && .venv/bin/python -c "from pathlib import Path; import yaml; p=Path('config/settings.yaml'); d=yaml.safe_load(p.read_text(encoding='utf-8')); d.setdefault('hermes', {}); d['hermes'].update({'enabled': True, 'skills_dir': '~/.hermes/skills', 'deploy_skills': True}); d['hermes'].setdefault('webhook', {}); d['hermes']['webhook'].update({'enabled': True, 'url': 'http://127.0.0.1:8644/webhooks/cooagents', 'secret': '\$ENV:HERMES_WEBHOOK_SECRET'}); p.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding='utf-8')"
```

Windows Git Bash 改为 `.venv/Scripts/python`；全局安装回退改为 `python3`。

**6. 注册 cooagents 通用 webhook 订阅**（HMAC 由 `secret` 提供，内容与 Hermes 路由的 secret 一致）：

```bash
exec curl -s -X POST http://127.0.0.1:8321/api/v1/webhooks \
  -H "X-Agent-Token: $AGENT_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"http://127.0.0.1:8644/webhooks/cooagents\",\"events\":[\"gate.waiting\",\"run.completed\",\"run.failed\",\"merge.conflict\"],\"secret\":\"{hermes_secret}\"}"
```

**7. 注入 `AGENT_API_TOKEN` 到 Hermes 环境**（Hermes skill 里的 `exec curl` 会用到）：

```bash
exec sh -c 'printf "AGENT_API_TOKEN=%s\n" "{agent_api_token}" >> "$(hermes config env-path)"'
exec chmod 600 "$(hermes config env-path)"
```

验证：

```bash
exec hermes gateway restart 2>/dev/null || true
exec curl -s -X POST http://127.0.0.1:8644/webhooks/cooagents -H "Content-Type: application/json" -d '{"ping":"1"}' -o /dev/null -w "%{http_code}\n"
```

成功判定：返回 `401`（未签名）或 `202`（签名正确）。若返回 `000`/`curl: (7)`，说明 Hermes webhook 未就绪，检查 Hermes 日志。

## D. 完成确认

所有阶段完成后，回复用户：

```
✅ cooagents 已启动
- 服务地址：http://127.0.0.1:8321（公网访问需经反向代理终止 HTTPS）
- 健康状态：ok
- Dashboard：http://127.0.0.1:8321/（返回 HTML，首次访问需登录）
- 登录凭据：用户名 {admin_username} / 已由用户提供的密码
- 本地 Agent 主机：已注册（claude + codex, 共享并发上限 2）
- AGENT_API_TOKEN：已写入 {repo_path}/.env 并注入到宿主 Agent（{runtime}）
- 通知通道：
  - `openclaw` → cooagents 指向 http://127.0.0.1:{gateway_port}/hooks/agent
  - `hermes` → cooagents 订阅已注册到 http://127.0.0.1:8644/webhooks/cooagents（HMAC 签名）
  - `both` → 两条通道并行
  - `none` → 未配置外部通知

可以使用 /cooagents-workflow 开始创建任务。任务的 repo_path 必须位于 {workspace_root} 下，repo_url 仅支持 github.com 和 gitee.com。
```

## E. 参考文档

遇到问题时使用 Read 工具按需读取：
- 常见问题排查 → `references/troubleshooting.md`
- Hermes 宿主集成细节 → `references/hermes-integration.md`

## F. 操作原则

- **顺序执行**：必须按 ⓪→⑤ 顺序执行；阶段 ⑥ 仅在同机 OpenClaw/Hermes 场景下作为可选收尾步骤
- **状态追踪**：记住阶段 ⓪ 的 `{runtime}` 和阶段 ② bootstrap 输出（venv 是否成功、Dashboard 是否完成构建），阶段 ④/⑥ 的命令路径取决于此
- **认证必填**：阶段 ③ 生成的 4 个 env 变量缺一不可，服务启动时会校验；`.env` 文件权限必须是 600
- **密码不回显**：从用户处收集密码时使用 `read -s`，命令行历史不得留存明文
- **宿主先就绪**：阶段 ⑥ 必须先验证 OpenClaw/Hermes 自身 webhook/hooks 可达，再回写 cooagents 配置和 `AGENT_API_TOKEN`
- **token 必须隔离**：`hooks.token`、`HERMES_WEBHOOK_SECRET`、`AGENT_API_TOKEN`、`gateway.auth.token` 四者必须各自独立，严禁复用同一个值
- **失败即停**：阶段失败时先查 troubleshooting.md 尝试修复，修不了就告知用户
- **幂等安全**：重复执行不会破坏已有安装（DB 备份、主机检查）；已存在 `.env` 时提示用户确认是否覆盖,不静默重写
- **最少交互**：能自动完成的不问用户，只在缺少必要信息时询问

## G. 首次安装（引导问题）

本 Skill 由 cooagents 启动时自动部署到已配置的宿主 Agent（OpenClaw / Hermes / 同时）。但首次安装时 cooagents 尚未运行，Skill 也未部署。用户有以下方式获取此 Skill：

1. **手动放置（推荐）：** 从 cooagents 仓库复制 `skills/cooagents-setup/` 到对应宿主的 skills 目录，然后调用 `/cooagents-setup`：
   - OpenClaw：`~/.openclaw/skills/cooagents-setup/`
   - Hermes：`~/.hermes/skills/cooagents-setup/`
2. **从已 clone 的仓库：** 用户先 clone 仓库，然后告诉宿主 Agent 读取 `{repo_path}/skills/cooagents-setup/SKILL.md` 并按指示操作。
3. **后续自动：** 首次安装成功后，cooagents 启动时 `src/skill_deployer.py` 会把 `skills/` 自动同步到所有启用的宿主 skills 目录。
