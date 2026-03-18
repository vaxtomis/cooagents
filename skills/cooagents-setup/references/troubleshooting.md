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
