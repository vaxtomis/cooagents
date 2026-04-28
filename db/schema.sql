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

-- 2b. agent_hosts — Phase 8a: registered agent execution hosts.
--     'local' (always present) plus operator-registered remote SSH targets.
--     Referenced by design_works.agent_host_id and dev_works.agent_host_id.
CREATE TABLE IF NOT EXISTS agent_hosts (
  id              TEXT PRIMARY KEY,                  -- 'local' or 'ah-<hex12>'
  host            TEXT NOT NULL,                     -- 'local' or 'user@ip[:port]'
  agent_type      TEXT NOT NULL CHECK(agent_type IN ('claude','codex','both')),
  max_concurrent  INTEGER NOT NULL DEFAULT 1,
  ssh_key         TEXT,                              -- absolute path or NULL (local)
  labels_json     TEXT NOT NULL DEFAULT '[]',
  health_status   TEXT NOT NULL DEFAULT 'unknown'
                  CHECK(health_status IN ('unknown','healthy','unhealthy')),
  last_health_at  TEXT,
  last_health_err TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

-- 2c. agent_dispatches — per-LLM-call dispatch lifecycle row (Phase 8a).
--     Inserted in queued state, transitions to running, then succeeded/failed/timeout.
CREATE TABLE IF NOT EXISTS agent_dispatches (
  id               TEXT PRIMARY KEY,                 -- 'ad-<hex12>'
  host_id          TEXT NOT NULL REFERENCES agent_hosts(id),
  workspace_id     TEXT NOT NULL REFERENCES workspaces(id),
  correlation_id   TEXT NOT NULL,                    -- design_work_id or dev_work_id
  correlation_kind TEXT NOT NULL CHECK(correlation_kind IN ('design_work','dev_work')),
  state            TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed','timeout')),
  started_at       TEXT,
  finished_at      TEXT,
  exit_code        INTEGER,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

-- 2d. repos — Repo Registry (Phase 1, repo-registry feature).
--     One row per registered git repository. ``ssh_key_path`` is the
--     filesystem path to a passphraseless SSH private key, or NULL for a
--     public/ambient-auth repo. ``bare_clone_path`` is filled by Phase 2's
--     fetcher; nullable in this phase.
CREATE TABLE IF NOT EXISTS repos (
  id                TEXT PRIMARY KEY,                  -- 'repo-<hex12>'
  name              TEXT NOT NULL UNIQUE,              -- operator-facing handle
  url               TEXT NOT NULL,
  default_branch    TEXT NOT NULL DEFAULT 'main',
  ssh_key_path      TEXT,                              -- absolute path to SSH private key, or NULL
  bare_clone_path   TEXT,                              -- Phase 2 writer; NULL until then
  -- Phase 4 (repo-registry): closed enum so reviewer prompts and UI badges
  -- don't fork by free-text. Drives primary-ref auto-selection in
  -- DevWorkStateMachine._s0_init when no DevRepoRef.is_primary is set.
  role              TEXT NOT NULL DEFAULT 'other'
                    CHECK(role IN ('backend','frontend','fullstack','infra','docs','other')),
  fetch_status      TEXT NOT NULL DEFAULT 'unknown'
                    CHECK(fetch_status IN ('unknown','healthy','error')),
  last_fetched_at   TEXT,
  last_fetch_err    TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

-- 2e. design_work_repos — link DesignWork → Repo (Phase 1; rows written in
--     Phase 4+). ON DELETE RESTRICT prevents removing a repo that any
--     DesignWork still binds to.
CREATE TABLE IF NOT EXISTS design_work_repos (
  design_work_id   TEXT NOT NULL REFERENCES design_works(id),
  repo_id          TEXT NOT NULL REFERENCES repos(id) ON DELETE RESTRICT,
  branch           TEXT NOT NULL,
  rev              TEXT,
  created_at       TEXT NOT NULL,
  PRIMARY KEY (design_work_id, repo_id)
);

-- 2f. dev_work_repos — link DevWork → Repo (Phase 1; rows written in
--     Phase 4+). UNIQUE(dev_work_id, mount_name) enforces the per-DevWork
--     mount-point invariant from the PRD.
CREATE TABLE IF NOT EXISTS dev_work_repos (
  dev_work_id      TEXT NOT NULL REFERENCES dev_works(id),
  repo_id          TEXT NOT NULL REFERENCES repos(id) ON DELETE RESTRICT,
  mount_name       TEXT NOT NULL,
  base_branch      TEXT NOT NULL,
  base_rev         TEXT,
  devwork_branch   TEXT NOT NULL,
  push_state       TEXT NOT NULL DEFAULT 'pending'
                   CHECK(push_state IN ('pending','pushed','failed')),
  push_err         TEXT,
  -- Phase 4: explicit override of role-based primary-ref auto-selection.
  -- The boundary CreateDevWorkRequest validator rejects >1 row marked
  -- is_primary; the partial UNIQUE index below enforces the same at DB level.
  is_primary       INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  PRIMARY KEY (dev_work_id, repo_id),
  UNIQUE(dev_work_id, mount_name)
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
  -- Phase 8a: which agent host runs this DesignWork's LLM calls.
  -- Default 'local' keeps old DBs working; FK enforced after agent_hosts table.
  agent_host_id           TEXT NOT NULL DEFAULT 'local' REFERENCES agent_hosts(id),
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
-- Phase 4 (repo-registry): repo binding moved to dev_work_repos. Old DBs
-- with a non-null ``repo_path`` column will trip the legacy-schema warning
-- in src/app.py lifespan; operators wipe ``.coop/state.db`` and restart.
CREATE TABLE IF NOT EXISTS dev_works (
  id                          TEXT PRIMARY KEY,  -- 'dev-<hex12>'
  workspace_id                TEXT NOT NULL REFERENCES workspaces(id),
  design_doc_id               TEXT NOT NULL REFERENCES design_docs(id),
  prompt                      TEXT NOT NULL,
  worktree_path               TEXT,
  worktree_branch             TEXT,
  current_step                TEXT NOT NULL DEFAULT 'INIT' CHECK(current_step IN ('INIT','STEP1_VALIDATE','STEP2_ITERATION','STEP3_CONTEXT','STEP4_DEVELOP','STEP5_REVIEW','COMPLETED','ESCALATED','CANCELLED')),
  iteration_rounds            INTEGER NOT NULL DEFAULT 0,
  first_pass_success          INTEGER CHECK(first_pass_success IN (0,1)),
  last_score                  INTEGER,
  last_problem_category       TEXT CHECK(last_problem_category IN ('req_gap','impl_gap','design_hollow') OR last_problem_category IS NULL),
  agent                       TEXT NOT NULL DEFAULT 'claude' CHECK(agent IN ('claude','codex')),
  -- Phase 8a: which agent host runs this DevWork's LLM calls.
  agent_host_id               TEXT NOT NULL DEFAULT 'local' REFERENCES agent_hosts(id),
  gates_json                  TEXT,
  -- Phase 3 (devwork-acpx-overhaul): single-row JSON projection of the most
  -- recent heartbeat tick from LLMRunner.run_with_progress. NULL means no
  -- LLM call is in flight; the SM clears it on dispatch close.
  current_progress_json       TEXT,
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
--    Rows are created by WorkspaceFileRegistry.register() = local atomic
--    write → PUT OSS (when enabled) → DB upsert. `relative_path` is
--    workspace-relative POSIX (no leading '/', no backslash, no drive
--    letter); the workspace slug is implicit via workspace_id.
CREATE TABLE IF NOT EXISTS workspace_files (
  id                TEXT PRIMARY KEY,              -- 'wf-<hex12>'
  workspace_id      TEXT NOT NULL REFERENCES workspaces(id),
  relative_path     TEXT NOT NULL,                 -- POSIX, no leading '/'
  kind              TEXT NOT NULL CHECK(kind IN (
                        'design_doc','design_input','iteration_note',
                        'prompt','image','workspace_md',
                        'context','artifact','feedback','other')),
  content_hash      TEXT,                          -- sha256 of local bytes; NULL before first write
  byte_size         INTEGER,
  local_mtime_ns    INTEGER,
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
CREATE INDEX IF NOT EXISTS idx_agent_hosts_health          ON agent_hosts(health_status);
CREATE INDEX IF NOT EXISTS idx_agent_dispatches_correlation
  ON agent_dispatches(correlation_kind, correlation_id);
CREATE INDEX IF NOT EXISTS idx_agent_dispatches_host       ON agent_dispatches(host_id);
CREATE INDEX IF NOT EXISTS idx_agent_dispatches_state      ON agent_dispatches(state);
CREATE INDEX IF NOT EXISTS idx_repos_fetch_status          ON repos(fetch_status);
CREATE INDEX IF NOT EXISTS idx_design_work_repos_repo      ON design_work_repos(repo_id);
CREATE INDEX IF NOT EXISTS idx_dev_work_repos_repo         ON dev_work_repos(repo_id);
-- Phase 4: defense-in-depth for the at-most-one-primary rule. The
-- boundary validator on CreateDevWorkRequest is the first layer; this
-- partial UNIQUE index catches any race or direct-DB write that bypasses
-- the boundary.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_dev_work_repos_primary
  ON dev_work_repos(dev_work_id) WHERE is_primary=1;

-- Phase 8a invariant: the reserved 'local' host always exists so the
-- design_works.agent_host_id / dev_works.agent_host_id FK ('local' default)
-- always resolves. sync_from_config(agents.yaml) may overwrite the row,
-- but never deletes it. Idempotent via INSERT OR IGNORE.
INSERT OR IGNORE INTO agent_hosts(
  id, host, agent_type, max_concurrent, ssh_key, labels_json,
  health_status, created_at, updated_at
) VALUES (
  'local', 'local', 'both', 2, NULL, '[]',
  'unknown', '1970-01-01T00:00:00Z', '1970-01-01T00:00:00Z'
);

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
