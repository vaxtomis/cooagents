PRAGMA journal_mode=WAL;

-- ============================================================================
-- Phase 1: Workspace-Driven Data Model
-- Breaking rewrite — old tables (runs/steps/events/approvals/artifacts/jobs/
-- merge_queue/turns/webhooks/agent_hosts) are dropped wholesale.
-- ============================================================================

-- 1. workspaces — workspace container
CREATE TABLE IF NOT EXISTS workspaces (
  id          TEXT PRIMARY KEY,              -- 'ws-<hex12>'
  title       TEXT NOT NULL,
  slug        TEXT NOT NULL UNIQUE,
  status      TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
  root_path   TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

-- 2. design_docs — DesignDoc artifact index (created before design_works since
--    design_works.output_design_doc_id soft-references this table)
CREATE TABLE IF NOT EXISTS design_docs (
  id                      TEXT PRIMARY KEY,  -- 'des-<hex12>'
  workspace_id            TEXT NOT NULL REFERENCES workspaces(id),
  slug                    TEXT NOT NULL,
  version                 TEXT NOT NULL,     -- SemVer string '1.0.0'
  -- Workspace-relative POSIX path, e.g. "designs/DES-login-1.0.0.md".
  -- Phase 2 flips the semantic; Phase 3 flips the writers.
  path                    TEXT NOT NULL,
  parent_version          TEXT,
  needs_frontend_mockup   INTEGER NOT NULL DEFAULT 0 CHECK(needs_frontend_mockup IN (0,1)),
  rubric_threshold        INTEGER NOT NULL DEFAULT 85,
  status                  TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','superseded')),
  content_hash            TEXT,
  byte_size               INTEGER,
  created_at              TEXT NOT NULL,
  published_at            TEXT,
  UNIQUE(workspace_id, slug, version)
);

-- 3. design_works — DesignWork state machine instance (process table)
CREATE TABLE IF NOT EXISTS design_works (
  id                      TEXT PRIMARY KEY,  -- 'desw-<hex12>'
  workspace_id            TEXT NOT NULL REFERENCES workspaces(id),
  mode                    TEXT NOT NULL CHECK(mode IN ('new','optimize')),
  parent_version          TEXT,
  needs_frontend_mockup   INTEGER NOT NULL DEFAULT 0 CHECK(needs_frontend_mockup IN (0,1)),
  current_state           TEXT NOT NULL DEFAULT 'INIT' CHECK(current_state IN ('INIT','MODE_BRANCH','PRE_VALIDATE','PROMPT_COMPOSE','LLM_GENERATE','MOCKUP','POST_VALIDATE','PERSIST','COMPLETED','ESCALATED','CANCELLED')),
  loop                    INTEGER NOT NULL DEFAULT 0,
  missing_sections_json   TEXT,
  agent                   TEXT NOT NULL DEFAULT 'claude' CHECK(agent IN ('claude','codex')),
  escalated_at            TEXT,
  -- Workspace-relative POSIX (e.g. "designs/.drafts/desw-<id>-input.md").
  user_input_path         TEXT,
  output_design_doc_id    TEXT,              -- soft reference (no FK, U12)
  -- Phase 3 additions (U7): runtime-required but nullable so compat migrations
  -- (ALTER TABLE ADD COLUMN) can widen a Phase 1 DB without NOT NULL errors.
  -- The state machine enforces non-null invariants at create().
  title                   TEXT,
  sub_slug                TEXT,
  version                 TEXT,
  -- Workspace-relative POSIX (mirrors design_docs.path on publish).
  output_path             TEXT,
  gates_json              TEXT,
  created_at              TEXT NOT NULL,
  updated_at              TEXT NOT NULL
);

-- 4. dev_works — DevWork state machine instance + indicator fields
CREATE TABLE IF NOT EXISTS dev_works (
  id                          TEXT PRIMARY KEY,  -- 'dev-<hex12>'
  workspace_id                TEXT NOT NULL REFERENCES workspaces(id),
  design_doc_id               TEXT NOT NULL REFERENCES design_docs(id),
  repo_path                   TEXT NOT NULL,
  prompt                      TEXT NOT NULL,
  worktree_path               TEXT,
  worktree_branch             TEXT,
  current_step                TEXT NOT NULL DEFAULT 'INIT' CHECK(current_step IN ('INIT','STEP1_VALIDATE','STEP2_ITERATION','STEP3_CONTEXT','STEP4_DEVELOP','STEP5_REVIEW','COMPLETED','ESCALATED','CANCELLED')),
  iteration_rounds            INTEGER NOT NULL DEFAULT 0,
  first_pass_success          INTEGER CHECK(first_pass_success IN (0,1)),
  last_score                  INTEGER,
  last_problem_category       TEXT CHECK(last_problem_category IN ('req_gap','impl_gap','design_hollow') OR last_problem_category IS NULL),
  agent                       TEXT NOT NULL DEFAULT 'claude' CHECK(agent IN ('claude','codex')),
  gates_json                  TEXT,
  escalated_at                TEXT,
  completed_at                TEXT,
  created_at                  TEXT NOT NULL,
  updated_at                  TEXT NOT NULL
);

-- 5. dev_iteration_notes — iteration design file metadata (markdown body on disk)
CREATE TABLE IF NOT EXISTS dev_iteration_notes (
  id                  TEXT PRIMARY KEY,      -- 'note-<hex12>'
  dev_work_id         TEXT NOT NULL REFERENCES dev_works(id),
  round               INTEGER NOT NULL,
  -- Workspace-relative POSIX, e.g. "devworks/<dev_work_id>/iteration-round-<n>.md".
  markdown_path       TEXT NOT NULL,
  score_history_json  TEXT,
  created_at          TEXT NOT NULL,
  UNIQUE(dev_work_id, round)
);

-- 6. reviews — Step5 / D5 review records
CREATE TABLE IF NOT EXISTS reviews (
  id                      TEXT PRIMARY KEY,  -- 'rev-<hex12>'
  dev_work_id             TEXT REFERENCES dev_works(id),
  design_work_id          TEXT REFERENCES design_works(id),
  dev_iteration_note_id   TEXT REFERENCES dev_iteration_notes(id),
  round                   INTEGER NOT NULL,
  score                   INTEGER,
  issues_json             TEXT,
  findings_json           TEXT,
  problem_category        TEXT CHECK(problem_category IN ('req_gap','impl_gap','design_hollow') OR problem_category IS NULL),
  reviewer                TEXT,
  created_at              TEXT NOT NULL,
  CHECK ((dev_work_id IS NOT NULL) OR (design_work_id IS NOT NULL))
);

-- 7. workspace_events — telemetry event log (pure log, no delivery state)
CREATE TABLE IF NOT EXISTS workspace_events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id        TEXT NOT NULL UNIQUE,
  event_name      TEXT NOT NULL,
  workspace_id    TEXT REFERENCES workspaces(id),
  correlation_id  TEXT,
  payload_json    TEXT,
  ts              TEXT NOT NULL
);

-- 8. workspace_files — authoritative per-workspace file inventory.
--    Rows are created by Phase 5 WorkspaceFileRegistry.register() (Phase 2
--    ships the shape only). `relative_path` is workspace-relative POSIX
--    (no leading '/', no backslash, no drive letter); the workspace slug
--    is implicit via workspace_id. `oss_key` includes the deployment
--    prefix + workspace slug (e.g. "workspaces/<slug>/designs/DES-x-1.md")
--    and is NULL when OSS is disabled. `oss_etag` CAS-guards writes.
CREATE TABLE IF NOT EXISTS workspace_files (
  id                TEXT PRIMARY KEY,              -- 'wf-<hex12>'
  workspace_id      TEXT NOT NULL REFERENCES workspaces(id),
  relative_path     TEXT NOT NULL,                 -- POSIX, no leading '/'
  kind              TEXT NOT NULL CHECK(kind IN (
                        'design_doc','design_input','iteration_note',
                        'prompt','image','workspace_md',
                        'context','artifact','other')),
  content_hash      TEXT,                          -- sha256 of local bytes; NULL before first write
  byte_size         INTEGER,
  oss_key           TEXT,                          -- NULL when oss.enabled=false or never flushed
  oss_etag          TEXT,                          -- NULL when never flushed
  local_mtime_ns    INTEGER,
  last_synced_at    TEXT,                          -- ISO-8601, NULL until first successful flush
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  UNIQUE(workspace_id, relative_path)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_workspaces_status           ON workspaces(status);
CREATE INDEX IF NOT EXISTS idx_design_works_workspace      ON design_works(workspace_id);
CREATE INDEX IF NOT EXISTS idx_design_works_state          ON design_works(current_state);
CREATE INDEX IF NOT EXISTS idx_design_docs_workspace       ON design_docs(workspace_id);
CREATE INDEX IF NOT EXISTS idx_design_docs_slug            ON design_docs(slug);
CREATE INDEX IF NOT EXISTS idx_dev_works_workspace         ON dev_works(workspace_id);
CREATE INDEX IF NOT EXISTS idx_dev_works_step              ON dev_works(current_step);
CREATE INDEX IF NOT EXISTS idx_dev_works_design_doc        ON dev_works(design_doc_id);
-- Phase 4 invariant C1: at most one active DevWork per design_doc. Partial
-- UNIQUE index (SQLite 3.8+) enforces this atomically, closing the
-- SELECT-then-INSERT race that the route-level check cannot prevent.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_dev_works_active_per_design_doc
  ON dev_works(design_doc_id)
  WHERE current_step NOT IN ('COMPLETED','ESCALATED','CANCELLED');
CREATE INDEX IF NOT EXISTS idx_dev_iteration_notes_work    ON dev_iteration_notes(dev_work_id);
CREATE INDEX IF NOT EXISTS idx_reviews_dev_work            ON reviews(dev_work_id);
CREATE INDEX IF NOT EXISTS idx_reviews_design_work         ON reviews(design_work_id);
CREATE INDEX IF NOT EXISTS idx_reviews_note                ON reviews(dev_iteration_note_id);
CREATE INDEX IF NOT EXISTS idx_workspace_events_name       ON workspace_events(event_name);
CREATE INDEX IF NOT EXISTS idx_workspace_events_workspace  ON workspace_events(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_events_ts         ON workspace_events(ts);
CREATE INDEX IF NOT EXISTS idx_workspace_files_workspace   ON workspace_files(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_files_kind        ON workspace_files(kind);
CREATE INDEX IF NOT EXISTS idx_workspace_files_oss_key     ON workspace_files(oss_key)
  WHERE oss_key IS NOT NULL;

-- 9. webhook_subscriptions — outbound webhook delivery targets
--    Replaces the legacy `webhooks` table dropped in Phase 1.
--    slug: 'openclaw' / 'hermes' for builtin; NULL for user-registered.
--    secret: HMAC secret for generic path; Bearer token for OpenClaw path.
--            Supports '$ENV:VARNAME' redirection via _resolve_secret.
--    events_json: JSON list[str]; NULL means subscribe to all KNOWN_EVENTS.
CREATE TABLE IF NOT EXISTS webhook_subscriptions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  slug          TEXT UNIQUE,
  url           TEXT NOT NULL,
  secret        TEXT,
  events_json   TEXT,
  active        INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_subscriptions_active
  ON webhook_subscriptions(active);
