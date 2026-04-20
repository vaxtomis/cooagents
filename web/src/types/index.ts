export type GateName = "req" | "design" | "dev";
export type RunStatus = "running" | "completed" | "failed" | "cancelled" | string;
export type JobStatus =
  | "starting"
  | "running"
  | "completed"
  | "failed"
  | "timeout"
  | "cancelled"
  | "interrupted"
  | string;

export const DASHBOARD_STAGE_FLOW = [
  "REQ_COLLECTING",
  "REQ_REVIEW",
  "DESIGN_QUEUED",
  "DESIGN_DISPATCHED",
  "DESIGN_RUNNING",
  "DESIGN_REVIEW",
  "DEV_QUEUED",
  "DEV_DISPATCHED",
  "DEV_RUNNING",
  "DEV_REVIEW",
  "MERGE_QUEUED",
  "MERGING",
  "MERGE_CONFLICT",
  "MERGED",
] as const;

export type DashboardStage = (typeof DASHBOARD_STAGE_FLOW)[number] | "INIT" | "FAILED" | string;

export interface Pagination {
  limit: number;
  offset: number;
  has_more: boolean;
  total?: number;
}

export interface StepRecord {
  id?: number;
  run_id: string;
  from_stage: DashboardStage;
  to_stage: DashboardStage;
  triggered_by?: string | null;
  created_at: string;
}

export interface ApprovalRecord {
  id?: number;
  run_id: string;
  gate: GateName;
  decision: "approved" | "rejected";
  by: string;
  comment?: string | null;
  created_at: string;
}

export interface EventRecord {
  id?: number;
  run_id?: string | null;
  event_type: string;
  created_at: string;
  payload?: unknown;
  trace_id?: string | null;
  job_id?: string | null;
  span_type?: string | null;
  level?: string | null;
  duration_ms?: number | null;
  error_detail?: string | null;
  source?: string | null;
  ticket?: string | null;
}

export interface ArtifactRecord {
  id: number;
  run_id: string;
  kind: string;
  path: string;
  version: number;
  status: string;
  content_hash?: string | null;
  byte_size?: number | null;
  stage?: DashboardStage | null;
  git_ref?: string | null;
  review_comment?: string | null;
  created_at: string;
}

export interface ArtifactContentResponse extends ArtifactRecord {
  content: string;
}

export interface ArtifactDiffResponse {
  artifact_id: number;
  diff: string;
}

export interface JobRecord {
  id: string;
  run_id: string;
  host_id?: string | null;
  agent_type: string;
  stage: DashboardStage | string;
  status: JobStatus;
  task_file?: string | null;
  worktree?: string | null;
  base_commit?: string | null;
  pid?: number | null;
  ssh_session_id?: string | null;
  snapshot_json?: string | null;
  resume_count?: number | null;
  session_name?: string | null;
  turn_count?: number | null;
  events_file?: string | null;
  timeout_sec?: number | null;
  running_started_at?: string | null;
  started_at: string;
  ended_at?: string | null;
}

export interface JobOutputResponse {
  job_id: string;
  output: string;
}

export interface RunRecord {
  id: string;
  run_id?: string;
  ticket: string;
  repo_path: string;
  repo_url?: string | null;
  status: RunStatus;
  current_stage: DashboardStage;
  description?: string | null;
  failed_at_stage?: DashboardStage | null;
  design_worktree?: string | null;
  design_branch?: string | null;
  dev_worktree?: string | null;
  dev_branch?: string | null;
  preferences_json?: string | null;
  notify_channel?: string | null;
  notify_to?: string | null;
  created_at: string;
  updated_at: string;
  steps?: StepRecord[];
  approvals?: ApprovalRecord[];
  recent_events?: EventRecord[];
  artifacts?: ArtifactRecord[];
}

export interface RunsListResponse {
  items: RunRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface BriefCurrentStage {
  stage: DashboardStage;
  description: string;
  action_type: string;
  since?: string | null;
  elapsed_sec?: number | null;
  summary: string;
  job_id?: string;
  job_status?: JobStatus;
  agent_type?: string;
  turn_count?: number;
  host?: string | null;
}

export interface RunBrief {
  run_id: string;
  ticket: string;
  status: RunStatus;
  created_at: string;
  current: BriefCurrentStage;
  previous?: {
    stage: DashboardStage;
    result?: string | null;
    reason?: string | null;
    by?: string | null;
    at?: string | null;
    triggered_by?: string | null;
  } | null;
  progress: {
    gates_passed: GateName[];
    gates_remaining: GateName[];
    artifacts_count: number;
  };
}

export interface RunTraceResponse {
  run_id: string;
  status: RunStatus;
  current_stage: DashboardStage;
  failed_at_stage?: DashboardStage | null;
  created_at: string;
  summary: {
    total_events: number;
    errors: number;
    warnings: number;
    stages_visited: DashboardStage[];
    total_duration_ms?: number | null;
    jobs: Array<{
      job_id: string;
      stage: DashboardStage | string;
      status: JobStatus;
      duration_ms?: number | null;
    }>;
  };
  events: EventRecord[];
  pagination: Pagination;
}

export interface JobDiagnosisResponse {
  job_id: string;
  run_id?: string | null;
  host_id?: string | null;
  agent_type: string;
  stage: DashboardStage | string;
  status: JobStatus;
  session_name?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  diagnosis: {
    duration_ms?: number | null;
    turn_count?: number | null;
    error_summary?: string | null;
    error_detail?: string | null;
    last_output_excerpt?: string | null;
    failure_context: {
      stage_at_failure?: string | null;
      host_status_at_failure?: string | null;
      retry_count?: number | null;
    };
  };
  events: EventRecord[];
  turns: Array<Record<string, unknown>>;
}

export interface TraceLookupResponse {
  trace_id: string;
  origin: string;
  first_seen?: string | null;
  last_seen?: string | null;
  total_duration_ms?: number | null;
  affected_runs: string[];
  affected_jobs: string[];
  error_count: number;
  events: EventRecord[];
}

export interface AgentHost {
  id: string;
  host: string;
  agent_type: string;
  max_concurrent: number;
  ssh_key?: string | null;
  labels_json?: string | null;
  labels: string[];
  status: string;
  current_load: number;
  created_at: string;
  updated_at: string;
}

export interface MergeQueueItem {
  id: number;
  run_id: string;
  branch: string;
  priority: number;
  status: string;
  conflict_files: string[];
  created_at: string;
  updated_at: string;
}

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

// The server derives `by` from the authenticated session; clients must not
// send it, so it's omitted from the payload shape.
export interface ApprovePayload {
  gate: GateName;
  comment?: string;
}

export interface RejectPayload {
  gate: GateName;
  reason: string;
}
