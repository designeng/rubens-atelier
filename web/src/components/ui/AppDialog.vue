<script setup lang="ts">
import { ref } from "vue";
import Icon from "./Icon.vue";

// The shell every dialog shares: native <dialog> (focus trap, Esc) with a titled header, a scrolling body
// and a footer for the actions. Full screen below 600 px.
withDefaults(defineProps<{ title: string; size?: "sm" | "md" | "lg" }>(), { size: "sm" });
const emit = defineEmits<{ close: [] }>();
const dlg = ref<HTMLDialogElement>();

defineExpose({
  showModal: () => dlg.value?.showModal(),
  close: () => dlg.value?.close(),
});
</script>

<template>
  <dialog ref="dlg" class="dlg" :class="size" :aria-label="title" @close="emit('close')">
    <div class="dlg-head">
      <h2>{{ title }}</h2>
      <button type="button" class="icon-btn ghost" aria-label="Close" title="Close (Esc)" @click="dlg?.close()">
        <Icon name="x" />
      </button>
    </div>
    <div class="dlg-body"><slot /></div>
    <div v-if="$slots.footer" class="dlg-foot"><slot name="footer" /></div>
  </dialog>
</template>
