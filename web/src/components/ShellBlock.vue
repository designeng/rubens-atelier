<script setup lang="ts">
import { computed } from "vue";
import { shellCall, shellTokens } from "../shell";

// A Bash tool call as a terminal line, with what the agent said it does above it.
const props = defineProps<{ input: unknown }>();
const call = computed(() => shellCall(props.input));
const tokens = computed(() => shellTokens(call.value.command));
</script>

<template>
  <div class="sh">
    <div v-if="call.description || call.options.length" class="sh-head">
      <span class="sh-desc">{{ call.description }}</span>
      <span v-for="o in call.options" :key="o" class="sh-opt">{{ o }}</span>
    </div>
    <pre class="sh-cmd"><span class="sh-prompt">$ </span><span v-for="(t, i) in tokens" :key="i" :class="`t-${t.kind}`">{{ t.text }}</span></pre>
  </div>
</template>

<style scoped>
.sh {
  margin: 4px 0 6px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  overflow: hidden;
}
.sh-head {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s1) var(--s2);
  padding: 3px var(--s2);
  border-bottom: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-muted);
  font-family: var(--font-ui);
}
.sh-desc {
  margin-right: auto;
}
.sh-opt {
  padding: 0 6px;
  border-radius: var(--r-pill);
  background: var(--warn-subtle);
  color: var(--warn);
  font-size: var(--fs-xs);
}
.sh-cmd {
  margin: 0;
  border: 0;
  border-radius: 0;
  background: none;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.sh-prompt {
  color: var(--text-subtle);
  user-select: none;
}
.t-cmd {
  color: var(--accent);
  font-weight: 600;
}
.t-flag {
  color: var(--info);
}
.t-str {
  color: var(--ok);
}
.t-op {
  color: var(--warn);
}
.t-var {
  color: var(--brand);
}
.t-comment {
  color: var(--text-subtle);
  font-style: italic;
}
</style>
