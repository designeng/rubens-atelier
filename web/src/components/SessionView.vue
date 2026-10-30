<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { ApiError } from "../api/client";
import type { TaskEvent, TaskInfo } from "../api/types";
import { useBoardStore } from "../composables/useBoard";
import { formatUsd } from "../format";
import { transcript } from "../session";

// The full agent session of one run, live: replays the task's event log over SSE and keeps
// following it until the task finishes, reconnecting from the last event after a drop.
const props = defineProps<{ taskId: string }>();
const b = useBoardStore();

const RETRY_MS = 3000;
// Replays send thousands of events at once: rebuild the transcript at most this often.
const FLUSH_MS = 150;
const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

const task = ref<TaskInfo | null>(null);
const events = ref<TaskEvent[]>([]);
const live = ref(false);
const error = ref("");
const follow = ref(true);
const log = ref<HTMLElement | null>(null);
const entries = computed(() => transcript(events.value));
const turns = computed(() => events.value.filter((e) => e.type === "turn").length);

let abort: AbortController | null = null;
let pending: TaskEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | undefined;

function flush(): void {
  flushTimer = undefined;
  if (pending.length) events.value = events.value.concat(pending);
  pending = [];
}

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => (clearTimeout(t), resolve()), { once: true });
  });

async function run(id: string, signal: AbortSignal): Promise<void> {
  let cursor = "0";
  live.value = true;
  try {
    task.value = await b.api.getTask(id);
    while (!signal.aborted) {
      try {
        await b.api.streamTask(
          id,
          cursor,
          (e) => {
            cursor = e.id;
            error.value = "";
            pending.push(e);
            flushTimer ??= setTimeout(flush, FLUSH_MS);
          },
          signal,
        );
        error.value = "";
        // The stream ends when the task does; otherwise it was cut, so pick it up again.
        task.value = await b.api.getTask(id);
        if (TERMINAL.has(task.value.status)) return;
      } catch (e) {
        if (signal.aborted || (e instanceof ApiError && e.status === 404)) throw e;
        error.value = `connection lost, reconnecting: ${e instanceof Error ? e.message : String(e)}`;
      }
      await sleep(RETRY_MS, signal);
    }
  } catch (e) {
    if (signal.aborted) return;
    error.value =
      e instanceof ApiError && e.status === 404
        ? "No session log: Redis has expired this task and it is not in the archive (runs finished before archiving was added)."
        : e instanceof Error
          ? e.message
          : String(e);
  } finally {
    if (!signal.aborted) {
      flush();
      live.value = false;
    }
  }
}

watch(
  () => props.taskId,
  (id) => {
    abort?.abort();
    clearTimeout(flushTimer);
    flushTimer = undefined;
    pending = [];
    events.value = [];
    task.value = null;
    error.value = "";
    follow.value = true;
    abort = new AbortController();
    run(id, abort.signal);
  },
  { immediate: true },
);
onUnmounted(() => {
  abort?.abort();
  clearTimeout(flushTimer);
});

// Keep the newest entry in view while following; scrolling up stops following.
watch(
  () => entries.value.length,
  async () => {
    if (!follow.value) return;
    await nextTick();
    if (log.value) log.value.scrollTop = log.value.scrollHeight;
  },
);
function onScroll(): void {
  const el = log.value;
  if (el) follow.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
}

const fmtInput = (input: unknown) => JSON.stringify(input, null, 2);
</script>

<template>
  <div class="session">
    <p class="muted">
      <span v-if="live" class="spin"></span>
      <template v-if="task">{{ task.status }}</template>
      · {{ turns }} turns
      <template v-if="task?.cost_usd != null"> · {{ formatUsd(task.cost_usd) }}</template>
      <template v-if="task?.session_id"> · session {{ task.session_id }}</template>
      <label class="follow"><input v-model="follow" type="checkbox" /> follow</label>
    </p>
    <p v-if="error" class="err">{{ error }}</p>
    <div ref="log" class="log" @scroll="onScroll">
      <details v-if="task?.prompt" class="s-tool">
        <summary>prompt</summary>
        <pre>{{ task.prompt }}</pre>
      </details>
      <template v-for="(e, n) in entries" :key="n">
        <div v-if="e.kind === 'text'" class="s-text">{{ e.text }}</div>
        <details v-else-if="e.kind === 'thinking'" class="s-thinking">
          <summary>thinking</summary>
          <div class="s-text">{{ e.text }}</div>
        </details>
        <details v-else-if="e.kind === 'tool'" class="s-tool" :class="{ bad: e.result?.isError }">
          <summary>
            <span v-if="!e.result && live" class="spin"></span>{{ e.summary }}
          </summary>
          <pre v-if="e.input != null">{{ fmtInput(e.input) }}</pre>
          <pre v-if="e.result" :class="{ error: e.result.isError }">{{ e.result.content || "(empty)" }}</pre>
        </details>
        <details v-else-if="e.kind === 'verify'" class="s-tool" :class="{ bad: e.exitCode !== 0 }">
          <summary>verify: {{ e.command }} → exit {{ e.exitCode }}</summary>
          <pre>{{ e.output }}</pre>
        </details>
        <div v-else-if="e.kind === 'result'" class="s-result" :class="{ bad: e.isError }">
          <b>result</b> · {{ e.summary }}
          <div v-if="e.text" class="s-text">{{ e.text }}</div>
        </div>
        <div v-else class="s-note">{{ e.text }}</div>
      </template>
      <p v-if="!entries.length && !error" class="muted">{{ live ? "waiting for events…" : "no events" }}</p>
    </div>
  </div>
</template>
