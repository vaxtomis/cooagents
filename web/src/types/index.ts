// Workspace-driven domain types. Mirrors src/models.py.

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

export interface Pagination {
  limit: number;
  offset: number;
  total: number;
  has_more: boolean;
}

export interface PaginatedResult<T> {
  items: T[];
  pagination: Pagination;
}

// ---------------------------------------------------------------------------
// Workspace-driven enums (string-literal unions — mirror Python enums).
// ---------------------------------------------------------------------------

export type WorkspaceStatus = "active" | "archived";
export type DesignWorkMode = "new" | "optimize";

export type DesignWorkState =
  | "INIT"
  | "MODE_BRANCH"
  | "PRE_VALIDATE"
  | "PROMPT_COMPOSE"
  | "LLM_GENERATE"
  | "MOCKUP"
  | "POST_VALIDATE"
  | "PERSIST"
  | "COMPLETED"
  | "ESCALATED"
  | "CANCELLED";

export type DesignDocStatus = "draft" | "published" | "superseded";

export type DevWorkStep =
  | "INIT"
  | "STEP1_VALIDATE"
  | "STEP2_ITERATION"
  | "STEP3_CONTEXT"
  | "STEP4_DEVELOP"
  | "STEP5_REVIEW"
  | "COMPLETED"
  | "ESCALATED"
  | "CANCELLED";

export type ProblemCategory = "req_gap" | "impl_gap" | "design_hollow";

export type AgentKind = "claude" | "codex";
export type AgentHostType = "claude" | "codex" | "both";
export type HealthStatus = "unknown" | "healthy" | "unhealthy";

// Canonical ordered happy-path arrays for stepper components.
export const DESIGN_WORK_STATE_ORDER = [
  "INIT",
  "MODE_BRANCH",
  "PRE_VALIDATE",
  "PROMPT_COMPOSE",
  "LLM_GENERATE",
  "MOCKUP",
  "POST_VALIDATE",
  "PERSIST",
  "COMPLETED",
] as const satisfies readonly DesignWorkState[];

export const DEV_WORK_STEP_ORDER = [
  "INIT",
  "STEP1_VALIDATE",
  "STEP2_ITERATION",
  "STEP3_CONTEXT",
  "STEP4_DEVELOP",
  "STEP5_REVIEW",
  "COMPLETED",
] as const satisfies readonly DevWorkStep[];

// ---------------------------------------------------------------------------
// Workspace-driven domain models
// ---------------------------------------------------------------------------

export interface Workspace {
  id: string;
  title: string;
  slug: string;
  status: WorkspaceStatus;
  root_path: string;
  created_at: string;
  updated_at: string;
}

export type WorkspacePage = PaginatedResult<Workspace>;

export interface DesignWork {
  id: string;
  workspace_id: string;
  mode: DesignWorkMode;
  current_state: DesignWorkState;
  loop: number;
  missing_sections: string[] | null;
  output_design_doc_id: string | null;
  escalated_at: string | null;
  escalation_reason: string | null;
  title: string | null;
  sub_slug: string | null;
  version: string | null;
  created_at: string;
  updated_at: string;
  is_running: boolean;
  // Phase 4 (repo-registry): persisted refs from design_work_repos.
  repo_refs: DesignRepoRefView[];
}

export type DesignWorkPage = PaginatedResult<DesignWork>;

export interface DesignWorkRetrySource {
  title: string;
  slug: string;
  user_input: string;
  needs_frontend_mockup: boolean;
  agent: AgentKind | null;
  repo_refs: RepoRef[];
}

export interface DesignDoc {
  id: string;
  workspace_id: string;
  slug: string;
  version: string;
  /**
   * Workspace-relative POSIX path, e.g. "designs/DES-login-1.0.0.md".
   * Phase 3: flipped from an absolute filesystem path to this relative form.
   * To fetch the file body, call GET /api/v1/design-docs/{id}/content.
   */
  path: string;
  parent_version: string | null;
  needs_frontend_mockup: boolean;
  rubric_threshold: number;
  status: DesignDocStatus;
  content_hash: string | null;
  byte_size: number | null;
  created_at: string;
  published_at: string | null;
}

export interface DevWork {
  id: string;
  workspace_id: string;
  design_doc_id: string;
  current_step: DevWorkStep;
  iteration_rounds: number;
  first_pass_success: boolean | null;
  last_score: number | null;
  last_problem_category: ProblemCategory | null;
  escalated_at: string | null;
  completed_at: string | null;
  worktree_path: string | null;
  worktree_branch: string | null;
  created_at: string;
  updated_at: string;
  is_running: boolean;
  progress: DevWorkProgressSnapshot | null;
  // Phase 4 (repo-registry): persisted refs from dev_work_repos.
  repo_refs: DevRepoRefView[];
  // Phase 5 (repo-registry): worker-facing handoff. Same row source as
  // repo_refs, additive url / ssh_key_path / push_err.
  repos: WorkerRepoHandoff[];
}

export type DevWorkPage = PaginatedResult<DevWork>;

export interface DevWorkProgressSnapshot {
  last_heartbeat_at: string;
  elapsed_s: number;
  step: string;
  round: number;
  dispatch_id: string | null;
}

export interface DevIterationNote {
  id: string;
  dev_work_id: string;
  round: number;
  /**
   * Workspace-relative POSIX path, e.g.
   * "devworks/<dev_work_id>/iteration-round-<n>.md".
   */
  markdown_path: string;
  score_history: number[] | null;
  created_at: string;
}

export interface Review {
  id: string;
  dev_work_id: string | null;
  design_work_id: string | null;
  dev_iteration_note_id: string | null;
  round: number;
  score: number | null;
  issues: Record<string, unknown>[] | null;
  findings: Record<string, unknown>[] | null;
  problem_category: ProblemCategory | null;
  reviewer: string | null;
  created_at: string;
}

export interface WorkspaceEvent {
  id: number | null;
  event_id: string;
  event_name: string;
  workspace_id: string | null;
  correlation_id: string | null;
  payload: Record<string, unknown> | null;
  ts: string;
}

export interface WorkspaceEventsEnvelope {
  events: WorkspaceEvent[];
  pagination: Pagination;
}

export interface WorkspaceSyncReport {
  fs_only: string[];
  db_only: string[];
  in_sync: string[];
}

// Phase 8 — four PRD Success Metrics served by GET /api/v1/metrics/workspaces.
// Rates are always numbers (0.0 when denominator is 0); never null.
export interface WorkspaceMetrics {
  human_intervention_per_workspace: number;
  active_workspaces: number;
  first_pass_success_rate: number;
  avg_iteration_rounds: number;
}

// ---------------------------------------------------------------------------
// Agent Host registry - mirrors src/models.py:432-506
// ---------------------------------------------------------------------------

export interface AgentHost {
  id: string;
  host: string;
  agent_type: AgentHostType;
  max_concurrent: number;
  labels: string[];
  health_status: HealthStatus;
  last_health_at: string | null;
  last_health_err: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateAgentHostPayload {
  id?: string | null;
  host: string;
  agent_type?: AgentHostType;
  max_concurrent?: number;
  ssh_key?: string | null;
  labels?: string[];
}

export interface UpdateAgentHostPayload {
  host?: string;
  agent_type?: AgentHostType;
  max_concurrent?: number;
  ssh_key?: string | null;
  labels?: string[];
}

export interface AgentHostsSyncReport {
  upserted: number;
  marked_unknown: number;
}

// ---------------------------------------------------------------------------
// Gate contract
// ---------------------------------------------------------------------------

export type GateStatus = "waiting" | "approved" | "rejected";

export interface GateInfo {
  gate_id: string;
  workspace_id?: string;
  work_id?: string;
  gate_key?: string;
  status: GateStatus;
  actor?: string | null;
  note?: string | null;
  acted_at?: string | null;
  // Backend may include additional contextual fields not modeled here.
  extra?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Request payloads
// ---------------------------------------------------------------------------

export interface CreateWorkspacePayload {
  title: string;
  slug: string;
}

export interface CreateDesignWorkPayload {
  workspace_id: string;
  title: string;
  slug: string;
  user_input: string;
  mode?: DesignWorkMode;
  parent_version?: string | null;
  needs_frontend_mockup?: boolean;
  agent?: AgentKind;
  rubric_threshold?: number;
  // Phase 4 (repo-registry): optional repo binding. Empty list keeps
  // pure-doc DesignWorks creatable; omit (or send `[]`) when none.
  repo_refs?: RepoRef[];
}

export interface RetryDesignWorkPayload {
  title?: string;
  slug?: string;
  user_input?: string;
  needs_frontend_mockup?: boolean;
  agent?: AgentKind | null;
  repo_refs?: RepoRef[];
}

export interface CreateDevWorkPayload {
  workspace_id: string;
  design_doc_id: string;
  // Phase 4 (repo-registry): at least one ref required; mount uniqueness
  // enforced server-side and pre-validated client-side in the form.
  repo_refs: DevRepoRef[];
  prompt: string;
  agent?: AgentKind;
}

// ---------------------------------------------------------------------------
// Repo Registry — mirrors src/models.py:481-696
// ---------------------------------------------------------------------------

export type RepoRole =
  | "backend"
  | "frontend"
  | "fullstack"
  | "infra"
  | "docs"
  | "other";

export type RepoFetchStatus = "unknown" | "healthy" | "error";

export interface Repo {
  id: string;
  name: string;
  url: string;
  default_branch: string;
  ssh_key_path: string | null;
  bare_clone_path: string | null;
  role: RepoRole;
  fetch_status: RepoFetchStatus;
  last_fetched_at: string | null;
  last_fetch_err: string | null;
  created_at: string;
  updated_at: string;
}

export type RepoPage = PaginatedResult<Repo>;

export interface CreateRepoPayload {
  name: string;
  url: string;
  default_branch?: string;
  ssh_key_path?: string | null;
  role?: RepoRole;
}

export interface UpdateRepoPayload {
  name?: string;
  url?: string;
  default_branch?: string;
  ssh_key_path?: string | null;
  role?: RepoRole;
}

export interface ReposSyncReport {
  fs_only: string[];
  db_only: string[];
  in_sync: string[];
}

export interface RepoBranches {
  default_branch: string;
  branches: string[];
}

export interface RepoTreeEntry {
  path: string;
  type: "blob" | "tree";
  mode: string;
  size: number | null;
}

export interface RepoTree {
  ref: string;
  path: string;
  entries: RepoTreeEntry[];
  truncated: boolean;
}

export interface RepoBlob {
  ref: string;
  path: string;
  size: number;
  binary: boolean;
  content: string | null;
}

export interface RepoLogEntry {
  sha: string;
  author: string;
  email: string;
  committed_at: string;
  subject: string;
}

export interface RepoLog {
  ref: string;
  path: string | null;
  entries: RepoLogEntry[];
}

export interface RepoLogPage extends PaginatedResult<RepoLogEntry> {
  ref: string;
  path: string | null;
}

export interface FetchRepoResponse {
  outcome: string;
  fetch_status: RepoFetchStatus;
  last_fetched_at: string | null;
}

// ---------------------------------------------------------------------------
// Repo refs — mirrors src/models.py:564-639 (DesignWork + DevWork sides).
// ---------------------------------------------------------------------------

// DesignWork-side repo binding (request + response sub-shape).
export interface RepoRef {
  repo_id: string;
  base_branch: string;
}

// DevWork-side repo binding (request shape).
export interface DevRepoRef extends RepoRef {
  mount_name: string;
  base_rev_lock?: boolean;
  is_primary?: boolean;
}

// Read-only view over a `design_work_repos` row.
export interface DesignRepoRefView {
  repo_id: string;
  branch: string;
  rev: string | null;
}

// Read-only view over a `dev_work_repos` row (Phase 4 progress contract).
export interface DevRepoRefView {
  repo_id: string;
  mount_name: string;
  base_branch: string;
  base_rev: string | null;
  devwork_branch: string;
  push_state: "pending" | "pushed" | "failed";
  is_primary: boolean;
}

// Worker-facing handoff payload (Phase 5). Adds operational config + push_err.
export interface WorkerRepoHandoff extends DevRepoRefView {
  url: string;
  ssh_key_path: string | null;
  push_err: string | null;
}
