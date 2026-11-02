import { describe, expect, it } from "vitest";
import { createToasts, MAX_TOASTS, TOAST_MS } from "../toasts";

describe("toasts", () => {
  it("keeps errors until dismissed and schedules the rest to fade", () => {
    const timers: [() => void, number][] = [];
    const t = createToasts((fn, ms) => timers.push([fn, ms]));
    t.push("boom", "error");
    t.push("saved", "success");
    expect(t.list.value.map((x) => x.text)).toEqual(["boom", "saved"]);
    expect(timers).toHaveLength(1);
    expect(timers[0][1]).toBe(TOAST_MS);
    timers[0][0]();
    expect(t.list.value.map((x) => x.text)).toEqual(["boom"]);
    t.dismiss(t.list.value[0].id);
    expect(t.list.value).toEqual([]);
  });

  it("does not repeat a toast that is already showing", () => {
    const t = createToasts(() => {});
    t.push("Failed to fetch", "error");
    t.push("Failed to fetch", "error");
    expect(t.list.value).toHaveLength(1);
  });

  it("keeps only the newest few and ignores empty text", () => {
    const t = createToasts(() => {});
    t.push("");
    for (let n = 0; n < MAX_TOASTS + 2; n++) t.push(`e${n}`, "error");
    expect(t.list.value.map((x) => x.text)).toEqual(["e2", "e3", "e4", "e5"]);
  });
});
