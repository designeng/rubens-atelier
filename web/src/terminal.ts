import type { ExecResult } from "./api/types";

export const EXEC_POLL_MS = 1000;

export const execFinished = (r: ExecResult): boolean => r.status !== "running";

// "exit 0", "exit 2" or "timed out": the line shown above a finished command's output.
export function execSummary(r: ExecResult): string {
  if (r.status === "timeout") return "timed out";
  return `exit ${r.exit_code ?? "?"}`;
}

// Fetches the command's result every `intervalMs` until it is finished, `stopped()` says so
// (the story changed, the component is gone) or a fetch fails (e.g. 404: results are kept
// for an hour). `onUpdate` sees every result; the last one is returned, null if stopped first.
export async function pollExec(
  fetchResult: () => Promise<ExecResult>,
  onUpdate: (r: ExecResult) => void,
  stopped: () => boolean,
  intervalMs = EXEC_POLL_MS,
): Promise<ExecResult | null> {
  while (!stopped()) {
    const r = await fetchResult();
    if (stopped()) return null;
    onUpdate(r);
    if (execFinished(r)) return r;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return null;
}
