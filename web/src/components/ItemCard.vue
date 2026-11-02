<script setup lang="ts">
import { computed } from "vue";
import type { ItemOut } from "../api/types";
import { useBoardStore } from "../composables/useBoard";
import { attentionBadge, canRunNow, overBudget, spendText } from "../format";
import { urgencyLabel } from "../urgency";
import ProgressBlock from "./ProgressBlock.vue";
import Icon from "./ui/Icon.vue";

const props = defineProps<{ item: ItemOut }>();
const emit = defineEmits<{ open: [] }>();
const b = useBoardStore();
const badge = computed(() => (props.item.attention ? attentionBadge(props.item.attention) : null));
const money = computed(() => spendText(props.item.spent_usd, props.item.budget_usd));
// The column already says backlog, needs you, done: the state is shown only when it adds something.
const COLUMN_STATES = new Set(["needs_human", "done", "failed", "cancelled"]);
const state = computed(() => {
  const s = props.item.state;
  if (!s || COLUMN_STATES.has(s) || props.item.progress) return null;
  return s;
});
const urgency = computed(() => (props.item.priority > 0 ? urgencyLabel(props.item.priority) : null));
</script>

<template>
  <article class="card" :class="badge?.tone ? `tone-${badge.tone}` : undefined">
    <div v-if="badge || item.blocked.length || item.ready || urgency" class="chips">
      <span v-if="badge" class="chip" :class="badge.tone" :title="item.attention?.text || undefined">{{ badge.label }}</span>
      <span v-if="item.blocked.length" class="chip" :title="`Blocked by ${item.blocked.join(', ')}`">Blocked</span>
      <span v-if="item.ready" class="chip ok" title="All blockers succeeded; ready to start">Ready</span>
      <span v-if="urgency" class="chip warn">{{ urgency }}</span>
    </div>
    <h3 class="title">
      <button type="button" class="open" @click="emit('open')">{{ item.title }}</button>
    </h3>
    <div class="meta">
      <span v-if="item.kind !== 'story'">{{ item.kind }}</span>
      <span v-if="state" class="mono">{{ state }}</span>
      <span v-if="item.issue">#{{ item.issue }}</span>
      <span v-if="money" :class="{ err: overBudget(item.spent_usd, item.budget_usd) }" title="Spent / budget, children included">{{
        money
      }}</span>
      <a
        v-if="item.pr_url"
        class="pr"
        :href="item.pr_url"
        target="_blank"
        rel="noopener"
        title="Open the pull request on GitHub"
        @click.stop
      >
        <Icon name="pr" :size="13" /> PR
      </a>
    </div>
    <ProgressBlock v-if="item.progress" :progress="item.progress" :paused="b.isPaused()" />
    <button
      v-if="item.progress && canRunNow(item.progress)"
      type="button"
      class="btn sm run-now"
      :title="
        b.isPaused()
          ? 'Start this step now, past the paused queue. Only this step: the story\'s next steps queue again until you resume the queue.'
          : 'Start this step now, past the agent limits'
      "
      @click.stop="b.runItemNow(item.id)"
    >
      <Icon name="zap" :size="14" /> Run this step now
    </button>
  </article>
</template>

<style scoped>
.card {
  position: relative;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--s3);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  box-shadow: var(--e1);
  cursor: pointer;
  transition: border-color 0.12s, box-shadow 0.12s, transform 0.12s;
}
.card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--e2);
}
.card:focus-within {
  border-color: var(--focus);
}
.card.current {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent), var(--e1);
}
.card.dragging {
  opacity: 0.5;
}
.card.tone-bad {
  border-left: 3px solid var(--danger);
}
.card.tone-warn {
  border-left: 3px solid var(--warn);
}
.card.tone-info {
  border-left: 3px solid var(--info);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.title {
  font-size: var(--fs-base);
  font-weight: 500;
  line-height: 1.4;
}
/* The title button covers the whole card, so a click anywhere opens it and keyboard users get one tab stop. */
.open {
  all: unset;
  overflow-wrap: anywhere;
}
.open::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
}
.open:focus-visible {
  outline: none;
}
.open:focus-visible::after {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px var(--s2);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.meta:empty {
  display: none;
}
.meta .mono {
  font-size: var(--fs-xs);
}
.pr,
.run-now {
  position: relative;
  z-index: 1;
}
.pr {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-weight: 500;
  text-decoration: none;
  color: var(--ok);
}
.pr:hover {
  text-decoration: underline;
  color: var(--ok);
}
.run-now {
  align-self: flex-start;
  margin-top: 2px;
}
</style>
