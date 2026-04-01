# 升级常见问题排查

本文档提供升级过程中的常见问题诊断与修复方法。

---

## 1. `git pull` 冲突

**症状：** `CONFLICT (content): Merge conflict in ...`

**修复：** 告知用户存在本地修改与远程代码冲突。用户需手动解决冲突后重新执行升级：

```bash
cd {repo_path}
git status
# 手动编辑解决冲突
git add .
git commit -m "resolve merge conflicts"
```

解决后重新调用 `/cooagents-upgrade`。

---

## 2. `bootstrap.sh` 失败

**症状：** `scripts/bootstrap.sh` 退出码非 0

**常见原因：**

| 原因 | 修复 |
|------|------|
| 新增了系统依赖 | 查看 bootstrap 输出中的 `ERROR:` 信息 |
| pip 安装失败 | 参考 cooagents-setup 的 troubleshooting（pip install 失败） |
| `npm ci` 或 `npm run build` 失败 | 参考 cooagents-setup 的 troubleshooting（前端构建失败） |
| `web/dist/index.html` 未生成 | 重新在 `web/` 目录执行构建并校验产物 |
| DB schema 变更不兼容 | `.coop/state.db.bak` 已自动备份，可回退 |

---

## 3. 旧进程未完全退出

**症状：** 重启后端口 8321 仍被占用：`Address already in use`

**诊断：**

| 平台 | 命令 |
|------|------|
| Linux/macOS | `lsof -i :8321` |
| Windows | `netstat -ano \| findstr 8321` |

**修复：**

```bash
# Linux/macOS
kill -9 $(lsof -t -i :8321)

# Windows
taskkill /F /PID <pid>
```

等待几秒后重新启动。

---

## 4. 健康检查超时

**症状：** 重启后 30 秒内 `/health` 不可达

**诊断：**

```bash
cat {repo_path}/cooagents.log
```

**常见原因：**

| 原因 | 修复 |
|------|------|
| import 错误（新依赖未安装） | 重新执行 `bash scripts/bootstrap.sh` |
| DB schema 不兼容 | 检查 `db/schema.sql` 是否有破坏性变更，必要时从 `.bak` 恢复 |
| 配置格式变更 | 检查 `config/settings.yaml` 是否需要新增字段 |

---

## 5. Dashboard 根路径不返回 HTML

**症状：**
- `/health` 正常
- `curl -s http://127.0.0.1:8321/` 不包含 `<html`

**修复：**

```bash
cd {repo_path}/web
npm ci
npm run build
ls dist/index.html
```

确认产物存在后，重新执行：

```bash
cd {repo_path}
bash scripts/bootstrap.sh
```

如果根路径仍不返回 HTML，不要宣称升级成功。

---

## 6. 升级后任务状态异常

**症状：** 之前运行中的任务状态不一致

**修复：** 使用 API 查看和恢复：

```bash
# 查看所有运行中的任务
curl -s "http://127.0.0.1:8321/api/v1/runs?status=running"

# 恢复中断的任务
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/recover \
  -H "Content-Type: application/json" \
  -d '{"action":"resume"}'
```

---

## 7. 版本回退

**症状：** 升级后出现严重问题，需要回退

**修复：**

```bash
cd {repo_path}

# 1. 停止服务
pkill -f "uvicorn src.app:app" || true

# 2. 回退代码
git log --oneline -5
git reset --hard <old_commit>

# 3. 恢复数据库（如需要）
cp .coop/state.db.bak .coop/state.db

# 4. 重新执行 bootstrap 并启动
bash scripts/bootstrap.sh
uvicorn src.app:app --host 127.0.0.1 --port 8321 &
```
