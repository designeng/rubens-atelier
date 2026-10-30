import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExecResult } from "../api/types";
import { execSummary, pollExec } from "../terminal";

const res = (status: ExecResult["status"], exit_code: number | null = null): ExecResult => ({
  id: "e1",
  command: "ls",
  status,
  exit_code,
  output: "",
});

describe("pollExec", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("polls while running and stops on done", async () => {
    const fetchResult = vi
      .fn<() => Promise<ExecResult>>()
      .mockResolvedValueOnce(res("running"))
      .mockResolvedValueOnce(res("running"))
      .mockResolvedValueOnce(res("done", 0));
    const seen: string[] = [];
    const p = pollExec(fetchResult, (r) => seen.push(r.status), () => false, 1000);
    await vi.advanceTimersByTimeAsync(2000);
    await expect(p).resolves.toMatchObject({ status: "done", exit_code: 0 });
    expect(seen).toEqual(["running", "running", "done"]);
    expect(fetchResult).toHaveBeenCalledTimes(3);
  });

  it("stops on timeout", async () => {
    const fetchResult = vi.fn().mockResolvedValue(res("timeout"));
    await expect(pollExec(fetchResult, () => {}, () => false)).resolves.toMatchObject({ status: "timeout" });
    expect(fetchResult).toHaveBeenCalledTimes(1);
  });

  it("stops without reporting once abandoned", async () => {
    let stop = false;
    const fetchResult = vi.fn().mockImplementation(async () => {
      stop = true;
      return res("running");
    });
    const onUpdate = vi.fn();
    await expect(pollExec(fetchResult, onUpdate, () => stop)).resolves.toBeNull();
    expect(onUpdate).not.toHaveBeenCalled();
    expect(fetchResult).toHaveBeenCalledTimes(1);
  });

  it("lets a failed fetch (a 404) end the poll", async () => {
    const fetchResult = vi.fn().mockRejectedValue(new Error("command not found"));
    await expect(pollExec(fetchResult, () => {}, () => false)).rejects.toThrow("command not found");
  });
});

describe("execSummary", () => {
  it("shows the exit code, or a timeout", () => {
    expect(execSummary(res("done", 2))).toBe("exit 2");
    expect(execSummary(res("timeout"))).toBe("timed out");
  });
});
