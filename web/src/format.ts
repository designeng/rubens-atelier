import type { Progress, Todo } from "./api/types";

export function elapsed(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const s = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

// p: the item's `progress` from the API (only set while the item is in the working column).
export function progressSummary(p: Progress, now = Date.now()): string {
  if (p.kind === "check") return `waiting for ${p.step}`;
  if (p.kind === "action") return `running ${p.step}`;
  if (!p.task_id) return p.queued ? `${p.step} · waiting for a free agent slot` : `${p.step} · starting`;
  const todos = p.todos ? `todos ${p.todos.filter((t) => t.status === "completed").length}/${p.todos.length}` : null;
  return [p.step, p.turn != null ? `turn ${p.turn}/${p.max_turns}` : null, todos, elapsed(p.started_at, now)]
    .filter(Boolean)
    .join(" · ");
}

// Share of the turn budget used, for the bar under the progress line; null before the first turn.
export function turnPercent(p: Progress): number | null {
  if (p.turn == null || !p.max_turns) return null;
  return Math.min(100, Math.round((100 * p.turn) / p.max_turns));
}

export function todoMark(status: Todo["status"]): string {
  return status === "completed" ? "☑" : status === "in_progress" ? "▸" : "☐";
}
