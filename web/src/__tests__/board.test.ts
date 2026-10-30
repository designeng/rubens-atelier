import { describe, expect, it } from "vitest";
import { canShowMoreDone, DONE_PAGE, MAX_DONE } from "../composables/useBoard";

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
