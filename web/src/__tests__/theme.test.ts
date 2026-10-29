import { afterEach, describe, expect, it, vi } from "vitest";
import { applyTheme, loadTheme, parseTheme, saveTheme } from "../theme";

afterEach(() => vi.unstubAllGlobals());

function fakeStorage(initial: Record<string, string> = {}) {
  const data = { ...initial };
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => data[k] ?? null,
    setItem: (k: string, v: string) => void (data[k] = v),
  });
  return data;
}

describe("parseTheme", () => {
  it("accepts the two palettes and falls back to auto", () => {
    expect(parseTheme("day")).toBe("day");
    expect(parseTheme("midnight")).toBe("midnight");
    expect(parseTheme("")).toBe("auto");
    expect(parseTheme("dark")).toBe("auto");
  });
});

describe("applyTheme", () => {
  it("sets data-theme for day and midnight and removes it for auto", () => {
    const root = { dataset: {} as DOMStringMap };
    applyTheme("day", root);
    expect(root.dataset.theme).toBe("day");
    applyTheme("midnight", root);
    expect(root.dataset.theme).toBe("midnight");
    applyTheme("auto", root);
    expect("theme" in root.dataset).toBe(false);
  });
});

describe("saveTheme / loadTheme", () => {
  it("round-trips through localStorage and applies the theme", () => {
    const data = fakeStorage();
    const root = { dataset: {} as DOMStringMap };
    expect(loadTheme()).toBe("auto");
    saveTheme("midnight", root);
    expect(data.theme).toBe("midnight");
    expect(root.dataset.theme).toBe("midnight");
    expect(loadTheme()).toBe("midnight");
  });

  it("ignores a stored value it does not know", () => {
    fakeStorage({ theme: "solarized" });
    expect(loadTheme()).toBe("auto");
  });

  it("survives storage that throws", () => {
    const boom = () => {
      throw new Error("denied");
    };
    vi.stubGlobal("localStorage", { getItem: boom, setItem: boom });
    const root = { dataset: {} as DOMStringMap };
    expect(loadTheme()).toBe("auto");
    expect(() => saveTheme("day", root)).not.toThrow();
    expect(root.dataset.theme).toBe("day");
  });
});
