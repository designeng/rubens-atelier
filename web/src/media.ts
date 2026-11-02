import { onMounted, onUnmounted, ref, type Ref } from "vue";

// The board switches to one column at a time below this width.
export const NARROW = "(max-width: 767px)";

/** Whether a media query matches, kept up to date while the component lives. */
export function useMedia(query: string): Ref<boolean> {
  const mql = typeof matchMedia === "function" ? matchMedia(query) : null;
  const matches = ref(!!mql?.matches);
  const update = (e: MediaQueryListEvent) => (matches.value = e.matches);
  onMounted(() => mql?.addEventListener("change", update));
  onUnmounted(() => mql?.removeEventListener("change", update));
  return matches;
}
