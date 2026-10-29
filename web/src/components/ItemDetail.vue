<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { ArtifactOut } from "../api/types";
import { useBoardStore } from "../composables/useBoard";
import { todoMark } from "../format";
import ProgressBlock from "./ProgressBlock.vue";

const b = useBoardStore();
const it = computed(() => b.item.value);
const req = computed(() => it.value?.open_request ?? null);

// Polling replaces the item every 5 s; the comment survives as long as the request is the same.
const comment = ref("");
watch(
  () => req.value?.id,
  () => (comment.value = ""),
);

// Only the latest version of each artifact, in order of first appearance.
const artifacts = computed(() => {
  const latest = new Map<string, ArtifactOut>();
  for (const a of it.value?.artifacts ?? []) latest.set(a.name, a);
  return [...latest.values()];
});
const cancellable = computed(() => it.value?.column === "working" || it.value?.column === "needs_human");
</script>

<template>
  <aside id="detail" :class="{ open: it }">
    <template v-if="it">
      <div class="row">
        <button @click="b.closeItem()">✕</button>
        <button v-if="it.state === null" class="primary" @click="b.startItem(it.id)">Start</button>
        <button v-if="cancellable" @click="b.cancelItem(it.id)">Cancel</button>
      </div>
      <h3>{{ it.title }}</h3>
      <p class="muted">
        {{ it.kind }} · {{ it.workflow }} · state <b>{{ it.state || "backlog" }}</b>
        <template v-if="it.branch"> · {{ it.branch }}</template>
        <template v-if="it.pr_url"> · <a :href="it.pr_url" target="_blank" rel="noopener">PR</a></template>
      </p>
      <template v-if="it.progress">
        <h4>Now</h4>
        <ProgressBlock :progress="it.progress" />
        <ul v-if="it.progress.todos" class="todos">
          <li v-for="(t, n) in it.progress.todos" :key="n">{{ todoMark(t.status) }} {{ t.content }}</li>
        </ul>
      </template>
      <pre v-if="it.body">{{ it.body }}</pre>
      <template v-if="req">
        <h4>Decision needed</h4>
        <p>{{ req.question }}</p>
        <textarea v-model="comment" rows="3" style="width: 100%" placeholder="Comment for the next agent (optional)"></textarea>
        <div class="row">
          <button v-for="o in req.options" :key="o" class="primary" @click="b.answer(req.id, o, comment)">{{ o }}</button>
        </div>
      </template>
      <h4>Artifacts</h4>
      <template v-for="a in artifacts" :key="a.id">
        <p><b>{{ a.name }}</b></p>
        <pre>{{ a.content }}</pre>
      </template>
      <p v-if="!artifacts.length" class="muted">none yet</p>
      <h4>Runs</h4>
      <div v-for="r in it.runs" :key="r.id">
        {{ r.state }} · {{ r.role }} · {{ r.status }}<template v-if="r.verdict"> → {{ r.verdict }}</template>
        <template v-if="r.cost_usd != null"> · ${{ r.cost_usd.toFixed(2) }}</template>
        <span class="muted">task {{ r.task_id.slice(0, 8) }}</span>
      </div>
      <p v-if="!it.runs.length" class="muted">none</p>
      <h4>History</h4>
      <div v-for="(e, n) in it.events" :key="n" class="muted">
        {{ new Date(e.at).toLocaleString() }} · {{ e.from_state || "∅" }} -[{{ e.verdict || "start" }}]→
        <b>{{ e.to_state }}</b> · {{ e.actor }}
      </div>
    </template>
  </aside>
</template>
