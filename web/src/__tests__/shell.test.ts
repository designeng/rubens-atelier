import { describe, expect, it } from "vitest";
import { isShell, shellCall, shellTokens } from "../shell";

// Non-blank tokens as "kind:text".
const toks = (cmd: string) =>
  shellTokens(cmd)
    .filter((t) => t.text.trim())
    .map((t) => `${t.kind}:${t.text.trim()}`);

describe("shellTokens", () => {
  it("tells commands, flags, strings and operators apart", () => {
    expect(toks(`cd web && pnpm test -- --run "a b" | tail -5`)).toEqual([
      "cmd:cd", "text:web", "op:&&", "cmd:pnpm", "text:test", "flag:--", "flag:--run", "str:\"a b\"", "op:|", "cmd:tail", "flag:-5",
    ]);
  });

  it("knows redirections, variables, substitutions and comments", () => {
    expect(toks(`X=1 make 2>&1 > out.log`)).toEqual(["var:X=1", "cmd:make", "text:2", "op:>&", "text:1", "op:>", "text:out.log"]);
    expect(toks(`make &> log & wait`)).toEqual(["cmd:make", "op:&>", "text:log", "op:&", "cmd:wait"]);
    expect(toks(`echo $HOME "\\"q\\"" $(git rev-parse HEAD)`)).toEqual([
      "cmd:echo", "var:$HOME", 'str:"\\"q\\""', "op:$(", "cmd:git", "text:rev-parse HEAD", "op:)",
    ]);
    expect(toks("ls # list\nwc -l\\\n  file")).toEqual(["cmd:ls", "comment:# list", "cmd:wc", "flag:-l\\", "text:file"]);
  });

  it("keeps the command text intact", () => {
    const cmd = `git log --format='%s' -3 && echo "${"$"}{A}" ; ls`;
    expect(shellTokens(cmd).map((t) => t.text).join("")).toBe(cmd);
  });
});

describe("shellCall", () => {
  it("takes the command, description and options of a Bash call", () => {
    expect(isShell("Bash", { command: "ls" })).toBe(true);
    expect(isShell("Read", { command: "ls" })).toBe(false);
    expect(shellCall({ command: "make", description: "Build", run_in_background: true, timeout: 90000 })).toEqual({
      command: "make",
      description: "Build",
      options: ["background", "timeout 1m 30s"],
    });
    expect(shellCall({ command: "x", timeout: 600000 }).options).toEqual(["timeout 10m"]);
  });
});
