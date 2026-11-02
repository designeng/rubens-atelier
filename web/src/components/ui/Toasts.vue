<script setup lang="ts">
import { toasts } from "../../toasts";
import Icon from "./Icon.vue";

const ICON = { error: "alert", success: "check", info: "message" } as const;
</script>

<template>
  <div class="toasts" role="status" aria-live="polite">
    <div v-for="t in toasts.list.value" :key="t.id" class="toast" :class="t.tone">
      <Icon :name="ICON[t.tone]" />
      <span class="toast-text">{{ t.text }}</span>
      <button type="button" class="icon-btn ghost sm" aria-label="Dismiss" @click="toasts.dismiss(t.id)">
        <Icon name="x" :size="14" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  right: var(--s4);
  bottom: var(--s4);
  z-index: 60;
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  width: min(380px, calc(100vw - 2 * var(--s4)));
  pointer-events: none;
}
.toast {
  display: flex;
  align-items: flex-start;
  gap: var(--s2);
  padding: var(--s2) var(--s2) var(--s2) var(--s3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--info);
  border-radius: var(--r-md);
  box-shadow: var(--e2);
  pointer-events: auto;
  animation: pop-in 0.18s var(--ease);
}
.toast > .icon {
  margin-top: 6px;
  color: var(--info);
}
.toast.error {
  border-left-color: var(--danger);
}
.toast.error > .icon {
  color: var(--danger);
}
.toast.success {
  border-left-color: var(--ok);
}
.toast.success > .icon {
  color: var(--ok);
}
.toast-text {
  flex: 1;
  padding: 4px 0;
  overflow-wrap: anywhere;
}
</style>
