<script setup lang="ts">
import type { EventOut } from "../../api/types";

// The story's transitions between workflow states.
defineProps<{ events: EventOut[] }>();
</script>

<template>
  <section class="section">
    <div class="section-title">History</div>
    <ol class="history">
      <li v-for="(e, n) in events" :key="n">
        <time :datetime="e.at" class="subtle">{{ new Date(e.at).toLocaleString() }}</time>
        <span>
          <!-- The verdict is the result of the step that ended (from_state), not the story's status. -->
          {{ e.from_state || "created" }}<template v-if="e.verdict"> ({{ e.verdict }})</template> → <b>{{ e.to_state }}</b>
          <span class="subtle"> · {{ e.actor }}</span>
        </span>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.history {
  list-style: none;
  margin: 0;
  padding: 0;
}
.history li {
  display: grid;
  grid-template-columns: 170px 1fr;
  gap: var(--s3);
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  font-size: var(--fs-md);
}
.history time {
  font-variant-numeric: tabular-nums;
}
@media (max-width: 767px) {
  .history li {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
