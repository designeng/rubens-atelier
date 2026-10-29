import { describe, expect, it } from "vitest";
import type { TaskEvent } from "../api/types";
import { describeTool, resultText, transcript } from "../session";

let n = 0;
const ev = (type: string, data: Record<string, unknown>): TaskEvent => ({ id: `${++n}-0`, type, data });

describe("transcript", () => {
  it("pairs tool calls with their results and keeps the order", () => {
    const out = transcript([
      ev("session", { session_id: "s1", model: "m" }),
      ev("turn", { turn: 1 }),
      ev("text", { text: "looking" }),
      ev("tool_use", { id: "t1", name: "Bash", input: { command: "make test\nmore" } }),
      ev("tool_use", { id: "t2", name: "Read", input: { file_path: "a.py" } }),
      ev("tool_result", { tool_use_id: "t2", is_error: false, content: [{ type: "text", text: "code" }] }),
      ev("tool_result", { tool_use_id: "t1", is_error: true, content: "boom" }),
      ev("verify", { command: "make test", exit_code: 1, output: "FAIL" }),
      ev("result", { subtype: "success", num_turns: 3, total_cost_usd: 0.5, duration_ms: 12400, result: "done", is_error: false }),
    ]);
    expect(out).toEqual([
      { kind: "note", text: "session s1 · m" },
      { kind: "text", text: "looking" },
      { kind: "tool", id: "t1", name: "Bash", summary: "Bash: make test", input: { command: "make test\nmore" }, result: { content: "boom", isError: true } },
      { kind: "tool", id: "t2", name: "Read", summary: "Read: a.py", input: { file_path: "a.py" }, result: { content: "code", isError: false } },
      { kind: "verify", command: "make test", exitCode: 1, output: "FAIL" },
      { kind: "result", summary: "success · 3 turns · $0.50 · 12s", text: "done", isError: false },
    ]);
  });

  it("leaves a running call without a result", () => {
    const [e] = transcript([ev("tool_use", { id: "t", name: "Grep", input: { pattern: "x" } })]);
    expect(e).toMatchObject({ kind: "tool", summary: "Grep: x", result: null });
  });
});

describe("helpers", () => {
  it("describes a tool by its main argument", () => {
    expect(describeTool("TodoWrite", { todos: [] })).toBe("TodoWrite");
    expect(describeTool("Bash", { command: "" })).toBe("Bash");
  });

  it("joins content blocks", () => {
    expect(resultText([{ type: "text", text: "a" }, { type: "image" }])).toBe('a\n{\n  "type": "image"\n}');
    expect(resultText(null)).toBe("");
  });
});
