<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { ArtifactOut, GitSnapshot } from "../api/types";
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

// Read-only look into the story's workspace: what the agent has committed (and left uncommitted).
const git = ref<GitSnapshot | null>(null);
const gitError = ref("");
const gitBusy = ref(false);
watch(
  () => it.value?.id,
  () => {
    git.value = null;
    gitError.value = "";
  },
);
async function loadGit(diff = false): Promise<void> {
  if (!it.value) return;
  const id = it.value.id;
  gitBusy.value = true;
  gitError.value = "";
  try {
    const snap = await b.api.getItemGit(id, diff);
    if (it.value?.id === id) git.value = snap;
  } catch (e) {
    gitError.value = e instanceof Error ? e.message : String(e);
  } finally {
    gitBusy.value = false;
  }
}
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
      <template v-if="it.branch">
        <h4>Git</h4>
        <div class="row">
          <button :disabled="gitBusy" @click="loadGit()">{{ git ? "Refresh" : "Load report" }}</button>
          <button :disabled="gitBusy" @click="loadGit(true)">Show diff</button>
        </div>
        <p v-if="gitError" class="err">{{ gitError }}</p>
        <template v-if="git">
          <p class="muted">
            {{ git.branch }} @ {{ git.head }}<template v-if="git.merge_in_progress"> · merge in progress</template>
          </p>
          <p><b>Commits</b></p>
          <pre>{{ git.commits || "no commits on top of the base branch" }}</pre>
          <p><b>Status</b></p>
          <pre>{{ git.status }}</pre>
          <p><b>Changes vs base</b></p>
          <pre>{{ git.stat || "no changes" }}</pre>
          <template v-if="git.diff != null">
            <p><b>Diff</b></p>
            <pre>{{ git.diff }}</pre>
          </template>
        </template>
      </template>
      <h4>Artifacts</h4>
      <template v-for="a in artifacts" :key="a.id">
        <p><b :style="a.name === 'error' ? { color: 'var(--bad)' } : undefined">{{ a.name }}</b></p>
        <pre :class="{ error: a.name === 'error' }">{{ a.content }}</pre>
      </template>
      <p v-if="!artifacts.length" class="muted">none yet</p>
      <h4>Runs</h4>
      <div v-for="r in it.runs" :key="r.id">
        {{ r.state }} · {{ r.role }} · {{ r.status }}<template v-if="r.verdict"> → {{ r.verdict }}</template>
        <template v-if="r.cost_usd != null"> · ${{ r.cost_usd.toFixed(2) }}</template>
        <span class="muted">task {{ r.task_id.slice(0, 8) }}</span>
        <div v-if="r.error" class="run-error">{{ r.error }}</div>
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
