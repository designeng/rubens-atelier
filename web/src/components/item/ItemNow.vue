<script setup lang="ts">
import { ref, watch } from "vue";
import type { ArtifactOut, Progress } from "../../api/types";
import { useBoardStore } from "../../composables/useBoard";
import { todoMark } from "../../format";
import ProgressBlock from "../ProgressBlock.vue";
import Icon from "../ui/Icon.vue";

// What the story's agent is doing right now, with a way into its live session and a way to comment on its work.
const props = defineProps<{ itemId: string; progress: Progress; sessionTask: string | null; pendingComments: ArtifactOut[] }>();
const emit = defineEmits<{ "toggle-session": [taskId: string] }>();

const b = useBoardStore();

const comment = ref("");
const sending = ref(false);
watch(
  () => props.itemId,
  () => (comment.value = ""),
);
async function send(): Promise<void> {
  const body = comment.value.trim();
  if (!body || sending.value) return;
  sending.value = true;
  if (await b.commentItem(props.itemId, body)) comment.value = "";
  sending.value = false;
}
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
    <form v-if="progress.kind === 'agent'" class="comment" @submit.prevent="send">
      <label for="dAgentComment" class="subtle">Comment for the {{ progress.step }}</label>
      <textarea id="dAgentComment" v-model="comment" rows="2" maxlength="10000" placeholder="Also run the tests…"></textarea>
      <div class="comment-foot">
        <span class="subtle">The current run finishes, then the {{ progress.step }} runs again to read this, at extra cost.</span>
        <button type="submit" class="btn sm" :disabled="!comment.trim() || sending">Send</button>
      </div>
    </form>
    <div v-if="pendingComments.length" class="pending">
      <div class="subtle">Waiting for the agent</div>
      <p v-for="c in pendingComments" :key="c.id">{{ c.content }}</p>
    </div>
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
.comment {
  display: flex;
  flex-direction: column;
  gap: var(--s1);
  margin-top: var(--s3);
}
.comment textarea {
  width: 100%;
}
.comment-foot {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s3);
  font-size: var(--fs-md);
}
.comment-foot .btn {
  flex: none;
}
.pending {
  margin-top: var(--s3);
  font-size: var(--fs-md);
}
.pending p {
  margin: var(--s1) 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
