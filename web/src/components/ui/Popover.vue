<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from "vue";

// A button that opens a floating panel. With `menu`, the panel is a role=menu whose [role^=menuitem]
// children are reached with the arrow keys; otherwise it is a plain disclosure (e.g. a form).
// Esc and a click outside close it, and focus goes back to the button.
const props = withDefaults(
  defineProps<{ label: string; menu?: boolean; align?: "start" | "end"; buttonClass?: string; title?: string }>(),
  { menu: false, align: "start", buttonClass: "btn", title: undefined },
);

const open = ref(false);
const root = ref<HTMLElement>();
const button = ref<HTMLButtonElement>();
const panel = ref<HTMLElement>();

const items = () => [...(panel.value?.querySelectorAll<HTMLElement>("[role^=menuitem]:not(:disabled)") ?? [])];

async function show(focusLast = false): Promise<void> {
  open.value = true;
  document.addEventListener("pointerdown", onOutside, true);
  await nextTick();
  clamp();
  if (props.menu) {
    const list = items();
    (focusLast ? list.at(-1) : list[0])?.focus();
  } else {
    panel.value?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();
  }
}
// Keep the panel inside the viewport on narrow screens, whichever edge it is aligned to.
function clamp(): void {
  const el = panel.value;
  if (!el) return;
  const r = el.getBoundingClientRect();
  const gap = 8;
  const shift = r.left < gap ? gap - r.left : r.right > innerWidth - gap ? innerWidth - gap - r.right : 0;
  el.style.translate = shift ? `${shift}px 0` : "";
}
function close(refocus = true): void {
  if (!open.value) return;
  open.value = false;
  document.removeEventListener("pointerdown", onOutside, true);
  if (refocus) button.value?.focus();
}
const toggle = () => (open.value ? close() : show());
function onOutside(e: PointerEvent): void {
  if (!root.value?.contains(e.target as Node)) close(false);
}
function onButtonKey(e: KeyboardEvent): void {
  if (props.menu && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault();
    show(e.key === "ArrowUp");
  }
}
function onPanelKey(e: KeyboardEvent): void {
  if (e.key === "Escape") {
    e.stopPropagation();
    close();
    return;
  }
  if (e.key === "Tab" && props.menu) {
    close(false);
    return;
  }
  if (!props.menu || !["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
  e.preventDefault();
  const list = items();
  const i = list.indexOf(document.activeElement as HTMLElement);
  const next =
    e.key === "Home" ? 0 : e.key === "End" ? list.length - 1 : (i + (e.key === "ArrowDown" ? 1 : -1) + list.length) % list.length;
  list[next]?.focus();
}
// A chosen menu item closes the menu; panels with forms stay open until Esc or a click outside.
function onPanelClick(e: MouseEvent): void {
  if (props.menu && (e.target as Element).closest("[role^=menuitem]")) close();
}
onBeforeUnmount(() => document.removeEventListener("pointerdown", onOutside, true));

defineExpose({ close });
</script>

<template>
  <div ref="root" class="pop">
    <button
      ref="button"
      type="button"
      :class="buttonClass"
      :aria-label="label"
      :title="title ?? label"
      :aria-haspopup="menu ? 'menu' : 'dialog'"
      :aria-expanded="open"
      @click="toggle"
      @keydown="onButtonKey"
    >
      <slot name="button" :open="open" />
    </button>
    <div
      v-if="open"
      ref="panel"
      class="pop-panel"
      :class="align"
      :role="menu ? 'menu' : 'dialog'"
      :aria-label="label"
      @keydown="onPanelKey"
      @click="onPanelClick"
    >
      <slot :close="close" />
    </div>
  </div>
</template>
