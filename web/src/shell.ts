// Bash tool calls shown as a terminal line: the command with its parts told apart, and the
// call's description and options above it. A light tokenizer for reading, not a shell parser.
export type TokenKind = "cmd" | "flag" | "str" | "op" | "var" | "comment" | "text";

export interface Token {
  text: string;
  kind: TokenKind;
}

export interface ShellCall {
  command: string;
  description: string;
  options: string[];
}

// Operators after which the next word is a command; redirections are operators that are not.
const CHAINS = ["&&", "||", "$(", "|", ";", "&", "("];
// Longest first, so ">&" and "&>" are not read as "&".
const OPS = ["&&", "||", "$(", ">>", ">&", "&>", "|", ";", "&", "(", ">", "<", ")"];
const WORD_END = /[\s'"`|&;()<>]/;

export function shellTokens(command: string): Token[] {
  const out: Token[] = [];
  const push = (text: string, kind: TokenKind) => {
    const last = out[out.length - 1];
    if (last?.kind === kind && kind === "text") last.text += text;
    else out.push({ text, kind });
  };
  let expectCmd = true;
  let i = 0;
  while (i < command.length) {
    const c = command[i];
    const rest = command.slice(i);
    if (/\s/.test(c)) {
      const ws = /^\s+/.exec(rest)![0];
      // A new line starts a new command unless the previous one ends with a continuation.
      if (ws.includes("\n") && command[i - 1] !== "\\") expectCmd = true;
      push(ws, "text");
      i += ws.length;
    } else if (c === "#" && (i === 0 || /\s/.test(command[i - 1]))) {
      const end = command.indexOf("\n", i);
      const text = end < 0 ? rest : command.slice(i, end);
      push(text, "comment");
      i += text.length;
    } else if (c === "'" || c === '"' || c === "`") {
      let j = i + 1;
      while (j < command.length && command[j] !== c) j += c !== "'" && command[j] === "\\" ? 2 : 1;
      const text = command.slice(i, j + 1);
      push(text, "str");
      i += text.length;
      expectCmd = false;
    } else if (OPS.some((op) => rest.startsWith(op))) {
      const op = OPS.find((o) => rest.startsWith(o))!;
      push(op, "op");
      i += op.length;
      if (CHAINS.includes(op)) expectCmd = true;
    } else if (c === "$") {
      const v = /^\$(\{[^}]*\}|\w+|.)/.exec(rest)![0];
      push(v, "var");
      i += v.length;
      expectCmd = false;
    } else {
      let j = i + 1;
      while (j < command.length && !WORD_END.test(command[j]) && command[j] !== "$") j++;
      const word = command.slice(i, j);
      // VAR=value before a command keeps the command position open.
      if (expectCmd && /^\w+=/.test(word)) push(word, "var");
      else if (expectCmd) {
        push(word, "cmd");
        expectCmd = false;
      } else push(word, word.startsWith("-") ? "flag" : "text");
      i = j;
    }
  }
  return out;
}

export const isShell = (name: string, input: unknown): boolean =>
  name === "Bash" && !!input && typeof input === "object" && typeof (input as Record<string, unknown>).command === "string";

function duration(ms: number): string {
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : s % 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s / 60}m`;
}

export function shellCall(input: unknown): ShellCall {
  const args = (input ?? {}) as Record<string, unknown>;
  const options: string[] = [];
  if (args.run_in_background) options.push("background");
  if (typeof args.timeout === "number") options.push(`timeout ${duration(args.timeout)}`);
  if (args.dangerouslyDisableSandbox) options.push("no sandbox");
  return {
    command: typeof args.command === "string" ? args.command : "",
    description: typeof args.description === "string" ? args.description : "",
    options,
  };
}
