import { storage } from "./api/client";

// "auto" follows the OS (prefers-color-scheme); day and midnight pin a palette via <html data-theme>.
export type Theme = "auto" | "day" | "midnight";

export const THEMES: { value: Theme; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "day", label: "Day" },
  { value: "midnight", label: "Midnight" },
];

type Root = { dataset: DOMStringMap };

export function parseTheme(raw: string): Theme {
  return raw === "day" || raw === "midnight" ? raw : "auto";
}

export function applyTheme(theme: Theme, root: Root = document.documentElement): void {
  if (theme === "auto") delete root.dataset.theme;
  else root.dataset.theme = theme;
}

export function loadTheme(): Theme {
  return parseTheme(storage.get("theme"));
}

export function saveTheme(theme: Theme, root: Root = document.documentElement): void {
  storage.set("theme", theme);
  applyTheme(theme, root);
}
