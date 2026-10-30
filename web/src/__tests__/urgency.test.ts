import { describe, expect, it } from "vitest";
import { DEFAULT_URGENCY, URGENCY_LEVELS, urgencyLabel } from "../urgency";

describe("urgency", () => {
  it("defaults to Normal and orders levels from least to most urgent", () => {
    expect(urgencyLabel(DEFAULT_URGENCY)).toBe("Normal");
    const values = URGENCY_LEVELS.map((l) => l.value);
    expect(values).toEqual([...values].sort((a, b) => a - b));
    expect(values.every((v) => v >= -100 && v <= 100)).toBe(true);
  });

  it("labels known levels and falls back to the number", () => {
    expect(urgencyLabel(50)).toBe("Urgent");
    expect(urgencyLabel(-10)).toBe("Low");
    expect(urgencyLabel(7)).toBe("priority 7");
  });
});
