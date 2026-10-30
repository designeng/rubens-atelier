<script setup lang="ts">
import { computed } from "vue";
import type { ItemOut } from "../api/types";
import { useBoardStore } from "../composables/useBoard";
import { attentionBadge, canRunNow, overBudget, spendText } from "../format";
import ProgressBlock from "./ProgressBlock.vue";

const props = defineProps<{ item: ItemOut }>();
const b = useBoardStore();
const badge = computed(() => (props.item.attention ? attentionBadge(props.item.attention) : null));
const money = computed(() => spendText(props.item.spent_usd, props.item.budget_usd));
</script>

<template>
  <div class="card" draggable="true">
    <div>
      <span v-if="badge" class="badge" :class="badge.tone" :title="item.attention?.text || undefined">{{ badge.label }}</span>
      <span v-if="item.blocked.length" class="badge muted" :title="`Blocked by ${item.blocked.join(', ')}`">BLOCKED</span>
      {{ item.title }}
    </div>
    <div class="meta">{{ item.kind }} · {{ item.state || "backlog" }}{{ item.pr_url ? " · PR" : "" }}{{ item.issue ? ` · #${item.issue}` : "" }}</div>
    <div v-if="money" class="meta" :class="{ err: overBudget(item.spent_usd, item.budget_usd) }" title="Spent / budget, children included">
      {{ money }}
    </div>
    <ProgressBlock v-if="item.progress" :progress="item.progress" :paused="b.isPaused()" />
    <button
      v-if="item.progress && canRunNow(item.progress)"
      class="run-now"
      :title="
        b.isPaused()
          ? 'Start this step now, past the paused queue. Only this step: the story\'s next steps queue again until you resume the queue in the header.'
          : 'Start this step now, past the agent limits'
      "
      @click.stop="b.runItemNow(item.id)"
    >
      Run this step now
    </button>
  </div>
</template>
