import { describe, expect, it } from "vitest";
import type { EventOut, Progress, RunOut } from "../api/types";
import { attentionBadge, canRunNow, elapsed, formatBytes, isRunning, progressSummary, rerunReasons, todoMark, turnPercent } from "../format";

const now = Date.parse("2026-10-29T12:00:00Z");
const ago = (s: number) => new Date(now - s * 1000).toISOString();

describe("elapsed", () => {
  it("formats seconds, minutes and hours", () => {
    expect(elapsed(ago(59), now)).toBe("59s");
    expect(elapsed(ago(61), now)).toBe("1m 1s");
    expect(elapsed(ago(3700), now)).toBe("1h 1m");
  });

  it("is empty without a start and never negative", () => {
    expect(elapsed(null, now)).toBe("");
    expect(elapsed(ago(-30), now)).toBe("0s");
  });
});

describe("progressSummary", () => {
  it("describes checks and actions", () => {
    expect(progressSummary({ step: "ci", kind: "check" }, now)).toBe("waiting for ci");
    expect(progressSummary({ step: "open_pr", kind: "action" }, now)).toBe("running open_pr");
  });

  it("describes an agent step without a run", () => {
    expect(progressSummary({ step: "implement", kind: "agent", queued: true }, now)).toBe(
      "implement · waiting for a free agent slot",
    );
    expect(progressSummary({ step: "implement", kind: "agent", queued: false }, now)).toBe("implement · starting");
    expect(progressSummary({ step: "review", kind: "agent", queued: true }, now, true)).toBe(
      "review · queued, the queue is paused",
    );
  });

  it("describes a live agent run", () => {
    const p: Progress = {
      step: "implement",
      kind: "agent",
      task_id: "t1",
      turn: 12,
      max_turns: 80,
      started_at: ago(125),
      todos: [
        { content: "a", status: "completed" },
        { content: "b", status: "in_progress" },
        { content: "c", status: "pending" },
      ],
    };
    expect(progressSummary(p, now)).toBe("implement · turn 12/80 · todos 1/3 · 2m 5s");
  });

  it("skips the parts a fresh run does not have yet", () => {
    expect(progressSummary({ step: "plan", kind: "agent", task_id: "t1", turn: null, started_at: null }, now)).toBe(
      "plan",
    );
  });

  it("says a created run waits for a worker", () => {
    expect(progressSummary({ step: "plan", kind: "agent", task_id: "t1", status: "queued" }, now)).toBe(
      "plan · waiting for a worker",
    );
  });
});

describe("isRunning and canRunNow", () => {
  const waiting: Progress = { step: "implement", kind: "agent", queued: true };
  it("spins only for a step doing work", () => {
    expect(isRunning({ step: "open_pr", kind: "action" })).toBe(true);
    expect(isRunning({ step: "implement", kind: "agent", task_id: "t1", status: "running" })).toBe(true);
    expect(isRunning({ step: "implement", kind: "agent", task_id: "t1", status: "queued" })).toBe(false);
    expect(isRunning(waiting)).toBe(false);
    expect(isRunning({ step: "ci", kind: "check" })).toBe(false);
  });

  it("offers run now only for an agent step in the scheduler queue", () => {
    expect(canRunNow(waiting)).toBe(true);
    expect(canRunNow({ step: "implement", kind: "agent", queued: false })).toBe(false);
    expect(canRunNow({ step: "implement", kind: "agent", task_id: "t1", status: "running" })).toBe(false);
  });
});

describe("attentionBadge", () => {
  it("labels known reasons and passes unknown verdicts through", () => {
    expect(attentionBadge({ kind: "pr", text: "" })).toEqual({ label: "PR", tone: "ok" });
    expect(attentionBadge({ kind: "error", text: "boom" })).toEqual({ label: "ERROR", tone: "bad" });
    expect(attentionBadge({ kind: "needs_clarification", text: "" }).label).toBe("QUESTION");
    expect(attentionBadge({ kind: "needs_design", text: "" })).toEqual({ label: "NEEDS DESIGN", tone: "muted" });
  });
});

describe("turnPercent", () => {
  it("is capped at 100 and null before the first turn", () => {
    expect(turnPercent({ step: "s", kind: "agent", turn: 1, max_turns: 3 })).toBe(33);
    expect(turnPercent({ step: "s", kind: "agent", turn: 120, max_turns: 100 })).toBe(100);
    expect(turnPercent({ step: "s", kind: "agent", turn: null, max_turns: 100 })).toBeNull();
    expect(turnPercent({ step: "s", kind: "agent" })).toBeNull();
  });
});

describe("todoMark", () => {
  it("marks each status", () => {
    expect([todoMark("completed"), todoMark("in_progress"), todoMark("pending")]).toEqual(["☑", "▸", "☐"]);
  });
});

describe("rerunReasons", () => {
  const run = (id: string, state: string, at: string): RunOut => ({
    id, version: 1, state, role: state, task_id: id, status: "succeeded", verdict: "done", cost_usd: null, created_at: at, error: null,
  });
  const ev = (from_state: string | null, to_state: string, verdict: string | null, at: string): EventOut => ({
    from_state, to_state, verdict, actor: "worker", at,
  });

  it("names the transition that sent a story back into a step it already ran", () => {
    const runs = [
      run("p", "plan", "2026-10-29T15:09:54Z"),
      run("i1", "implement", "2026-10-29T21:18:35Z"),
      run("i2", "implement", "2026-10-29T21:19:30.4Z"),
      run("r", "review", "2026-10-29T21:20:18Z"),
    ];
    const events = [
      ev(null, "plan", null, "2026-10-29T15:08:58Z"),
      ev("plan", "implement", "ready", "2026-10-29T15:10:31Z"),
      ev("implement", "sync_base", "done", "2026-10-29T21:19:15Z"),
      ev("sync_base", "implement", "conflict", "2026-10-29T21:19:17.552Z"),
      ev("implement", "sync_base", "done", "2026-10-29T21:19:50Z"),
      ev("sync_base", "review", "done", "2026-10-29T21:19:51Z"),
    ];
    expect([...rerunReasons(runs, events)]).toEqual([["i2", "sync_base: conflict"]]);
  });
});

describe("formatBytes", () => {
  it("scales to the largest unit that keeps the number short", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1023)).toBe("1023 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(20 * 1024)).toBe("20 KB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
    expect(formatBytes(3000 * 1024 ** 4)).toBe("3000 TB");
  });
});
