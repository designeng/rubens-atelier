<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { ApiError } from "../api/client";
import type { TaskEvent, TaskInfo } from "../api/types";
import { useBoardStore } from "../composables/useBoard";
import { isFileEdit } from "../diff";
import { formatTokens, formatUsd } from "../format";
import { contextTokens, describeTool, transcript } from "../session";
import { isShell } from "../shell";
import DiffBlock from "./DiffBlock.vue";
import ShellBlock from "./ShellBlock.vue";

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
const short = ref(true);
const log = ref<HTMLElement | null>(null);
const entries = computed(() => transcript(events.value));
const turns = computed(() => events.value.filter((e) => e.type === "turn").length);
const context = computed(() => contextTokens(events.value));

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

const summaryOf = (e: Extract<(typeof entries.value)[number], { kind: "tool" }>) =>
  e.input == null ? e.summary : describeTool(e.name, e.input, short.value);
const fmtInput = (input: unknown) => JSON.stringify(input, null, 2);
</script>

<template>
  <div class="session">
    <div class="s-head">
      <span v-if="live" class="spin" aria-label="live"></span>
      <span v-if="task" class="chip" :class="task.status === 'succeeded' ? 'ok' : task.status === 'failed' ? 'bad' : task.status === 'running' ? 'accent' : ''">{{
        task.status
      }}</span>
      <span>{{ turns }} turns</span>
      <span v-if="context != null">context {{ formatTokens(context) }}</span>
      <span v-if="task?.cost_usd != null">{{ formatUsd(task.cost_usd) }}</span>
      <span v-if="task?.session_id" class="mono subtle sid" :title="`session ${task.session_id}`">{{ task.session_id.slice(0, 8) }}</span>
      <span class="spacer"></span>
      <label class="check follow" title="Hide the workspace directory from file paths"><input v-model="short" type="checkbox" /> Short paths</label>
      <label class="check follow" title="Keep the newest entry in view"><input v-model="follow" type="checkbox" /> Follow</label>
    </div>
    <p v-if="error" class="err">{{ error }}</p>
    <div ref="log" class="log" @scroll="onScroll">
      <details v-if="task?.prompt" class="s-tool s-prompt">
        <summary>Prompt</summary>
        <pre>{{ task.prompt }}</pre>
      </details>
      <template v-for="(e, n) in entries" :key="n">
        <div v-if="e.kind === 'text'" class="s-text">{{ e.text }}</div>
        <details v-else-if="e.kind === 'thinking'" class="s-thinking">
          <summary>Thinking</summary>
          <div class="s-text">{{ e.text }}</div>
        </details>
        <details v-else-if="e.kind === 'tool'" class="s-tool" :class="{ bad: e.result?.isError }">
          <summary>
            <span v-if="!e.result && live" class="spin"></span>{{ summaryOf(e) }}
          </summary>
          <template v-if="isFileEdit(e.name, e.input) || isShell(e.name, e.input)">
            <DiffBlock v-if="isFileEdit(e.name, e.input)" :name="e.name" :input="e.input" :short="short" />
            <ShellBlock v-else :input="e.input" :short="short" />
            <details class="s-raw">
              <summary>raw input</summary>
              <pre>{{ fmtInput(e.input) }}</pre>
            </details>
          </template>
          <pre v-else-if="e.input != null">{{ fmtInput(e.input) }}</pre>
          <pre v-if="e.result" class="s-out" :class="{ error: e.result.isError }">{{ e.result.content || "(empty)" }}</pre>
        </details>
        <details v-else-if="e.kind === 'verify'" class="s-tool" :class="{ bad: e.exitCode !== 0 }">
          <summary>verify: {{ e.command }} → exit {{ e.exitCode }}</summary>
          <pre>{{ e.output }}</pre>
        </details>
        <div v-else-if="e.kind === 'result'" class="s-result" :class="{ bad: e.isError }">
          <b>Result</b> · {{ e.summary }}
          <div v-if="e.text" class="s-text">{{ e.text }}</div>
        </div>
        <div v-else class="s-note">{{ e.text }}</div>
      </template>
      <p v-if="!entries.length && !error" class="subtle">{{ live ? "Waiting for events…" : "No events" }}</p>
    </div>
  </div>
</template>

<style scoped>
.s-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s1) var(--s3);
  margin-bottom: var(--s2);
  font-size: var(--fs-md);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.follow {
  font-size: var(--fs-sm);
}
.log {
  max-height: 65vh;
  overflow: auto;
  padding: var(--s3);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  background: var(--surface-2);
  font-size: var(--fs-md);
}
.s-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: var(--s2) 0;
  line-height: 1.55;
}
.s-thinking,
.s-note {
  color: var(--text-subtle);
  font-size: var(--fs-sm);
  margin: 2px 0;
}
.s-thinking .s-text {
  font-style: italic;
}
.s-tool {
  margin: 2px 0;
  font: var(--fs-sm) / 1.45 var(--font-mono);
}
.s-tool summary,
.s-thinking summary {
  cursor: pointer;
  overflow-wrap: anywhere;
  padding: 2px 4px;
  border-radius: 4px;
}
.s-tool summary:hover,
.s-thinking summary:hover {
  background: var(--surface-hover);
}
.s-tool pre {
  margin: 4px 0 6px;
  background: var(--surface);
}
/* The call's result and raw input are subsections of the call. */
.s-raw,
.s-tool pre.s-out {
  margin-left: var(--s4);
}
.s-raw summary {
  color: var(--text-subtle);
}
.s-prompt summary {
  font-family: var(--font-ui);
  font-weight: 600;
}
.s-tool.bad summary,
.s-result.bad {
  color: var(--danger);
}
.s-result {
  border-top: 1px solid var(--border);
  margin-top: var(--s3);
  padding-top: var(--s2);
}
</style>
