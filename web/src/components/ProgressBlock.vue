<script setup lang="ts">
import { computed } from "vue";
import type { Progress } from "../api/types";
import { progressSummary, turnPercent } from "../format";

const props = defineProps<{ progress: Progress }>();
// Recomputed whenever a poll brings a new progress object, which also advances the elapsed time.
const summary = computed(() => progressSummary(props.progress));
const percent = computed(() => turnPercent(props.progress));
</script>

<template>
  <div class="prog"><span class="spin"></span>{{ summary }}</div>
  <div v-if="progress.activity" class="activity" :title="progress.activity">{{ progress.activity }}</div>
  <div v-if="percent != null" class="bar" title="turns used"><i :style="{ width: `${percent}%` }"></i></div>
</template>
