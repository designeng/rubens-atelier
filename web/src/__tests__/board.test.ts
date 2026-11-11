import { describe, expect, it } from "vitest";
import { boardHash, canShowMoreDone, DONE_PAGE, itemHash, MAX_DONE, parseHash } from "../composables/useBoard";

describe("canShowMoreDone", () => {
  it("offers more while some done items are hidden", () => {
    expect(canShowMoreDone(120, DONE_PAGE, DONE_PAGE)).toBe(true);
  });

  it("does not once everything is shown", () => {
    expect(canShowMoreDone(30, 30, DONE_PAGE)).toBe(false);
  });

  it("does not once the server maximum is reached, however many are left", () => {
    expect(canShowMoreDone(MAX_DONE + 100, MAX_DONE, MAX_DONE)).toBe(false);
  });
});

describe("item hash", () => {
  it("reads the open story and its tab", () => {
    expect(parseHash("#item=abc&tab=runs")).toEqual({ project: null, item: "abc", tab: "runs" });
  });

  it("defaults to the overview, also for unknown tabs and old links", () => {
    expect(parseHash("#item=abc")).toEqual({ project: null, item: "abc", tab: "overview" });
    expect(parseHash("#item=abc&tab=nope")).toEqual({ project: null, item: "abc", tab: "overview" });
    expect(parseHash("")).toEqual({ project: null, item: null, tab: "overview" });
  });

  it("round-trips ids that need escaping and leaves the overview tab out", () => {
    expect(itemHash("a b/c")).toBe("#item=a%20b%2Fc");
    expect(parseHash(itemHash("a b/c", "workspace"))).toEqual({ project: null, item: "a b/c", tab: "workspace" });
  });

  it("carries the project in the link", () => {
    expect(parseHash("#project=foo&item=abc&tab=runs")).toEqual({ project: "foo", item: "abc", tab: "runs" });
    expect(boardHash({ project: "foo" })).toBe("#project=foo");
    expect(boardHash({ project: "foo", item: "abc", tab: "runs" })).toBe("#project=foo&item=abc&tab=runs");
    expect(itemHash("abc", "overview", "foo")).toBe("#project=foo&item=abc");
    expect(boardHash({})).toBe("");
  });

  it("round-trips project slugs that need escaping", () => {
    const hash = boardHash({ project: "a b/c", item: "x" });
    expect(parseHash(hash)).toEqual({ project: "a b/c", item: "x", tab: "overview" });
  });
});
