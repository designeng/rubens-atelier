<script setup lang="ts">
import type { RunOut } from "../../api/types";
import { formatUsd, runUsage } from "../../format";
import Icon from "../ui/Icon.vue";

// Each agent step of the story, with a way into its session.
// `reruns` says what sent the story back to a step that ran again, e.g. "implement ← sync_base: conflict".
defineProps<{ runs: RunOut[]; reruns: Map<string, string>; sessionTask: string | null }>();
const emit = defineEmits<{ "toggle-session": [taskId: string] }>();
</script>

<template>
  <section class="section">
    <div class="section-title">Runs</div>
    <ol v-if="runs.length" class="runs">
      <li v-for="r in runs" :key="r.id" class="run" :class="{ current: sessionTask === r.task_id }">
        <div class="run-main">
          <span class="run-state">{{ r.state }}</span>
          <span class="chip" :class="r.status === 'succeeded' ? 'ok' : r.status === 'failed' ? 'bad' : r.status === 'running' ? 'accent' : ''">{{
            r.status
          }}</span>
          <span class="muted">{{ r.role }}</span>
          <span v-if="r.verdict" class="muted">→ {{ r.verdict }}</span>
          <span class="spacer"></span>
          <span v-if="r.cost_usd != null" class="cost">{{ formatUsd(r.cost_usd) }}</span>
          <button
            type="button"
            class="btn sm"
            :class="{ ghost: sessionTask !== r.task_id }"
            :aria-pressed="sessionTask === r.task_id"
            :title="`Session of task ${r.task_id.slice(0, 8)}`"
            @click="emit('toggle-session', r.task_id)"
          >
            <Icon name="terminal" :size="14" /> {{ sessionTask === r.task_id ? "Hide" : "Session" }}
          </button>
        </div>
        <div v-if="reruns.get(r.id)" class="run-why subtle">ran again after {{ reruns.get(r.id) }}</div>
        <div v-if="runUsage(r)" class="run-usage subtle" title="Tokens: context of the last request, cache reads and writes, uncached input, output">
          {{ runUsage(r) }}
        </div>
        <div v-if="r.error" class="run-error">{{ r.error }}</div>
      </li>
    </ol>
    <p v-else class="subtle">No agent has run yet.</p>
  </section>
</template>

<style scoped>
.runs {
  list-style: none;
  margin: 0;
  padding: 0;
}
.run {
  padding: var(--s2) var(--s2) var(--s2) var(--s3);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  margin-bottom: var(--s2);
}
.run.current {
  border-color: var(--accent);
  background: var(--accent-subtle);
}
.run-main {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s2);
  font-size: var(--fs-md);
}
.run-state {
  font-weight: 600;
}
.cost {
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
}
.run-why {
  font-size: var(--fs-sm);
  margin-top: 2px;
}
.run-usage {
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
  margin-top: 2px;
}
.run-error {
  margin-top: var(--s1);
  color: var(--danger);
  font: var(--fs-sm) / 1.45 var(--font-mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
