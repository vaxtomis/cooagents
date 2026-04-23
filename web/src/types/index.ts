// Workspace-driven domain types. Mirrors src/models.py.

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

export interface Pagination {
  limit: number;
  offset: number;
  has_more: boolean;
  total?: number;
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

export interface DesignWork {
  id: string;
  workspace_id: string;
  mode: DesignWorkMode;
  current_state: DesignWorkState;
  loop: number;
  missing_sections: string[] | null;
  output_design_doc_id: string | null;
  escalated_at: string | null;
  title: string | null;
  sub_slug: string | null;
  version: string | null;
  created_at: string;
  updated_at: string;
}

export interface DesignDoc {
  id: string;
  workspace_id: string;
  slug: string;
  version: string;
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
}

export interface DevIterationNote {
  id: string;
  dev_work_id: string;
  round: number;
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
}

export interface CreateDevWorkPayload {
  workspace_id: string;
  design_doc_id: string;
  repo_path: string;
  prompt: string;
  agent?: AgentKind;
}
