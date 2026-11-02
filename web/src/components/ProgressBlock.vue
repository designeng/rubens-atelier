<script setup lang="ts">
import { computed } from "vue";
import type { Progress } from "../api/types";
import { isRunning, progressSummary, turnPercent } from "../format";

const props = defineProps<{ progress: Progress; paused?: boolean }>();
// Recomputed whenever a poll brings a new progress object, which also advances the elapsed time.
const summary = computed(() => progressSummary(props.progress, Date.now(), props.paused));
const percent = computed(() => turnPercent(props.progress));
const running = computed(() => isRunning(props.progress));
</script>

<template>
  <div class="prog" :class="{ running }">
    <div class="prog-line"><span v-if="running" class="spin"></span>{{ summary }}</div>
    <div v-if="progress.activity" class="activity" :title="progress.activity">{{ progress.activity }}</div>
    <div
      v-if="percent != null"
      class="bar"
      role="progressbar"
      aria-label="Turns used"
      :aria-valuenow="percent"
      aria-valuemin="0"
      aria-valuemax="100"
      title="turns used"
    >
      <i :style="{ width: `${percent}%` }"></i>
    </div>
  </div>
</template>

<style scoped>
.prog {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--s2);
  margin-top: 2px;
  background: var(--surface-2);
  border-radius: var(--r-sm);
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.prog.running {
  background: var(--accent-subtle);
  color: var(--text);
}
.prog-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-variant-numeric: tabular-nums;
}
.activity {
  font: var(--fs-xs) / 1.4 var(--font-mono);
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.bar {
  height: 3px;
  background: var(--border);
  border-radius: 2px;
  overflow: hidden;
}
.bar i {
  display: block;
  height: 100%;
  background: var(--accent);
  transition: width 0.3s var(--ease);
}
</style>
