<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import type { ItemDetail } from "../../api/types";
import { useBoardStore } from "../../composables/useBoard";
import Field from "../ui/Field.vue";

// Edit the story in place. A step waiting for a slot picks the changes up when it starts,
// a running one from the next agent step; a new priority reorders the queue right away.
const props = defineProps<{ item: ItemDetail }>();
const emit = defineEmits<{ close: [] }>();

const b = useBoardStore();
const draft = ref({
  title: props.item.title,
  body: props.item.body,
  priority: props.item.priority,
  blocked_by: [...props.item.blocked_by],
});
// Items of the project that can be picked as blockers (the server rejects cycles).
const blockerChoices = computed(() => Object.values(b.board.value ?? {}).flat().filter((x) => x.id !== props.item.id));
const editError = ref("");
const editBusy = ref(false);

onMounted(() => nextTick(() => document.getElementById("eTitle")?.focus()));

async function saveEdit(): Promise<void> {
  const d = draft.value;
  if (!d.title.trim()) {
    editError.value = "Title is required";
    return;
  }
  editBusy.value = true;
  try {
    await b.updateItem(props.item.id, {
      title: d.title.trim(),
      body: d.body,
      priority: d.priority,
      blocked_by: d.blocked_by,
    });
    emit("close");
  } catch (e) {
    editError.value = e instanceof Error ? e.message : String(e);
  } finally {
    editBusy.value = false;
  }
}
</script>

<template>
  <form class="section edit" @submit.prevent="saveEdit()">
    <p v-if="item.progress?.queued" class="note">Waiting for a slot: the agent starts with these changes.</p>
    <p v-else-if="item.progress?.task_id" class="note">An agent is running: changes apply from the next agent step.</p>
    <Field label="Title" for="eTitle"><input id="eTitle" v-model="draft.title" maxlength="300" required /></Field>
    <Field label="Description" for="eBody"><textarea id="eBody" v-model="draft.body" rows="8"></textarea></Field>
    <Field label="Priority" for="ePriority" hint="−100 to 100; higher runs first. Low −10, Normal 0, High 10, Urgent 50.">
      <input id="ePriority" v-model.number="draft.priority" type="number" min="-100" max="100" class="num" aria-describedby="ePriority-hint" />
    </Field>
    <Field label="Blocked by" for="eBlockedBy" hint="These must succeed before this story can start. Ctrl/⌘-click to pick several.">
      <select id="eBlockedBy" v-model="draft.blocked_by" multiple size="5" aria-describedby="eBlockedBy-hint">
        <option v-for="x in blockerChoices" :key="x.id" :value="x.id">{{ x.title }} · {{ x.state || "backlog" }}</option>
      </select>
    </Field>
    <div class="row">
      <span class="err">{{ editError }}</span>
      <span class="spacer"></span>
      <button type="button" class="btn" @click="emit('close')">Cancel</button>
      <button type="submit" class="btn primary" :disabled="editBusy">Save changes</button>
    </div>
  </form>
</template>

<style scoped>
.edit .row {
  margin-top: var(--s2);
}
</style>
