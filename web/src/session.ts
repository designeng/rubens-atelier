import type { TaskEvent } from "./api/types";

// The agent session as the board shows it, built from the task's event log: each tool call
// carries its result, everything else is one entry per event in order.
export type Entry =
  | { kind: "text" | "thinking"; text: string }
  | { kind: "tool"; id: string; name: string; summary: string; input: unknown; result: ToolResult | null }
  | { kind: "verify"; command: string; exitCode: number; output: string }
  | { kind: "result"; summary: string; text: string; isError: boolean }
  | { kind: "note"; text: string };

export interface ToolResult {
  content: string;
  isError: boolean;
}

const str = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : JSON.stringify(v, null, 2));

// Same as describe_tool in app/store.py: which tool, on what.
export function describeTool(name: string, input: unknown): string {
  const args = (input ?? {}) as Record<string, unknown>;
  for (const key of ["command", "file_path", "pattern", "path", "url", "description"]) {
    const v = args[key];
    if (typeof v === "string") return v ? `${name}: ${v.split("\n")[0].slice(0, 120)}` : name;
  }
  return name;
}

// Tool results come as a string or as content blocks ({type: "text", text} and others).
export function resultText(content: unknown): string {
  if (!Array.isArray(content)) return str(content);
  return content
    .map((b) => (b && typeof b === "object" && "text" in b && typeof b.text === "string" ? b.text : str(b)))
    .join("\n");
}

function resultSummary(d: Record<string, unknown>): string {
  const parts = [str(d.subtype)];
  if (typeof d.num_turns === "number") parts.push(`${d.num_turns} turns`);
  if (typeof d.total_cost_usd === "number") parts.push(`$${d.total_cost_usd.toFixed(2)}`);
  if (typeof d.duration_ms === "number") parts.push(`${Math.round(d.duration_ms / 1000)}s`);
  return parts.join(" · ");
}

export function transcript(events: TaskEvent[]): Entry[] {
  const out: Entry[] = [];
  const tools = new Map<string, Extract<Entry, { kind: "tool" }>>();
  for (const { type, data: d } of events) {
    switch (type) {
      case "text":
      case "thinking":
        out.push({ kind: type, text: str(d.text) });
        break;
      case "tool_use": {
        const name = str(d.name);
        const entry: Extract<Entry, { kind: "tool" }> = {
          kind: "tool",
          id: str(d.id),
          name,
          summary: describeTool(name, d.input),
          input: d.input,
          result: null,
        };
        tools.set(entry.id, entry);
        out.push(entry);
        break;
      }
      case "tool_result": {
        const result = { content: resultText(d.content), isError: !!d.is_error };
        const call = tools.get(str(d.tool_use_id));
        if (call) call.result = result;
        else out.push({ kind: "tool", id: str(d.tool_use_id), name: "?", summary: "tool result", input: null, result });
        break;
      }
      case "verify":
        out.push({ kind: "verify", command: str(d.command), exitCode: Number(d.exit_code), output: str(d.output) });
        break;
      case "result":
        out.push({ kind: "result", summary: resultSummary(d), text: str(d.result), isError: !!d.is_error });
        break;
      case "turn":
        break; // every assistant message; the header shows the count
      case "status":
        out.push({ kind: "note", text: `status: ${str(d.status)}${d.error ? ` · ${str(d.error)}` : ""}` });
        break;
      case "session":
        out.push({ kind: "note", text: `session ${str(d.session_id)}${d.model ? ` · ${str(d.model)}` : ""}` });
        break;
      case "rate_limit":
        out.push({
          kind: "note",
          text: `rate limit ${str(d.rate_limit_type)}: ${str(d.status)}${
            typeof d.utilization === "number" ? ` · ${Math.round(d.utilization * 100)}%` : ""
          }`,
        });
        break;
      case "publish":
        out.push({ kind: "note", text: `publishing ${str(d.branch)}` });
        break;
      default:
        out.push({ kind: "note", text: `${type}: ${JSON.stringify(d)}` });
    }
  }
  return out;
}
