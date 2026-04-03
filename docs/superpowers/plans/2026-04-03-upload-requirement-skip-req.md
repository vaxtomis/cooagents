# Upload Requirement & Skip REQ Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to upload a `.md` or `.docx` requirement document when creating a run, skipping the REQ_COLLECTING and REQ_REVIEW stages to start directly at DESIGN_QUEUED.

**Architecture:** New `file_converter.py` module handles validation and pandoc conversion. `StateMachine.create_run_with_requirement()` orchestrates file placement, run creation, auto-approval and stage skip. A new multipart endpoint in `routes/runs.py` ties it together. The Dashboard gets a "创建任务" dialog with optional file drop zone that routes to the appropriate endpoint.

**Tech Stack:** Python, FastAPI (UploadFile/Form), pandoc CLI, React, TypeScript, Tailwind CSS

---

### Task 1: Add `python-multipart` dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add python-multipart to requirements.txt**

Append after `jinja2>=3.1`:

```
python-multipart>=0.0.9
```

- [ ] **Step 2: Install the dependency**

Run: `pip install python-multipart>=0.0.9`
Expected: Successfully installed

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add python-multipart dependency for file upload support"
```

---

### Task 2: Create file_converter module

**Files:**
- Create: `src/file_converter.py`
- Create: `tests/test_file_converter.py`

- [ ] **Step 1: Write failing tests**

In `tests/test_file_converter.py`:

```python
import pytest
from pathlib import Path
from src.file_converter import validate_upload, convert_docx_to_md


def test_validate_upload_md():
    assert validate_upload("REQ-PROJ-1.md") == "md"


def test_validate_upload_docx():
    assert validate_upload("requirement.docx") == "docx"


def test_validate_upload_case_insensitive():
    assert validate_upload("doc.MD") == "md"
    assert validate_upload("doc.DOCX") == "docx"


def test_validate_upload_rejects_txt():
    with pytest.raises(Exception):
        validate_upload("file.txt")


def test_validate_upload_rejects_no_extension():
    with pytest.raises(Exception):
        validate_upload("noext")


async def test_convert_docx_to_md_missing_pandoc(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    with pytest.raises(RuntimeError, match="pandoc"):
        await convert_docx_to_md(tmp_path / "in.docx", tmp_path / "out.md")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_file_converter.py -v`
Expected: FAIL — `src.file_converter` does not exist

- [ ] **Step 3: Implement file_converter.py**

Create `src/file_converter.py`:

```python
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from src.exceptions import BadRequestError

ALLOWED_EXTENSIONS = {"md", "docx"}


def validate_upload(filename: str) -> str:
    """Return normalised extension ('md' or 'docx'). Raise on invalid."""
    suffix = Path(filename).suffix.lstrip(".").lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise BadRequestError(
            f"Only .md and .docx files are supported, got '.{suffix}'"
        )
    return suffix


async def convert_docx_to_md(input_path: Path, output_path: Path) -> None:
    """Convert a .docx file to markdown using pandoc.

    Raises RuntimeError if pandoc is not installed or conversion fails.
    """
    if not shutil.which("pandoc"):
        raise RuntimeError(
            "pandoc is required for .docx conversion but not found on PATH"
        )
    proc = await asyncio.create_subprocess_exec(
        "pandoc", "-f", "docx", "-t", "markdown", "-o", str(output_path), str(input_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Document conversion failed: {stderr.decode()}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_file_converter.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/file_converter.py tests/test_file_converter.py
git commit -m "feat: add file_converter module for upload validation and docx conversion"
```

---

### Task 3: Add `create_run_with_requirement` to StateMachine

**Files:**
- Modify: `src/state_machine.py` (after `create_run`, around line 174)
- Test: `tests/test_state_machine.py`

- [ ] **Step 1: Write failing tests**

In `tests/test_state_machine.py`, add:

```python
async def test_create_run_with_requirement_skips_to_design_queued(sm, db, tmp_path):
    """Uploading a requirement should skip REQ_COLLECTING and REQ_REVIEW."""
    run = await sm.create_run_with_requirement(
        "T-UPLOAD", str(tmp_path), "# My Requirement\nDetails here", "req.md",
    )
    assert run["current_stage"] == "DESIGN_QUEUED"

    # Requirement file written to disk
    req_path = Path(tmp_path) / "docs" / "req" / "REQ-T-UPLOAD.md"
    assert req_path.exists()
    assert "My Requirement" in req_path.read_text(encoding="utf-8")

    # Artifact registered
    arts = await db.fetchall(
        "SELECT * FROM artifacts WHERE run_id=? AND kind='req'", (run["id"],)
    )
    assert len(arts) == 1

    # Auto-approval recorded
    approvals = await db.fetchall(
        "SELECT * FROM approvals WHERE run_id=? AND gate='req'", (run["id"],)
    )
    assert len(approvals) == 1
    assert approvals[0]["decision"] == "approved"
    assert approvals[0]["by"] == "upload"

    # Upload event emitted
    events = await db.fetchall(
        "SELECT * FROM events WHERE run_id=? AND event_type='requirement.uploaded'",
        (run["id"],),
    )
    assert len(events) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_state_machine.py::test_create_run_with_requirement_skips_to_design_queued -v`
Expected: FAIL — `StateMachine` has no attribute `create_run_with_requirement`

- [ ] **Step 3: Implement create_run_with_requirement**

In `src/state_machine.py`, add after `create_run()` (after line 174):

```python
    async def create_run_with_requirement(
        self,
        ticket: str,
        repo_path: str,
        req_content: str,
        original_filename: str,
        description: str | None = None,
        preferences: dict | None = None,
        notify_channel: str | None = None,
        notify_to: str | None = None,
        repo_url: str | None = None,
        design_agent: str | None = None,
        dev_agent: str | None = None,
    ) -> dict:
        """Create a run with an already-written requirement, skipping REQ stages.

        Writes the requirement to disk, creates the run, registers the artifact,
        records an auto-approval for the req gate, and advances directly to
        DESIGN_QUEUED.
        """
        # Write requirement file
        req_dir = Path(repo_path) / "docs" / "req"
        req_dir.mkdir(parents=True, exist_ok=True)
        req_path = req_dir / f"REQ-{ticket}.md"
        req_path.write_text(req_content, encoding="utf-8")

        # Create the run (starts at REQ_COLLECTING)
        run = await self.create_run(
            ticket, repo_path, description, preferences,
            notify_channel=notify_channel, notify_to=notify_to,
            repo_url=repo_url, design_agent=design_agent, dev_agent=dev_agent,
        )
        run_id = run["id"]

        # Register artifact
        await self.artifacts.register(run_id, "req", str(req_path), "REQ_COLLECTING")

        # Auto-approve req gate
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT INTO approvals(run_id,gate,decision,by,comment,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (run_id, "req", "approved", "upload", f"Uploaded: {original_filename}", now),
        )
        await self._emit(run_id, "gate.approved", {"gate": "req", "by": "upload"})

        # Skip REQ_COLLECTING → REQ_REVIEW → DESIGN_QUEUED
        await self._update_stage(run_id, "REQ_COLLECTING", "DESIGN_QUEUED")
        await self._emit(run_id, "requirement.uploaded", {
            "path": str(req_path),
            "original_filename": original_filename,
        })

        return await self._get_run(run_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_state_machine.py::test_create_run_with_requirement_skips_to_design_queued -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/state_machine.py tests/test_state_machine.py
git commit -m "feat: add create_run_with_requirement to skip REQ stages on upload"
```

---

### Task 4: Add upload-requirement API endpoint

**Files:**
- Modify: `routes/runs.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing test**

In `tests/test_api.py`, add:

```python
async def test_upload_requirement_creates_run_at_design_queued(client, tmp_path):
    """POST /runs/upload-requirement with .md file should skip to DESIGN_QUEUED."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()

    md_content = b"# Requirement\nDetails"
    response = await client.post(
        "/api/v1/runs/upload-requirement",
        files={"file": ("REQ-TEST.md", md_content, "text/markdown")},
        data={"ticket": "UPLOAD-1", "repo_path": str(repo)},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["current_stage"] == "DESIGN_QUEUED"

    # Verify file was written
    req_path = repo / "docs" / "req" / "REQ-UPLOAD-1.md"
    assert req_path.exists()
    assert "Requirement" in req_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api.py::test_upload_requirement_creates_run_at_design_queued -v`
Expected: FAIL — 404 (endpoint doesn't exist yet)

- [ ] **Step 3: Add endpoint to routes/runs.py**

Add imports at top of `routes/runs.py`:

```python
from fastapi import UploadFile, Form
from src.file_converter import validate_upload, convert_docx_to_md
```

Add the endpoint (before the `@router.get("/runs")` route to avoid path conflicts):

```python
@router.post("/runs/upload-requirement", status_code=201)
async def create_run_with_requirement(
    request: Request,
    file: UploadFile,
    ticket: str = Form(...),
    repo_path: str = Form(...),
    description: str | None = Form(None),
    notify_channel: str | None = Form(None),
    notify_to: str | None = Form(None),
    repo_url: str | None = Form(None),
    design_agent: str | None = Form(None),
    dev_agent: str | None = Form(None),
):
    import tempfile
    from pathlib import Path

    ext = validate_upload(file.filename or "")
    sm = request.app.state.sm

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_input = Path(tmp_dir) / f"upload.{ext}"
        content = await file.read()
        tmp_input.write_bytes(content)

        if ext == "docx":
            tmp_output = Path(tmp_dir) / "converted.md"
            await convert_docx_to_md(tmp_input, tmp_output)
            req_content = tmp_output.read_text(encoding="utf-8")
        else:
            req_content = tmp_input.read_text(encoding="utf-8")

    result = await sm.create_run_with_requirement(
        ticket, repo_path, req_content, file.filename or "unknown",
        description=description,
        notify_channel=notify_channel, notify_to=notify_to,
        repo_url=repo_url, design_agent=design_agent, dev_agent=dev_agent,
    )
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api.py::test_upload_requirement_creates_run_at_design_queued -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add routes/runs.py tests/test_api.py
git commit -m "feat: add POST /runs/upload-requirement endpoint for file-based run creation"
```

---

### Task 5: Frontend — add API client functions

**Files:**
- Modify: `web/src/api/runs.ts`
- Modify: `web/src/types/index.ts`

- [ ] **Step 1: Add CreateRunPayload type**

In `web/src/types/index.ts`, add before the `ApprovePayload` interface:

```typescript
export interface CreateRunPayload {
  ticket: string;
  repo_path: string;
  description?: string;
  notify_channel?: string;
  notify_to?: string;
  repo_url?: string;
  design_agent?: string;
  dev_agent?: string;
}
```

- [ ] **Step 2: Add createRun and createRunWithRequirement functions**

In `web/src/api/runs.ts`, add at the end of imports:

```typescript
import type { CreateRunPayload } from "../types";
```

Then add after the existing `getRunEventsStreamUrl` function:

```typescript
export async function createRun(payload: CreateRunPayload): Promise<RunRecord> {
  return apiFetch<RunRecord>("/runs", { method: "POST", body: payload });
}

export async function createRunWithRequirement(formData: FormData): Promise<RunRecord> {
  const response = await fetch("/api/v1/runs/upload-requirement", {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const message =
      data && typeof data === "object" && "message" in data
        ? String(data.message)
        : `Upload failed with status ${response.status}`;
    throw new Error(message);
  }
  return response.json();
}
```

- [ ] **Step 3: Commit**

```bash
git add web/src/types/index.ts web/src/api/runs.ts
git commit -m "feat: add createRun and createRunWithRequirement API client functions"
```

---

### Task 6: Frontend — add Create Run dialog to RunsListPage

**Files:**
- Modify: `web/src/pages/RunsListPage.tsx`

- [ ] **Step 1: Add the CreateRunDialog component and wire it into RunsListPage**

Add imports at the top of `web/src/pages/RunsListPage.tsx`:

```typescript
import { useRef, useCallback } from "react";
import { createRun, createRunWithRequirement } from "../api/runs";
import type { CreateRunPayload } from "../types";
```

Add the dialog component before the `RunsListPage` function:

```tsx
function CreateRunDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (runId: string) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const dropped = e.dataTransfer.files[0];
    if (dropped) setFile(dropped);
  }, []);

  if (!open) return null;

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    const fd = new FormData(e.currentTarget);
    const ticket = (fd.get("ticket") as string).trim();
    const repo_path = (fd.get("repo_path") as string).trim();
    if (!ticket || !repo_path) {
      setError("工单和仓库路径为必填项");
      setSubmitting(false);
      return;
    }

    try {
      let result: { id: string };
      if (file) {
        const upload = new FormData();
        upload.append("file", file);
        upload.append("ticket", ticket);
        upload.append("repo_path", repo_path);
        const desc = (fd.get("description") as string)?.trim();
        if (desc) upload.append("description", desc);
        const da = fd.get("design_agent") as string;
        if (da) upload.append("design_agent", da);
        const dva = fd.get("dev_agent") as string;
        if (dva) upload.append("dev_agent", dva);
        result = await createRunWithRequirement(upload);
      } else {
        const payload: CreateRunPayload = { ticket, repo_path };
        const desc = (fd.get("description") as string)?.trim();
        if (desc) payload.description = desc;
        const da = fd.get("design_agent") as string;
        if (da) payload.design_agent = da;
        const dva = fd.get("dev_agent") as string;
        if (dva) payload.dev_agent = dva;
        result = await createRun(payload);
      }
      onCreated(result.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="w-full max-w-lg rounded-[28px] border border-white/8 bg-panel p-6 shadow-panel" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-white">创建任务</h2>
        <form className="mt-5 space-y-4" onSubmit={handleSubmit}>
          <label className="block space-y-1 text-sm text-muted">
            <span>工单 <span className="text-red-400">*</span></span>
            <input name="ticket" required className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none focus:border-accent/40" placeholder="PROJ-123" />
          </label>
          <label className="block space-y-1 text-sm text-muted">
            <span>仓库路径 <span className="text-red-400">*</span></span>
            <input name="repo_path" required className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none focus:border-accent/40" placeholder="/path/to/repo" />
          </label>
          <label className="block space-y-1 text-sm text-muted">
            <span>描述</span>
            <textarea name="description" rows={2} className="w-full rounded-2xl border border-white/8 bg-black/18 px-4 py-3 text-sm text-white outline-none focus:border-accent/40" />
          </label>

          {/* File drop zone */}
          <div className="space-y-1 text-sm text-muted">
            <span>需求文档（可选，上传后跳过需求阶段）</span>
            <div
              className={`flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed px-4 py-6 transition ${file ? "border-accent/50 bg-accent/5" : "border-white/10 bg-black/10 hover:border-white/20"}`}
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={handleDrop}
            >
              {file ? (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-white">{file.name}</span>
                  <button type="button" className="text-xs text-red-400 hover:underline" onClick={(e) => { e.stopPropagation(); setFile(null); }}>移除</button>
                </div>
              ) : (
                <>
                  <p className="text-muted">拖拽文件到此处或点击选择</p>
                  <p className="mt-1 text-xs text-muted/60">支持 .md 和 .docx 文件</p>
                </>
              )}
              <input ref={fileRef} type="file" accept=".md,.docx" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <label className="block space-y-1 text-sm text-muted">
              <span>设计 Agent</span>
              <select name="design_agent" className="w-full rounded-2xl border border-white/8 bg-panel-strong px-4 py-3 text-sm text-white outline-none [&_option]:bg-panel-strong">
                <option value="">默认</option>
                <option value="claude">Claude</option>
                <option value="codex">Codex</option>
              </select>
            </label>
            <label className="block space-y-1 text-sm text-muted">
              <span>开发 Agent</span>
              <select name="dev_agent" className="w-full rounded-2xl border border-white/8 bg-panel-strong px-4 py-3 text-sm text-white outline-none [&_option]:bg-panel-strong">
                <option value="">默认</option>
                <option value="claude">Claude</option>
                <option value="codex">Codex</option>
              </select>
            </label>
          </div>

          {error && <p className="rounded-xl bg-red-500/10 px-4 py-2 text-sm text-red-400">{error}</p>}

          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="rounded-full border border-white/10 bg-white/4 px-5 py-2.5 text-sm font-medium text-white hover:bg-white/8">取消</button>
            <button type="submit" disabled={submitting} className="rounded-full bg-white px-5 py-2.5 text-sm font-medium text-black hover:bg-white/90 disabled:opacity-50">
              {submitting ? "创建中..." : "创建"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire the dialog into RunsListPage**

In the `RunsListPage` function body, add state and handler:

```typescript
const [showCreate, setShowCreate] = useState(false);
```

Add the button next to the existing "刷新" button (inside the form's button area, after the 刷新 button):

```tsx
<button
  className="rounded-full bg-accent px-4 py-3 text-sm font-medium text-white transition hover:bg-accent/90"
  onClick={() => setShowCreate(true)}
  type="button"
>
  创建任务
</button>
```

Add the dialog at the end of the returned JSX (before the closing `</div>`):

```tsx
<CreateRunDialog
  open={showCreate}
  onClose={() => setShowCreate(false)}
  onCreated={(runId) => {
    setShowCreate(false);
    navigate(`/runs/${runId}`);
  }}
/>
```

- [ ] **Step 3: Build and verify**

Run: `cd web && npm run build`
Expected: Build succeeds with no errors

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/RunsListPage.tsx
git commit -m "feat: add Create Run dialog with file upload to Dashboard"
```

---

### Task 7: Update workflow skill and API playbook docs

**Files:**
- Modify: `skills/cooagents-workflow/SKILL.md`
- Modify: `skills/cooagents-workflow/references/api-playbook.md`

- [ ] **Step 1: Update SKILL.md §B stage decision tree**

In `skills/cooagents-workflow/SKILL.md`, update the `(新任务)` row in the stage decision tree:

```
│ (新任务)            │ 自动     │ curl POST /repos/ensure → 判断是否有   │
│                     │          │ 需求文档文件：                         │
│                     │          │ · 有文件 → curl POST                   │
│                     │          │   /runs/upload-requirement（multipart） │
│                     │          │   → 直接进入 DESIGN_QUEUED             │
│                     │          │ · 无文件 → curl POST /runs →           │
│                     │          │   /runs/{id}/submit-requirement → tick  │
```

- [ ] **Step 2: Add §1b to api-playbook.md**

After the existing §1 block (after `# Response: {"id":"<run_id>","current_stage":"REQ_COLLECTING",...}` comment block and before `---`), add:

```markdown
### 1b. 上传需求文档创建任务（跳过需求阶段）

**前置条件：** 已有需求文档文件（.md 或 .docx），服务已运行。

```bash
# 上传需求文档并创建任务（跳过 REQ_COLLECTING 和 REQ_REVIEW）
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/upload-requirement \
  -F "file=@/path/to/REQ-PROJ-123.md" \
  -F "ticket=PROJ-123" \
  -F "repo_path=/path/to/repo"
# 可选字段（均通过 -F 传递）：description、notify_channel、notify_to、repo_url、design_agent、dev_agent
# .docx 文件会自动通过 pandoc 转换为 markdown（需要主机安装 pandoc）
# Response: {"id":"<run_id>","current_stage":"DESIGN_QUEUED",...}
```
```

- [ ] **Step 3: Commit**

```bash
git add skills/cooagents-workflow/SKILL.md skills/cooagents-workflow/references/api-playbook.md
git commit -m "docs: update workflow skill and API playbook for upload-requirement endpoint"
```

---

### Task 8: Run full test suite and verify

**Files:**
- All modified files from Tasks 1-7

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: ALL PASS — no regressions

- [ ] **Step 2: Build frontend**

Run: `cd web && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Commit any fixes**

If steps 1-2 found issues, fix and commit. Otherwise skip.
