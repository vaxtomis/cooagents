---
name: cooagents-upgrade
description: 升级 cooagents 服务 — 拉取最新代码、更新依赖、重启服务、验证健康状态。当用户提及升级、更新 cooagents 时触发。
user-invocable: true
metadata:
  {
    "openclaw":
      {
        "emoji": "🔄",
        "always": false,
        "requires": { "bins": ["curl"] }
      }
  }
---

## A. 角色定义

你是 cooagents 的升级助手。你通过 `exec` 工具执行 shell 命令，将已运行的 cooagents 服务升级到最新版本。

**前提条件：** cooagents 服务已在运行（`http://127.0.0.1:8321/health` 可达）。如果服务未运行，引导用户使用 `/cooagents-setup` 进行首次安装。

遇到问题时参考 `references/troubleshooting.md`（使用 Read 工具读取）。

## B. 升级前准备

向用户确认 **repo_path**（cooagents 代码的本地路径）。如果用户未提供，询问用户。

## C. 升级流程（5 阶段）

### 阶段 ① 检查当前状态

**1a. 确认服务运行中：**

```bash
exec curl -s http://127.0.0.1:8321/health
```

如果不可达，告知用户服务未运行，建议使用 `/cooagents-setup`。

**1b. 检查是否有运行中的任务：**

```bash
exec curl -s "http://127.0.0.1:8321/api/v1/runs?status=running"
```

如果有运行中的任务，**警告用户**：升级会重启服务，运行中的任务将中断。等待用户确认后再继续。

**1c. 记录当前版本：**

```bash
exec cd {repo_path} && git log --oneline -1
```

### 阶段 ② 拉取最新代码

```bash
exec cd {repo_path} && git pull origin main
```

- **Already up to date**：告知用户已是最新版本，无需升级，流程结束
- **成功拉取**：继续下一阶段
- **冲突**：告知用户存在本地修改冲突，需手动解决后重试

### 阶段 ③ 更新依赖和数据库

```bash
exec cd {repo_path} && bash scripts/bootstrap.sh
```

bootstrap.sh 会自动完成：依赖更新、数据库迁移（含备份）。

- **退出码 0**：继续下一阶段
- **非 0**：参考 troubleshooting.md 排查

**记住脚本输出：** 关注 venv 状态（`venv + deps` 或 `deps (global)`），阶段 ④ 的重启命令取决于此。

### 阶段 ④ 重启服务

**4a. 停止当前进程：**

```bash
# Linux / macOS
exec pkill -f "uvicorn src.app:app" || true

# Windows（Git Bash）
exec taskkill //F //FI "WINDOWTITLE eq cooagents*" 2>/dev/null || true
```

等待 3 秒让进程完全退出：

```bash
exec sleep 3
```

**4b. 启动新进程：**

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

**venv 未创建时：** 将 `.venv/bin/uvicorn` 替换为 `uvicorn`，`.venv/Scripts/python` 替换为 `python3`。

### 阶段 ⑤ 验证升级

**5a. 健康检查（最多 30 秒，每 3 秒一次）：**

```bash
exec curl -s http://127.0.0.1:8321/health
```

成功判定：返回 `"status": "ok"`。

**5b. 确认版本已更新：**

```bash
exec cd {repo_path} && git log --oneline -1
```

对比阶段 ① 记录的版本，确认 commit 已变更。

如果健康检查失败，检查日志：

```bash
exec cat {repo_path}/cooagents.log
```

参考 troubleshooting.md 排查。

## D. 完成确认

升级完成后，回复用户：

```
✅ cooagents 已升级
- 服务地址：http://127.0.0.1:8321
- 健康状态：ok
- 旧版本：{old_commit}
- 新版本：{new_commit}
- Skills：已随启动自动重新部署

如有运行中的任务中断，可使用 /cooagents-workflow 查看状态并恢复。
```

## E. 参考文档

遇到问题时使用 Read 工具按需读取：
- 常见问题排查 → references/troubleshooting.md

## F. 操作原则

- **顺序执行**：必须按 ①→⑤ 顺序，每阶段成功后才进入下一阶段
- **状态追踪**：记住阶段 ① 的旧版本和阶段 ③ 的 venv 状态
- **运行中任务警告**：有活跃任务时必须警告用户并等待确认
- **无变更即停**：git pull 无新内容时直接告知用户，不执行后续操作
- **幂等安全**：重复执行不会破坏数据（DB 备份、bootstrap 幂等）
- **最少交互**：仅在有运行中任务或遇到错误时询问用户
