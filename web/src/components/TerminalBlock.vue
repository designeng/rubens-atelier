<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import type { ExecResult } from "../api/types";
import { useBoardStore } from "../composables/useBoard";
import { execFinished, execSummary, pollExec } from "../terminal";

const props = defineProps<{ itemId: string }>();
const b = useBoardStore();

const command = ref("");
const result = ref<ExecResult | null>(null);
const error = ref("");
const busy = ref(false);
const running = computed(() => busy.value || (!!result.value && !execFinished(result.value)));

// Bumped to abandon a poll: when the story changes or the block goes away.
let generation = 0;
const reset = () => {
  generation++;
  command.value = "";
  result.value = null;
  error.value = "";
  busy.value = false;
};
watch(() => props.itemId, reset);
onBeforeUnmount(() => generation++);

async function run(): Promise<void> {
  const cmd = command.value.trim();
  if (!cmd || running.value) return;
  const id = props.itemId;
  const mine = ++generation;
  busy.value = true;
  error.value = "";
  result.value = null;
  try {
    const { id: execId } = await b.api.execCommand(id, cmd);
    await pollExec(
      () => b.api.getExec(id, execId),
      (r) => (result.value = r),
      () => generation !== mine,
    );
  } catch (e) {
    if (generation === mine) error.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (generation === mine) busy.value = false;
  }
}
</script>

<template>
  <h4>Terminal</h4>
  <form class="row" @submit.prevent="run()">
    <input v-model="command" placeholder="shell command in the story's workspace" maxlength="4000" style="flex: 1" />
    <button class="primary" type="submit" :disabled="running || !command.trim()">{{ running ? "Running…" : "Run" }}</button>
  </form>
  <p v-if="error" class="err">{{ error }}</p>
  <template v-if="result">
    <p class="muted">
      $ {{ result.command }}<template v-if="execFinished(result)"> · {{ execSummary(result) }}</template>
    </p>
    <pre>{{ result.output || (execFinished(result) ? "no output" : "") }}</pre>
  </template>
</template>
