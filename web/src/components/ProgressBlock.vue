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
  <div class="prog"><span v-if="running" class="spin"></span>{{ summary }}</div>
  <div v-if="progress.activity" class="activity" :title="progress.activity">{{ progress.activity }}</div>
  <div v-if="percent != null" class="bar" title="turns used"><i :style="{ width: `${percent}%` }"></i></div>
</template>
