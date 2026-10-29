// Mirrors the response models of app/board_api.py.

export type Column = "backlog" | "working" | "needs_human" | "done" | "failed";
export type Kind = "epic" | "story" | "task";

export interface ProjectIn {
  slug: string;
  repo: string;
  base_branch?: string | null;
  verify_command?: string | null;
  installation_id?: number | null;
}

// PATCH /projects/{slug}: fields left out stay as they are, null clears them.
export interface ProjectPatch {
  base_branch?: string | null;
  verify_command?: string | null;
}

export interface ProjectOut {
  id: string;
  slug: string;
  repo: string;
  base_branch: string | null;
  verify_command: string | null;
  installation_id: number | null;
}

export interface ItemIn {
  title: string;
  body?: string;
  kind?: Kind;
  workflow?: string;
  parent_id?: string | null;
  issue?: number | null;
  priority?: number;
  start?: boolean;
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
  updated_at: string;
  column: Column;
  progress: Progress | null;
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

export interface GitSnapshot {
  branch: string;
  head: string;
  merge_in_progress: boolean;
  status: string;
  commits: string;
  stat: string;
  diff?: string;
}

export type Board = Record<Column, ItemOut[]>;

export interface SchedulerStatus {
  capacity: number;
  reason: string;
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
