import { ref } from "vue";

// Short-lived notices in the corner. Errors stay until dismissed, other tones fade after TOAST_MS.
export type ToastTone = "error" | "success" | "info";
export interface Toast {
  id: number;
  tone: ToastTone;
  text: string;
}

export const TOAST_MS = 4000;
export const MAX_TOASTS = 4;

export function createToasts(schedule: (fn: () => void, ms: number) => unknown = setTimeout) {
  const list = ref<Toast[]>([]);
  let next = 1;

  function dismiss(id: number): void {
    list.value = list.value.filter((t) => t.id !== id);
  }

  // The same text already showing is not repeated: a failing poll would otherwise add one every few seconds.
  function push(text: string, tone: ToastTone = "info"): void {
    if (!text || list.value.some((t) => t.text === text && t.tone === tone)) return;
    const id = next++;
    list.value = [...list.value, { id, tone, text }].slice(-MAX_TOASTS);
    if (tone !== "error") schedule(() => dismiss(id), TOAST_MS);
  }

  return { list, push, dismiss };
}

export const toasts = createToasts();
