# OpenClaw cooagents-workflow SKILL Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a cooagents-workflow SKILL (maintained in cooagents project) + deployment mechanism that syncs the skill to OpenClaw on startup, and update supporting project docs.

**Architecture:** Skill source files live in `cooagents/skills/cooagents-workflow/`. On startup, `src/skill_deployer.py` copies them to OpenClaw's `~/.openclaw/skills/` (local or remote via SSH). SKILL.md (~150 lines) + 3 reference docs guide the OpenClaw Agent to manage the 15-stage workflow via `exec` + `curl`.

**Tech Stack:** Markdown, YAML frontmatter, JSON, Python (asyncio, shutil, asyncssh)

**Spec:** `docs/superpowers/specs/2026-03-17-openclaw-skill-design.md`

**All files commit to this repo** (`C:\Work\codex\cooagents`).

---

### Task 1: Add `tick` endpoint to `openclaw-tools.json` ✅

> Already completed (commit 6ea28fc).

---

### Task 2: Create SKILL.md

**Files:**
- Create: `skills/cooagents-workflow/SKILL.md`

This is the core deliverable — the prompt injected into OpenClaw Agent's context. ~150 lines, structured per spec sections 5.1 and 5.2.

- [ ] **Step 1: Create the directory**

```bash
mkdir -p skills/cooagents-workflow/references
```

- [ ] **Step 2: Write SKILL.md**

Write the file with the following content structure:

**Frontmatter** (per spec 5.1 — uses `metadata:` block with JSON5 `"openclaw"` key, `user-invocable` at top level, `requires` for `curl` binary):

```yaml
---
name: cooagents-workflow
description: 管理 cooagents 多 Agent 协作工作流 — 通过 exec + curl 编排 Claude Code/Codex 完成从需求到合并的全生命周期。当用户提及任务创建、需求提交、设计/开发审批、任务状态查询、产物查看等工作流操作时触发。
user-invocable: true
metadata:
  {
    "openclaw":
      {
        "emoji": "🤖",
        "always": false,
        "requires": { "bins": ["curl"] }
      }
  }
---
```

**Body sections** (A through F, per spec 5.2):

**A. Role definition + API method:**
```markdown
你是 cooagents 工作流的项目经理。你通过 `exec` 工具执行 `curl` 命令驱动 15 阶段状态机，自动执行机械性操作，在审批环节通过对话回复与人类交互。

所有 API 调用的 Base URL 为 `http://127.0.0.1:8321/api/v1`。

调用模式：
- GET:  exec `curl -s http://127.0.0.1:8321/api/v1/runs/{run_id}`
- POST: exec `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/tick`
- POST+body: exec `curl -s -X POST URL -H "Content-Type: application/json" -d '{"key":"val"}'`

完整调用参数见 `references/api-playbook.md`（使用 Read 工具读取）。
```

**B. Stage decision tree** — the complete ASCII table with all entries from the spec (INIT through FAILED, including MERGE_CONFLICT). Copy verbatim from spec section 5.2 subsection C. All actions should reference `curl` commands, not function calls.

**Note:** INIT is a transient stage — `create_run` automatically advances to REQ_COLLECTING (state_machine.py line 109). The Agent should rarely encounter INIT via status query, but the decision tree entry serves as a safety net.

**C. Human interaction rules** — the updated flow from spec 5.2D:
- Agent 回复格式化文本（非调用飞书 API），等待用户下一条消息
- `by` field guidance (use message sender username/ID)
- Reject target stages (req→REQ_COLLECTING, design→DESIGN_QUEUED, dev→DEV_QUEUED)
- curl commands for approve/reject with JSON body examples (include optional `comment` field in approve)

**D. Webhook events** — copy the full event list verbatim from spec section 5.2E

**E. References pointer:**
```markdown
详细参考（使用 Read 工具按需读取）：
- curl 命令详情 → references/api-playbook.md
- 异常处理策略 → references/error-handling.md
- 回复消息模板 → references/feishu-interaction.md
```

Total target: ~150 lines. Keep tight — this is injected into the Agent prompt every time the skill activates.

- [ ] **Step 3: Verify file loads correctly**

Validate frontmatter YAML is parseable:

```bash
python -c "
import yaml
with open('skills/cooagents-workflow/SKILL.md') as f:
    content = f.read()
parts = content.split('---', 2)
fm = yaml.safe_load(parts[1])
assert fm['name'] == 'cooagents-workflow'
assert fm['user-invocable'] == True
assert fm['metadata']['openclaw']['emoji'] == '🤖'
assert fm['metadata']['openclaw']['requires']['bins'] == ['curl']
print('Frontmatter OK')
print(f'Body: {len(parts[2].strip().splitlines())} lines')
"
```

Expected: `Frontmatter OK` and body line count ~120-150.

- [ ] **Step 4: Commit**

```bash
git add skills/cooagents-workflow/SKILL.md
git commit -m "feat: add cooagents-workflow SKILL.md for OpenClaw agent integration"
```

---

### Task 3: Create `references/api-playbook.md`

**Files:**
- Create: `skills/cooagents-workflow/references/api-playbook.md`

Organized by 8 operation scenarios per spec section 6.1. Each scenario includes: preconditions, complete `curl` commands, expected response JSON.

- [ ] **Step 1: Write api-playbook.md**

The file should contain these 8 scenarios with **complete, copy-pasteable curl commands**:

**1. Create and start a task** (create → submit → tick):
```bash
# 1. Create task
curl -s -X POST http://127.0.0.1:8321/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"ticket":"PROJ-123","repo_path":"/path/to/repo","description":"任务描述"}'
# Response: {"id":"<run_id>","current_stage":"INIT",...}

# 2. Submit requirement
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/submit-requirement \
  -H "Content-Type: application/json" \
  -d '{"content":"# 需求文档\n..."}'

# 3. Tick to advance
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/<run_id>/tick
# Response: {"id":"<run_id>","current_stage":"REQ_REVIEW",...}
```

**2. Query status** (`curl -s GET /runs/{run_id}`)
**3. Approve gate** (`curl -s -X POST /approve` with `{"gate":"req","by":"..."}`) → tick
**4. Reject gate** (`curl -s -X POST /reject` with `{"gate":"design","by":"...","reason":"..."}`) — document target stages
**5. View artifacts** (`curl -s GET /artifacts`, `curl -s GET /artifacts/{id}/content`)
**6. Handle failure** — explain when to use which:
  - `retry` (POST /retry): for FAILED status, restores to `failed_at_stage` or INIT
  - `recover` (POST /recover): for interrupted jobs, action: resume/redo/manual
**7. Cancel task** (`curl -s -X DELETE /runs/{run_id}`)
**8. Tick (most common)** (`curl -s -X POST /runs/{run_id}/tick`)

All commands must use full URLs with `http://127.0.0.1:8321` prefix.

- [ ] **Step 2: Commit**

```bash
git add skills/cooagents-workflow/references/api-playbook.md
git commit -m "docs: add api-playbook reference for cooagents-workflow skill"
```

---

### Task 4: Create `references/error-handling.md`

**Files:**
- Create: `skills/cooagents-workflow/references/error-handling.md`

Defines autonomous error-handling rules per spec section 6.2.

- [ ] **Step 1: Write error-handling.md**

Include the error decision table from spec 6.2, with curl commands for each auto-response:

| Event | Auto Response | Escalation |
|-------|--------------|------------|
| `job.timeout` | `curl POST /recover` (action=resume), max 3 | 3 consecutive → reply to user |
| `job.failed` | `curl POST /retry`, max 2 | still fails → reply to user |
| `job.interrupted` / `job.error` | same as `job.failed` | same |
| `merge.conflict` | immediately reply to user with conflict file list | — |
| `host.offline` | wait for `host.online` then `curl POST /tick` | >30 min → reply to user |
| curl 4xx response | log error, no retry | reply to user |
| curl 5xx / network error | wait 10s, retry 1x | still fails → reply to user |

Add sections:
- **Retry counter tracking**: Agent tracks counts per run_id in conversation context
- **Escalation reply format**: Use the escalation template from `feishu-interaction.md`
- **curl error detection**: Check HTTP status code from curl output (use `curl -s -o /dev/null -w "%{http_code}"` pattern)

- [ ] **Step 2: Commit**

```bash
git add skills/cooagents-workflow/references/error-handling.md
git commit -m "docs: add error-handling reference for cooagents-workflow skill"
```

---

### Task 5: Create `references/feishu-interaction.md`

**Files:**
- Create: `skills/cooagents-workflow/references/feishu-interaction.md`

Three message template types per spec section 6.3. Clarify that these are **plain text reply templates** — the Agent formats its normal conversation reply using these templates. No feishu API calls needed.

- [ ] **Step 1: Write feishu-interaction.md**

Start with a clarification section:
```markdown
# 回复消息模板

Agent 的回复会自动通过当前对话渠道（飞书/Telegram/Discord 等）返回给用户。
以下模板用于格式化 Agent 的文本回复，不需要调用任何渠道 API。
```

Include all 3 template types from the spec:

**Approval request** (for REQ_REVIEW / DESIGN_REVIEW / DEV_REVIEW):
```
📋 任务 {ticket} 等待审批 ({gate_name})

【{artifact_type} 摘要】
{artifact_summary_or_first_500_chars}

请回复：
- "通过" — 审批通过，推进到下一阶段
- 具体的驳回原因 — 将驳回并附上你的反馈给 Agent 修订
```

**Status notification** (stage changes, completion):
```
🔄 任务 {ticket}: {from_stage} → {to_stage}
{contextual_message}
```

**Escalation** (exceeded limits, conflicts):
```
⚠️ 任务 {ticket} 需要人工介入
原因：{reason}
当前阶段：{stage}
建议：{suggestion}
```

Add guidance:
- MERGE_CONFLICT → use escalation template with conflict file list
- MERGED / run.completed → use status notification template
- Gate approval/rejection → use status notification for confirmation

- [ ] **Step 2: Commit**

```bash
git add skills/cooagents-workflow/references/feishu-interaction.md
git commit -m "docs: add feishu-interaction reference for cooagents-workflow skill"
```

---

### Task 6: Add OpenClaw config + skill deployer

**Files:**
- Modify: `config/settings.yaml` (add `openclaw` section)
- Modify: `src/config.py` (add `OpenclawConfig` Pydantic model)
- Create: `src/skill_deployer.py`
- Create: `tests/test_skill_deployer.py`

This task adds the deployment mechanism that copies skill files to OpenClaw on startup.

- [ ] **Step 1: Add `openclaw` section to settings.yaml**

```yaml
openclaw:
  deploy_skills: true
  targets:
    - type: local
      skills_dir: "~/.openclaw/skills"
```

- [ ] **Step 2: Add Pydantic models to config.py**

```python
class OpenclawTarget(BaseModel):
    type: str = "local"              # "local" or "ssh"
    skills_dir: str = "~/.openclaw/skills"
    host: str | None = None          # SSH only
    port: int = 22                   # SSH only
    user: str | None = None          # SSH only
    key: str | None = None           # SSH only

class OpenclawConfig(BaseModel):
    deploy_skills: bool = True
    targets: list[OpenclawTarget] = []
```

Add `openclaw: OpenclawConfig = OpenclawConfig()` to `Settings`.

- [ ] **Step 3: Write `src/skill_deployer.py`**

Responsibilities:
- Scan `ROOT / "skills"` for skill directories (containing `SKILL.md`)
- For each configured target:
  - `local`: `shutil.copytree` with `dirs_exist_ok=True` to `{skills_dir}/{skill_name}/`
  - `ssh`: Use `asyncssh` + SFTP to upload skill directory
- Return `list[DeployResult]` with target, skill name, success/error
- Log results at INFO level

Key details:
- Expand `~` in `skills_dir` paths
- Create target directory if it doesn't exist
- Overwrite existing files (skill updates)
- SSH targets reuse the asyncssh patterns from `host_manager.py`

- [ ] **Step 4: Write tests**

Test cases:
- `test_deploy_local_copies_files` — verify files are copied to a temp dir
- `test_deploy_local_creates_dir` — verify target dir is created if missing
- `test_deploy_local_overwrites` — verify existing files are replaced
- `test_deploy_disabled` — verify no-op when `deploy_skills: false`
- `test_deploy_no_targets` — verify graceful handling of empty targets list

Use `tmp_path` fixture, mock `ROOT / "skills"` with test skill files.

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_skill_deployer.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add config/settings.yaml src/config.py src/skill_deployer.py tests/test_skill_deployer.py
git commit -m "feat: add skill deployer to sync cooagents skills to OpenClaw on startup"
```

---

### Task 7: Rewrite `docs/PROCESS.md`

**Files:**
- Modify: `docs/PROCESS.md`

Complete rewrite per spec section 8. Delete all tmux/cron/flock/old-state-name references. The current content is 55 lines of outdated material.

- [ ] **Step 1: Write the new PROCESS.md**

Structure per spec section 8 (6 sections):

**1. Overview** — OpenClaw (requirements management) + Claude Code (design) + Codex (development) three-role collaboration. Reference README for architecture diagrams.

**2. 15-Stage Workflow** — Reference the mermaid state diagram from README. List each stage with its input/output in a table:

| Stage | Input | Output | Mode |
|-------|-------|--------|------|
| INIT | create_task call | run record | auto |
| REQ_COLLECTING | requirement content | requirement doc | auto |
| REQ_REVIEW | requirement doc | approval/rejection | human |
| DESIGN_QUEUED | approval | host assignment | auto |
| DESIGN_DISPATCHED | host assignment | acpx session | auto |
| DESIGN_RUNNING | session | design docs + ADR | auto |
| DESIGN_REVIEW | design docs | approval/rejection | human |
| DEV_QUEUED | approval | host assignment | auto |
| DEV_DISPATCHED | host assignment | acpx session | auto |
| DEV_RUNNING | session | code + tests | auto |
| DEV_REVIEW | code + test report | approval/rejection | human |
| MERGE_QUEUED | approval | merge queue position | auto |
| MERGING | queue position | merge result | auto |
| MERGED | merge success | completion notice | auto |
| MERGE_CONFLICT | merge failure | conflict file list | human |
| FAILED | error | retry/recover decision | auto |

**3. Branch conventions:**
- Design: `feat/{ticket}-design`
- Development: `feat/{ticket}-dev`

**4. Artifact conventions:**
- `docs/design/DES-{ticket}.md`
- `docs/design/ADR-{ticket}.md`
- `docs/dev/TEST-REPORT-{ticket}.md`

**5. Approval flow** — 3 gates (req/design/dev), trigger conditions, approval methods, reject-then-redo behavior with target stages.

**6. API-driven** — all operations via HTTP API at `http://127.0.0.1:8321/api/v1`, no CLI scripts, no tmux, no cron.

- [ ] **Step 2: Commit**

```bash
git add docs/PROCESS.md
git commit -m "docs: rewrite PROCESS.md to reflect acpx + 15-stage architecture"
```

---

### Task 8: Verify end-to-end

- [ ] **Step 1: Validate all new files exist and are well-formed**

```bash
# Check skill files in cooagents project
ls -la skills/cooagents-workflow/SKILL.md
ls -la skills/cooagents-workflow/references/api-playbook.md
ls -la skills/cooagents-workflow/references/error-handling.md
ls -la skills/cooagents-workflow/references/feishu-interaction.md

# Check deployer module
ls -la src/skill_deployer.py

# Check API reference
python -c "import json; t=json.load(open('docs/openclaw-tools.json')); print(f'{len(t[\"tools\"])} tools'); assert any(x['name']=='tick_task' for x in t['tools']), 'tick missing'"
```

Expected: 4 skill files exist, deployer exists, 12 tools, tick present.

- [ ] **Step 2: Validate SKILL.md frontmatter**

```bash
python -c "
import yaml
with open('skills/cooagents-workflow/SKILL.md') as f:
    content = f.read()
parts = content.split('---', 2)
fm = yaml.safe_load(parts[1])
assert fm['name'] == 'cooagents-workflow'
assert fm['user-invocable'] == True
assert fm['metadata']['openclaw']['always'] == False
assert fm['metadata']['openclaw']['requires']['bins'] == ['curl']
print('All validations passed')
"
```

- [ ] **Step 3: Verify SKILL.md uses exec+curl pattern, not function calls**

```bash
python -c "
with open('skills/cooagents-workflow/SKILL.md') as f:
    content = f.read()
# Should contain curl references
assert 'curl' in content, 'Missing curl references'
# Should NOT contain function-call-style invocations
for bad in ['调用 get_task_status', '调用 list_artifacts', '函数调用',
            'approve_gate(', 'reject_gate(', 'tick(', 'create_task(',
            'submit_requirement(', 'retry_task(', 'recover_task(']:
    assert bad not in content, f'Found function-call pattern: {bad}'
print('No function-call patterns found — exec+curl only')
"
```

- [ ] **Step 4: Run deployer tests**

```bash
python -m pytest tests/test_skill_deployer.py -v
```

Expected: all pass.

- [ ] **Step 5: Check no stale references remain in PROCESS.md**

```bash
python -c "
with open('docs/PROCESS.md') as f:
    content = f.read().lower()
stale = ['tmux', 'cron', 'flock', 'req_ready', 'design_assigned', 'design_done', 'dev_assigned']
found = [s for s in stale if s in content]
if found:
    print(f'STALE REFERENCES: {found}')
else:
    print('No stale references found')
"
```

Expected: `No stale references found`

- [ ] **Step 6: Line count check on SKILL.md**

```bash
wc -l skills/cooagents-workflow/SKILL.md
```

Expected: 120-180 lines (target ~150)
