# Upload Requirement Document & Skip REQ Stage

**Date:** 2026-04-03
**Status:** Approved

## Problem

The workflow always starts from `REQ_COLLECTING`, requiring the user to submit a requirement document through the API. When a requirement document already exists (e.g., written in Feishu or local editor), the user must copy-paste it into the `submit-requirement` endpoint. There is no way to upload a `.md` or `.docx` file directly, and no way to skip the REQ stage.

## Solution

Add a file upload endpoint that accepts `.md` or `.docx` files, converts `.docx` to markdown via `pandoc`, stores the result at the conventional path, registers it as an artifact, and creates the run starting at `DESIGN_QUEUED` — skipping `REQ_COLLECTING` and `REQ_REVIEW`. The Dashboard gets a "Create Run" dialog with optional file upload. The workflow skill gets updated instructions for file-based creation.

## Design

### 1. Backend: Upload Endpoint

#### New route: `POST /api/v1/runs/upload-requirement`

- **Content-Type:** `multipart/form-data`
- **Form fields:**
  - `file` (required): `.md` or `.docx` file
  - `ticket` (required): ticket identifier
  - `repo_path` (required): path to the git repo
  - `description` (optional): run description
  - `notify_channel` (optional)
  - `notify_to` (optional)
  - `repo_url` (optional)
  - `design_agent` (optional): `"claude"` or `"codex"`
  - `dev_agent` (optional): `"claude"` or `"codex"`

#### Processing flow:

1. Validate `file` extension is `.md` or `.docx`.
2. Save the uploaded file to a temporary location.
3. If `.docx`: run `pandoc -f docx -t markdown -o output.md input.docx`. If `pandoc` is not available, return `500` with a clear error message.
4. Write the resulting markdown to `{repo_path}/docs/req/REQ-{ticket}.md`.
5. Call `state_machine.create_run()` with the provided fields.
6. Register artifact (`kind=req`, path=`REQ-{ticket}.md`, stage=`REQ_COLLECTING`).
7. Record approvals: insert an auto-approval record for the `req` gate (`by="upload"`).
8. Update stage directly: `REQ_COLLECTING` → `DESIGN_QUEUED` (skip `REQ_REVIEW`).
9. Emit event: `requirement.uploaded` with `{"path": ..., "original_filename": ...}`.
10. Clean up temporary files.
11. Return the run object.

#### New file: `src/file_converter.py`

```python
async def convert_docx_to_md(input_path: Path, output_path: Path) -> None:
    """Convert .docx to .md using pandoc. Raises RuntimeError if pandoc unavailable."""

def validate_upload(filename: str) -> str:
    """Validate file extension. Returns 'md' or 'docx'. Raises BadRequestError on invalid."""
```

#### Route location: `routes/runs.py`

Add the new endpoint alongside the existing `POST /runs`:

```python
@router.post("/runs/upload-requirement", status_code=201)
async def create_run_with_requirement(
    file: UploadFile,
    ticket: str = Form(...),
    repo_path: str = Form(...),
    description: str | None = Form(None),
    notify_channel: str | None = Form(None),
    notify_to: str | None = Form(None),
    repo_url: str | None = Form(None),
    design_agent: str | None = Form(None),
    dev_agent: str | None = Form(None),
    request: Request,
):
```

### 2. State Machine: New method

Add `create_run_with_requirement()` to `StateMachine`:

```python
async def create_run_with_requirement(
    self, ticket, repo_path, req_content: str, original_filename: str,
    description=None, preferences=None, notify_channel=None,
    notify_to=None, repo_url=None, design_agent=None, dev_agent=None,
) -> dict:
```

This method:
1. Writes `req_content` to `{repo_path}/docs/req/REQ-{ticket}.md`.
2. Calls `create_run()` (which creates the run at `REQ_COLLECTING`).
3. Registers the requirement artifact.
4. Inserts an auto-approval record for the `req` gate.
5. Updates stage: `REQ_COLLECTING` → `DESIGN_QUEUED`.
6. Emits `requirement.uploaded` event.
7. Returns the run dict.

### 3. Frontend: Create Run Dialog

#### Location: `web/src/pages/RunsListPage.tsx`

Add a "创建任务" button in the page header that opens a modal dialog.

#### Dialog fields:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| 工单 (ticket) | text input | Yes | |
| 仓库路径 (repo_path) | text input | Yes | |
| 描述 (description) | textarea | No | |
| 需求文档 | file drop zone | No | Accept `.md`, `.docx` |
| 设计 Agent | select | No | claude / codex |
| 开发 Agent | select | No | claude / codex |

#### Behavior:

- **No file uploaded:** `POST /api/v1/runs` (JSON body) → normal flow from `REQ_COLLECTING`.
- **File uploaded:** `POST /api/v1/runs/upload-requirement` (multipart/form-data) → skip to `DESIGN_QUEUED`.
- Show upload status indicator (uploading / converting / done).
- On success: navigate to the new run's detail page.
- On error: show error message in the dialog.

#### API client addition (`web/src/api/runs.ts`):

```typescript
export async function createRun(payload: CreateRunPayload): Promise<RunRecord> { ... }
export async function createRunWithRequirement(formData: FormData): Promise<RunRecord> { ... }
```

The `createRunWithRequirement` function uses `fetch` directly with `FormData` (no `Content-Type` header — browser sets multipart boundary automatically).

### 4. Dashboard UI Design (pencil)

Update `pencil/dashboard.pen` to include the Create Run dialog mockup:
- Button position: top-right of RunsListPage, next to the "刷新" button.
- Dialog: centered modal with backdrop, rounded corners matching existing design system.
- File drop zone: dashed border area with icon, supporting click-to-browse and drag-and-drop.
- File type hint: "支持 .md 和 .docx 文件" below the drop zone.

### 5. Skill Update

#### `skills/cooagents-workflow/SKILL.md`

Update §B stage decision tree — `(新任务)` row:
- If user provides a requirement document file: use `upload-requirement` endpoint.
- Otherwise: use existing `POST /runs` + `submit-requirement` + `tick` flow.

#### `skills/cooagents-workflow/references/api-playbook.md`

Add §1b documenting the new `upload-requirement` endpoint with curl example:

```bash
curl -s -X POST http://127.0.0.1:8321/api/v1/runs/upload-requirement \
  -F "file=@/path/to/REQ-PROJ-123.md" \
  -F "ticket=PROJ-123" \
  -F "repo_path=/path/to/repo"
```

### 6. Dependencies

- `pandoc` CLI must be installed on the host for `.docx` support.
- `python-multipart` pip package (required by FastAPI for file uploads). Add to `requirements.txt` if not present.
- `.md` files work without pandoc — no external dependency.

### 7. Error Handling

| Scenario | Response |
|----------|----------|
| Invalid file extension | 400: "Only .md and .docx files are supported" |
| pandoc not installed (for .docx) | 500: "pandoc is required for .docx conversion but not found" |
| pandoc conversion fails | 500: "Document conversion failed: {stderr}" |
| repo_path not a git repo | 400: existing validation from `create_run()` |

## Files to Modify

| File | Change |
|------|--------|
| `src/file_converter.py` | New: pandoc conversion + file validation |
| `src/state_machine.py` | Add `create_run_with_requirement()` |
| `routes/runs.py` | Add `POST /runs/upload-requirement` endpoint |
| `requirements.txt` | Add `python-multipart` if missing |
| `web/src/api/runs.ts` | Add `createRun()`, `createRunWithRequirement()` |
| `web/src/api/client.ts` | Add `apiUpload()` helper for multipart requests |
| `web/src/pages/RunsListPage.tsx` | Add "创建任务" button + modal dialog |
| `web/src/types/index.ts` | Add `CreateRunPayload` type |
| `pencil/dashboard.pen` | Update UI design with create run dialog |
| `skills/cooagents-workflow/SKILL.md` | Update §B for upload path |
| `skills/cooagents-workflow/references/api-playbook.md` | Add §1b upload-requirement |

## Constraints

- Only `.md` and `.docx` file extensions accepted.
- `.docx` conversion requires `pandoc` on the host PATH.
- Upload file size limit: 10MB (FastAPI default is sufficient).
- Original `POST /runs` endpoint unchanged — fully backward compatible.
- The uploaded requirement is treated as "already approved" — no REQ_REVIEW gate.
