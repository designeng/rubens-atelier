<script setup lang="ts">
import type { Progress } from "../../api/types";
import { useBoardStore } from "../../composables/useBoard";
import { todoMark } from "../../format";
import ProgressBlock from "../ProgressBlock.vue";
import Icon from "../ui/Icon.vue";

// What the story's agent is doing right now, with a way into its live session.
defineProps<{ progress: Progress; sessionTask: string | null }>();
const emit = defineEmits<{ "toggle-session": [taskId: string] }>();

const b = useBoardStore();
</script>

<template>
  <section class="section">
    <div class="section-title">Now</div>
    <ProgressBlock :progress="progress" :paused="b.isPaused()" />
    <ul v-if="progress.todos" class="todos">
      <li v-for="(t, n) in progress.todos" :key="n" :class="t.status">
        <span class="todo-mark" aria-hidden="true">{{ todoMark(t.status) }}</span>{{ t.content }}
      </li>
    </ul>
    <button v-if="progress.task_id" type="button" class="btn sm" @click="emit('toggle-session', progress.task_id)">
      <Icon name="terminal" :size="14" /> {{ sessionTask === progress.task_id ? "Hide session" : "Watch session" }}
    </button>
  </section>
</template>

<style scoped>
.todos {
  list-style: none;
  padding: 0;
  margin: var(--s3) 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--fs-md);
}
.todos li {
  display: flex;
  gap: var(--s2);
}
.todos li.completed {
  color: var(--text-muted);
  text-decoration: line-through;
  text-decoration-color: var(--border-strong);
}
.todos li.in_progress {
  font-weight: 600;
}
.todo-mark {
  flex: none;
  width: 1em;
  color: var(--text-subtle);
}
</style>
