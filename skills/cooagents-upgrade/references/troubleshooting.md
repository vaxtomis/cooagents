# 升级常见问题排查

本文件提供升级过程中常见问题的诊断和修复方法。

---

## 1. git pull 冲突

**症状：** `CONFLICT (content): Merge conflict in ...`

**修复：** 告知用户存在本地修改与远程代码冲突。用户需手动解决冲突后重新执行升级：

```bash
cd {repo_path}
git status          # 查看冲突文件
# 手动编辑解决冲突
git add .
git commit -m "resolve merge conflicts"
```

解决后重新调用 `/cooagents-upgrade`。

---

## 2. bootstrap.sh 失败

**症状：** `scripts/bootstrap.sh` 退出码非 0

**常见原因：**

| 原因 | 修复 |
|------|------|
| 新增了系统依赖 | 查看 bootstrap 输出中的 `ERROR:` 信息 |
| pip 安装新包失败 | 参考 cooagents-setup 的 troubleshooting（pip install 失败） |
| DB schema 变更不兼容 | `.coop/state.db.bak` 已自动备份，可回退 |

---

## 3. 旧进程未完全退出

**症状：** 重启后端口 8321 被占用：`Address already in use`

**诊断：**

| 平台 | 命令 |
|------|------|
| Linux/macOS | `lsof -i :8321` |
| Windows | `netstat -ano \| findstr 8321` |

**修复：**

```bash
# Linux/macOS — 强制终止
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

## 5. 升级后任务状态异常

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

## 6. 版本回退

**症状：** 升级后出现严重问题，需要回退

**修复：**

```bash
cd {repo_path}

# 1. 停止服务
pkill -f "uvicorn src.app:app" || true

# 2. 回退代码
git log --oneline -5          # 找到要回退的 commit
git reset --hard <old_commit>

# 3. 恢复数据库（如需要）
cp .coop/state.db.bak .coop/state.db

# 4. 重新执行 bootstrap 并启动
bash scripts/bootstrap.sh
uvicorn src.app:app --host 127.0.0.1 --port 8321 &
```
