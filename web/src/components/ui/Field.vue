<script setup lang="ts">
// A labelled form control with an optional hint under it. The control in the slot takes `id` = `for`
// and `aria-describedby` = `${for}-hint` to be tied to the label and hint.
defineProps<{ label: string; for: string; hint?: string }>();
</script>

<template>
  <div class="field">
    <label :for="$props.for">{{ label }}</label>
    <slot />
    <div v-if="hint" :id="`${$props.for}-hint`" class="hint">{{ hint }}</div>
  </div>
</template>
