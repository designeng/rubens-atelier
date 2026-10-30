// Mirrors the response models of app/board_api.py.

export type Column = "backlog" | "working" | "needs_human" | "done" | "failed";
export type Kind = "epic" | "story" | "task";

export interface ProjectIn {
  slug: string;
  repo: string;
  base_branch?: string | null;
  verify_command?: string | null;
  installation_id?: number | null;
  auto_merge?: boolean;
}

// PATCH /projects/{slug}: fields left out stay as they are, null clears them.
export interface ProjectPatch {
  base_branch?: string | null;
  verify_command?: string | null;
  auto_merge?: boolean;
}

export interface ProjectOut {
  id: string;
  slug: string;
  repo: string;
  base_branch: string | null;
  verify_command: string | null;
  installation_id: number | null;
  auto_merge: boolean;
}

export interface ItemIn {
  title: string;
  body?: string;
  kind?: Kind;
  workflow?: string;
  parent_id?: string | null;
  issue?: number | null;
  priority?: number;
  blocked_by?: string[];
  start?: boolean;
}

// PATCH /items/{id}: fields left out stay as they are; model null goes back to the default.
export interface ItemPatch {
  title?: string;
  body?: string;
  priority?: number;
  model?: string | null;
  blocked_by?: string[];
}

export interface Todo {
  content: string;
  status: "pending" | "in_progress" | "completed" | string;
  activeForm?: string;
}

// `_progress` in board_api.py plus `TaskStore.progress` in store.py for a live agent run.
export interface Progress {
  step: string;
  kind: "agent" | "action" | "check" | string;
  queued?: boolean;
  task_id?: string;
  status?: string;
  started_at?: string | null;
  turn?: number | null;
  max_turns?: number;
  activity?: string | null;
  todos?: Todo[] | null;
}

export interface ItemOut {
  id: string;
  project_id: string;
  parent_id: string | null;
  kind: string;
  title: string;
  workflow: string;
  state: string | null;
  version: number;
  branch: string | null;
  pr_url: string | null;
  priority: number;
  model: string | null;
  blocked_by: string[];
  updated_at: string;
  column: Column;
  issue: number | null;
  progress: Progress | null;
  attention?: Attention | null;
  // Backlog items only: the blockers that have not succeeded yet ("title (id)").
  blocked: string[];
  // Ids of the backlog items waiting for this one.
  blocking: string[];
}

// `_attention` in board_api.py: why an item in the needs_human column waits. `kind` is pr, error,
// questions, exhausted, or the verdict that led there (timeout, too_big, needs_clarification, ...).
export interface Attention {
  kind: string;
  text: string;
  from?: string;
}

export interface ImportResult {
  imported: ItemOut[];
  skipped: number;
}

export interface RunOut {
  id: string;
  version: number;
  state: string;
  role: string;
  task_id: string;
  status: string;
  verdict: string | null;
  cost_usd: number | null;
  created_at: string;
  error: string | null; // the `error` artifact of a run that ended in the error verdict
}

export interface ArtifactOut {
  id: string;
  name: string;
  content: string;
  created_at: string;
}

export interface EventOut {
  from_state: string | null;
  to_state: string;
  verdict: string | null;
  actor: string;
  at: string;
}

export interface RequestOut {
  id: string;
  item_id: string;
  state: string;
  question: string;
  options: string[];
  answer: string | null;
  created_at: string;
}

export interface ItemDetail extends ItemOut {
  body: string;
  runs: RunOut[];
  artifacts: ArtifactOut[];
  events: EventOut[];
  open_request: RequestOut | null;
}

export interface Models {
  default: string;
  models: string[];
}

export interface GitSnapshot {
  branch: string;
  head: string;
  merge_in_progress: boolean;
  status: string;
  commits: string;
  stat: string;
  diff?: string;
}

// POST /items/{id}/exec answers with the id to poll; GET .../exec/{id} reports the command.
export interface ExecStart {
  id: string;
}

export interface ExecResult {
  id: string;
  command: string;
  status: "running" | "done" | "timeout";
  exit_code: number | null;
  output: string | null;
}

export type Board = Record<Column, ItemOut[]>;

export interface SchedulerLimits {
  max_agents: number;
  max_agents_per_project: number;
  // Keep max_agents while a subscription window throttles to one agent.
  force: boolean;
}

export interface SchedulerStatus {
  capacity: number;
  reason: string;
  limits: SchedulerLimits;
  active: string[];
  ready: string[];
  quota: Record<string, unknown>;
}

export interface Installation {
  id: number;
  account: string;
  repository_selection: "all" | "selected" | string;
  manage_url: string | null;
  suspended: boolean;
}

export interface GithubApp {
  configured: boolean;
  install_url: string | null;
  installations: Installation[];
}

export interface Repository {
  full_name: string;
  default_branch: string;
  private: boolean;
}

// The harness task behind an agent run (app/models.py TaskInfo), as far as the board uses it.
export type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface TaskInfo {
  id: string;
  status: TaskStatus;
  prompt: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  session_id: string | null;
  error: string | null;
  cost_usd: number | null;
  num_turns: number | null;
}

// One entry of a task's event log (app/runner.py, app/execution.py, app/store.py).
export interface TaskEvent {
  id: string;
  type: string;
  data: Record<string, unknown>;
}

// GET /workspaces (app/workspaces.py): a working copy on the volume and what it belongs to.
export type WorkspacePhase = "orphan" | "finished" | "failed" | "idle" | "busy";

export interface WorkspaceOut {
  id: string;
  kind: "item" | "task" | string;
  path: string;
  size_bytes: number | null;
  modified_at: string;
  item: {
    id: string;
    project: string;
    kind: string;
    title: string;
    state: string | null;
    column: string;
    branch: string | null;
    pr_url: string | null;
    updated_at: string;
  } | null;
  task: { id: string; status: TaskStatus; prompt: string; finished_at: string | null } | null;
  phase: WorkspacePhase;
  reason: string;
  removable: boolean;
}

export interface WorkspaceList {
  root: string;
  count: number;
  size_bytes: number | null;
  workspaces: WorkspaceOut[];
}

export interface PruneIn {
  older_than_hours: number;
  include_failed: boolean;
  dry_run: boolean;
}

// The worker deletes, so a real prune only reports what it queued.
export interface PruneResult {
  dry_run: boolean;
  queued: string[];
  would_remove: string[] | null;
  size_bytes: number;
}

export interface WorkspaceQueued {
  queued: string[];
  phase: WorkspacePhase;
}
