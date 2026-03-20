PRAGMA journal_mode=WAL;

-- 1. runs — workflow run tracking
CREATE TABLE IF NOT EXISTS runs (
  id              TEXT PRIMARY KEY,
  ticket          TEXT NOT NULL,
  repo_path       TEXT NOT NULL,
  repo_url        TEXT,
  status          TEXT DEFAULT 'running' CHECK(status IN ('running','completed','failed','cancelled')),
  current_stage   TEXT NOT NULL DEFAULT 'INIT',
  description     TEXT,
  failed_at_stage TEXT,
  design_worktree TEXT,
  design_branch   TEXT,
  dev_worktree    TEXT,
  dev_branch      TEXT,
  preferences_json TEXT,
  notify_channel  TEXT,
  notify_to       TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

-- 2. steps — stage transition history
CREATE TABLE IF NOT EXISTS steps (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT NOT NULL REFERENCES runs(id),
  from_stage   TEXT NOT NULL,
  to_stage     TEXT NOT NULL,
  triggered_by TEXT,
  created_at   TEXT NOT NULL
);

-- 3. events — audit log
CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT NOT NULL REFERENCES runs(id),
  event_type   TEXT NOT NULL,
  payload_json TEXT,
  created_at   TEXT NOT NULL
);

-- 4. approvals — gate approvals/rejections
CREATE TABLE IF NOT EXISTS approvals (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL REFERENCES runs(id),
  gate       TEXT NOT NULL CHECK(gate IN ('req','design','dev')),
  decision   TEXT NOT NULL CHECK(decision IN ('approved','rejected')),
  by         TEXT NOT NULL,
  comment    TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, gate, created_at)
);

-- 5. webhooks — webhook subscriptions
CREATE TABLE IF NOT EXISTS webhooks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  url         TEXT NOT NULL,
  events_json TEXT,
  secret      TEXT,
  status      TEXT DEFAULT 'active' CHECK(status IN ('active','disabled')),
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

-- 6. artifacts — artifact versioning
CREATE TABLE IF NOT EXISTS artifacts (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id         TEXT NOT NULL REFERENCES runs(id),
  kind           TEXT NOT NULL CHECK(kind IN ('req','design','adr','code','test-report')),
  path           TEXT NOT NULL,
  version        INTEGER DEFAULT 1,
  status         TEXT DEFAULT 'draft' CHECK(status IN ('draft','submitted','approved','rejected')),
  content_hash   TEXT,
  byte_size      INTEGER,
  stage          TEXT,
  git_ref        TEXT,
  review_comment TEXT,
  created_at     TEXT NOT NULL
);

-- 7. agent_hosts — host pool
CREATE TABLE IF NOT EXISTS agent_hosts (
  id             TEXT PRIMARY KEY,
  host           TEXT NOT NULL,
  agent_type     TEXT NOT NULL CHECK(agent_type IN ('claude','codex','both')),
  max_concurrent INTEGER DEFAULT 2,
  ssh_key        TEXT,
  labels_json    TEXT,
  status         TEXT DEFAULT 'active' CHECK(status IN ('active','draining','offline')),
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

-- 8. jobs — agent job tracking
CREATE TABLE IF NOT EXISTS jobs (
  id             TEXT PRIMARY KEY,
  run_id         TEXT NOT NULL REFERENCES runs(id),
  host_id        TEXT REFERENCES agent_hosts(id),
  agent_type     TEXT NOT NULL,
  stage          TEXT NOT NULL,
  status         TEXT DEFAULT 'starting' CHECK(status IN ('starting','running','completed','failed','timeout','cancelled','interrupted')),
  task_file      TEXT,
  worktree       TEXT,
  base_commit    TEXT,
  pid            INTEGER,
  ssh_session_id TEXT,
  snapshot_json  TEXT,
  resume_count   INTEGER DEFAULT 0,
  session_name   TEXT,
  turn_count     INTEGER DEFAULT 1,
  events_file    TEXT,
  timeout_sec    INTEGER,
  running_started_at TEXT,
  started_at     TEXT NOT NULL,
  ended_at       TEXT
);

-- 9. merge_queue — merge ordering
CREATE TABLE IF NOT EXISTS merge_queue (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id              TEXT NOT NULL REFERENCES runs(id) UNIQUE,
  branch              TEXT NOT NULL,
  priority            INTEGER DEFAULT 0,
  status              TEXT DEFAULT 'waiting' CHECK(status IN ('waiting','merging','merged','conflict','skipped')),
  conflict_files_json TEXT,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

-- 10. turns — per-turn history within a job
CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    turn_num    INTEGER NOT NULL,
    prompt_file TEXT,
    verdict     TEXT,
    detail      TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    UNIQUE(job_id, turn_num)
);

CREATE INDEX IF NOT EXISTS idx_turns_job ON turns(job_id);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_runs_status   ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_ticket   ON runs(ticket);
CREATE INDEX IF NOT EXISTS idx_events_run    ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_jobs_run      ON jobs(run_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status   ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);
