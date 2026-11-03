import { diffLines, diffWordsWithSpace } from "diff";

// File-editing tool calls shown as diffs. The log only has what the tool was given: an Edit's
// fragment without the lines around it, a Write's new content without what it replaced.
export interface Span {
  text: string;
  changed: boolean;
}

export interface DiffLine {
  kind: "add" | "del" | "same";
  spans: Span[];
}

export interface FileChange {
  path: string;
  note: string;
  lines: DiffLine[];
}

const FILE_TOOLS = new Set(["Edit", "MultiEdit", "Write"]);

const split = (s: string): string[] => (s.endsWith("\n") ? s.slice(0, -1) : s).split("\n");
const whole = (kind: DiffLine["kind"], text: string): DiffLine => ({ kind, spans: [{ text, changed: false }] });

// A removed line and the added line that replaced it, with the changed words marked on each.
function pair(before: string, after: string): [DiffLine, DiffLine] {
  const del: Span[] = [];
  const add: Span[] = [];
  for (const c of diffWordsWithSpace(before, after)) {
    if (!c.added) del.push({ text: c.value, changed: !!c.removed });
    if (!c.removed) add.push({ text: c.value, changed: !!c.added });
  }
  return [
    { kind: "del", spans: del },
    { kind: "add", spans: add },
  ];
}

export function lineDiff(before: string, after: string): DiffLine[] {
  const out: DiffLine[] = [];
  const chunks = diffLines(before, after);
  for (let i = 0; i < chunks.length; i++) {
    const c = chunks[i];
    const next = chunks[i + 1];
    if (c.removed && next?.added) {
      // Pair the lines one to one so the word changes show; the rest stay whole lines.
      const dels = split(c.value);
      const adds = split(next.value);
      const n = Math.min(dels.length, adds.length);
      const paired = Array.from({ length: n }, (_, k) => pair(dels[k], adds[k]));
      out.push(...paired.map(([d]) => d), ...dels.slice(n).map((t) => whole("del", t)));
      out.push(...paired.map(([, a]) => a), ...adds.slice(n).map((t) => whole("add", t)));
      i++;
    } else {
      out.push(...split(c.value).map((t) => whole(c.added ? "add" : c.removed ? "del" : "same", t)));
    }
  }
  return out;
}

export const isFileEdit = (name: string, input: unknown): boolean =>
  FILE_TOOLS.has(name) && !!input && typeof input === "object" && typeof (input as Record<string, unknown>).file_path === "string";

export function fileChanges(name: string, input: unknown): FileChange[] {
  if (!isFileEdit(name, input)) return [];
  const args = input as Record<string, unknown>;
  const path = String(args.file_path);
  const text = (v: unknown) => (typeof v === "string" ? v : "");
  if (name === "Write") {
    const content = text(args.content);
    return [{ path, note: "new content", lines: content ? split(content).map((t) => whole("add", t)) : [] }];
  }
  const edits = name === "MultiEdit" && Array.isArray(args.edits) ? (args.edits as Record<string, unknown>[]) : [args];
  return edits.map((e, k) => ({
    path,
    note: [edits.length > 1 ? `edit ${k + 1}/${edits.length}` : "", e.replace_all ? "replace all" : ""].filter(Boolean).join(" · "),
    lines: lineDiff(text(e.old_string), text(e.new_string)),
  }));
}
