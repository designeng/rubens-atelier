<script setup lang="ts">
import { computed } from "vue";
import type { ItemOut } from "../api/types";
import { useBoardStore } from "../composables/useBoard";
import { attentionBadge, canRunNow } from "../format";
import ProgressBlock from "./ProgressBlock.vue";

const props = defineProps<{ item: ItemOut }>();
const b = useBoardStore();
const badge = computed(() => (props.item.attention ? attentionBadge(props.item.attention) : null));
</script>

<template>
  <div class="card" draggable="true">
    <div>
      <span v-if="badge" class="badge" :class="badge.tone" :title="item.attention?.text || undefined">{{ badge.label }}</span>
      {{ item.title }}
    </div>
    <div class="meta">{{ item.kind }} · {{ item.state || "backlog" }}{{ item.pr_url ? " · PR" : "" }}{{ item.issue ? ` · #${item.issue}` : "" }}</div>
    <ProgressBlock v-if="item.progress" :progress="item.progress" />
    <button
      v-if="item.progress && canRunNow(item.progress)"
      class="run-now"
      title="Start this step now, past the pause and the agent limits"
      @click.stop="b.runItemNow(item.id)"
    >
      {{ b.isPaused() ? "Resume" : "Run now" }}
    </button>
  </div>
</template>
