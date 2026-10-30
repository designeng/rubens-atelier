import type { Attention, EventOut, Progress, RunOut, Todo } from "./api/types";

export function elapsed(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const s = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

// p: the item's `progress` from the API (only set while the item is in the working column);
// paused: the scheduler's queue is paused, so a queued step waits for the operator, not a slot.
export function progressSummary(p: Progress, now = Date.now(), paused = false): string {
  if (p.kind === "check") return `waiting for ${p.step}`;
  if (p.kind === "action") return `running ${p.step}`;
  if (!p.task_id) {
    if (!p.queued) return `${p.step} · starting`;
    return paused ? `${p.step} · queued, the queue is paused` : `${p.step} · waiting for a free agent slot`;
  }
  if (p.status === "queued") return `${p.step} · waiting for a worker`;
  const todos = p.todos ? `todos ${p.todos.filter((t) => t.status === "completed").length}/${p.todos.length}` : null;
  return [p.step, p.turn != null ? `turn ${p.turn}/${p.max_turns}` : null, todos, elapsed(p.started_at, now)]
    .filter(Boolean)
    .join(" · ");
}

// Whether the step is doing work right now (the card's spinner), not waiting for a slot, worker or CI.
export function isRunning(p: Progress): boolean {
  return p.kind === "action" || (p.kind === "agent" && p.status === "running");
}

// An agent step in the scheduler's queue: the operator may start it now (POST /items/{id}/run-now).
export const canRunNow = (p: Progress): boolean => p.kind === "agent" && !!p.queued && !p.task_id;

// 1536 -> "1.5 KB"; the size of a workspace on disk.
export function formatBytes(n: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${i === 0 || n >= 10 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
}

// 1.5 -> "$1.50"
export const formatUsd = (n: number): string => `$${n.toFixed(2)}`;

// "$1.25 / $5.00" with a limit, "$1.25" without; null when nothing is spent and nothing is limited.
export function spendText(spent: number, budget: number | null | undefined): string | null {
  if (budget == null) return spent > 0 ? formatUsd(spent) : null;
  return `${formatUsd(spent)} / ${formatUsd(budget)}`;
}

// The limit is used up: no new agent run starts until it is raised.
export const overBudget = (spent: number, budget: number | null | undefined): boolean =>
  budget != null && spent >= budget;

export type Tone = "ok" | "bad" | "warn" | "info" | "muted";

const BADGES: Record<string, [string, Tone]> = {
  pr: ["PR", "ok"],
  error: ["ERROR", "bad"],
  questions: ["QUESTION", "info"],
  needs_clarification: ["QUESTION", "info"],
  needs_human: ["ESCALATED", "warn"],
  too_big: ["TOO BIG", "warn"],
  timeout: ["TIMEOUT", "warn"],
  exhausted: ["LOOP LIMIT", "bad"],
};

// The label on a needs_human card; an unknown verdict from a custom workflow is shown as is.
export function attentionBadge(a: Attention): { label: string; tone: Tone } {
  const [label, tone] = BADGES[a.kind] ?? [a.kind.replace(/_/g, " ").toUpperCase(), "muted"];
  return { label, tone };
}

// Share of the turn budget used, for the bar under the progress line; null before the first turn.
export function turnPercent(p: Progress): number | null {
  if (p.turn == null || !p.max_turns) return null;
  return Math.min(100, Math.round((100 * p.turn) / p.max_turns));
}

export function todoMark(status: Todo["status"]): string {
  return status === "completed" ? "☑" : status === "in_progress" ? "▸" : "☐";
}

// Why a step ran again: for every run after the first of its step, the transition that sent the
// story back into it ("sync_base: conflict", "review: changes_requested"), keyed by run id.
export function rerunReasons(runs: RunOut[], events: EventOut[]): Map<string, string> {
  const reasons = new Map<string, string>();
  const seen = new Set<string>();
  for (const r of runs) {
    if (seen.has(r.state)) {
      const started = Date.parse(r.created_at);
      const entered = events.filter((e) => e.to_state === r.state && Date.parse(e.at) <= started).at(-1);
      if (entered?.from_state) reasons.set(r.id, [entered.from_state, entered.verdict].filter(Boolean).join(": "));
    }
    seen.add(r.state);
  }
  return reasons;
}
