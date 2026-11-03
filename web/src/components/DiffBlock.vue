<script setup lang="ts">
import { computed } from "vue";
import { fileChanges } from "../diff";

// A file-editing tool call as a unified diff, one block per edit.
const props = defineProps<{ name: string; input: unknown }>();
const changes = computed(() => fileChanges(props.name, props.input));
const SIGN = { add: "+", del: "-", same: " " } as const;
</script>

<template>
  <div v-for="(c, i) in changes" :key="i" class="diff">
    <div class="d-head">
      <span class="d-path" :title="c.path">{{ c.path }}</span>
      <span v-if="c.note" class="d-note">{{ c.note }}</span>
    </div>
    <div class="d-body">
      <div v-for="(l, j) in c.lines" :key="j" class="d-line" :class="l.kind">
        <span class="d-sign">{{ SIGN[l.kind] }}</span
        ><span class="d-text"><span v-for="(s, k) in l.spans" :key="k" :class="{ hl: s.changed }">{{ s.text }}</span></span>
      </div>
      <div v-if="!c.lines.length" class="d-line same"><span class="d-sign"> </span><span class="d-text">(empty)</span></div>
    </div>
  </div>
</template>

<style scoped>
.diff {
  margin: 4px 0 6px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  overflow: hidden;
}
.d-head {
  display: flex;
  gap: var(--s2);
  padding: 3px var(--s2);
  border-bottom: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-muted);
}
.d-path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.d-note {
  flex: none;
  margin-left: auto;
  color: var(--text-subtle);
}
.d-body {
  overflow-x: auto;
  padding: 2px 0;
}
.d-line {
  display: flex;
  min-width: max-content;
  white-space: pre;
}
.d-sign {
  flex: none;
  width: 2ch;
  text-align: center;
  color: var(--text-subtle);
  user-select: none;
}
.d-text {
  padding-right: var(--s2);
}
.d-line.add {
  background: var(--ok-subtle);
}
.d-line.del {
  background: var(--danger-subtle);
}
.d-line.add .d-sign {
  color: var(--ok);
}
.d-line.del .d-sign {
  color: var(--danger);
}
.d-line.add .hl {
  background: color-mix(in srgb, var(--ok) 28%, transparent);
  border-radius: 2px;
}
.d-line.del .hl {
  background: color-mix(in srgb, var(--danger) 28%, transparent);
  border-radius: 2px;
}
</style>
