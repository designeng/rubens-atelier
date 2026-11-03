import { describe, expect, it } from "vitest";
import { fileChanges, isFileEdit, lineDiff } from "../diff";

const plain = (lines: ReturnType<typeof lineDiff>) =>
  lines.map((l) => `${{ add: "+", del: "-", same: " " }[l.kind]}${l.spans.map((s) => (s.changed ? `[${s.text}]` : s.text)).join("")}`);

describe("lineDiff", () => {
  it("keeps unchanged lines and marks the changed words of a replaced line", () => {
    expect(plain(lineDiff("a\nprice 1\nz\n", "a\nprice 2\nz\n"))).toEqual([" a", "-price [1]", "+price [2]", " z"]);
  });

  it("shows pure additions and leftover lines whole", () => {
    expect(plain(lineDiff("import a;\n", "import a;\nimport b;\n"))).toEqual([" import a;", "+import b;"]);
    expect(plain(lineDiff("x\n", "y\nw\n"))).toEqual(["-[x]", "+[y]", "+w"]);
  });
});

describe("fileChanges", () => {
  it("only takes file-editing calls", () => {
    expect(isFileEdit("Bash", { command: "ls" })).toBe(false);
    expect(isFileEdit("Edit", null)).toBe(false);
    expect(fileChanges("Read", { file_path: "a" })).toEqual([]);
  });

  it("shows an Edit as its fragment diff", () => {
    const [c] = fileChanges("Edit", { file_path: "/w/a.vue", old_string: "a\n", new_string: "a\nb\n", replace_all: true });
    expect(c.path).toBe("/w/a.vue");
    expect(c.note).toBe("replace all");
    expect(plain(c.lines)).toEqual([" a", "+b"]);
  });

  it("shows a Write as all added lines and a MultiEdit as one block per edit", () => {
    expect(plain(fileChanges("Write", { file_path: "f", content: "x\ny\n" })[0].lines)).toEqual(["+x", "+y"]);
    const multi = fileChanges("MultiEdit", {
      file_path: "f",
      edits: [
        { old_string: "a", new_string: "b" },
        { old_string: "c", new_string: "d" },
      ],
    });
    expect(multi.map((c) => c.note)).toEqual(["edit 1/2", "edit 2/2"]);
  });
});
