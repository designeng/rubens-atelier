<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { ArtifactOut, GitSnapshot } from "../api/types";
import { useBoardStore } from "../composables/useBoard";
import { formatUsd, overBudget, rerunReasons, todoMark } from "../format";
import ProgressBlock from "./ProgressBlock.vue";
import SessionView from "./SessionView.vue";
import TerminalBlock from "./TerminalBlock.vue";

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

// The same changes on github.com: the PR's files once there is one, else a compare view
// (without a base GitHub compares against the default branch). Needs the branch to be pushed.
const githubChanges = computed(() => {
  const i = it.value;
  const project = b.projects.value.find((p) => p.id === i?.project_id);
  if (!i?.branch || !project) return null;
  if (i.pr_url) return `${i.pr_url}/files`;
  const base = project.base_branch ? `${encodeURIComponent(project.base_branch)}...` : "";
  return `https://github.com/${project.repo}/compare/${base}${i.branch.split("/").map(encodeURIComponent).join("/")}`;
});
// Edit the story in place. A step waiting for a slot picks the changes up when it starts,
// a running one from the next agent step; a new priority reorders the queue right away.
const editing = ref(false);
const draft = ref({ title: "", body: "", priority: 0, blocked_by: [] as string[] });
// Items of the project that can be picked as blockers (the server rejects cycles).
const blockerChoices = computed(() => {
  const all = Object.values(b.board.value ?? {}).flat();
  return all.filter((x) => x.id !== it.value?.id);
});
const editError = ref("");
const editBusy = ref(false);
const editable = computed(() => it.value?.column !== "done" && it.value?.column !== "failed");
watch(
  () => it.value?.id,
  () => (editing.value = false),
);
function startEdit(): void {
  const i = it.value;
  if (!i) return;
  draft.value = { title: i.title, body: i.body, priority: i.priority, blocked_by: [...i.blocked_by] };
  editError.value = "";
  editing.value = true;
}
async function saveEdit(): Promise<void> {
  const i = it.value;
  const d = draft.value;
  if (!i) return;
  if (!d.title.trim()) {
    editError.value = "title is required";
    return;
  }
  editBusy.value = true;
  try {
    await b.updateItem(i.id, {
      title: d.title.trim(),
      body: d.body,
      priority: d.priority,
      blocked_by: d.blocked_by,
    });
    editing.value = false;
  } catch (e) {
    editError.value = e instanceof Error ? e.message : String(e);
  } finally {
    editBusy.value = false;
  }
}

// The agent session being watched: a run's harness task (the live one from "Now", or any past run).
const sessionTask = ref<string | null>(null);
const sessionRun = computed(() => it.value?.runs.find((r) => r.task_id === sessionTask.value) ?? null);
watch(
  () => it.value?.id,
  () => (sessionTask.value = null),
);
// A step that ran again shows what sent the story back, e.g. "implement ← sync_base: conflict".
const reruns = computed(() => rerunReasons(it.value?.runs ?? [], it.value?.events ?? []));
const toggleSession = (taskId: string) => (sessionTask.value = sessionTask.value === taskId ? null : taskId);

// The model picker saves right away; "" is the default (the step's model, else the harness's).
const modelOptions = computed(() => {
  const list = b.models.value?.models ?? [];
  const current = it.value?.model;
  return current && !list.includes(current) ? [current, ...list] : list;
});
const modelError = ref("");
async function setModel(value: string): Promise<void> {
  const i = it.value;
  if (!i) return;
  modelError.value = "";
  try {
    await b.updateItem(i.id, { model: value || null });
  } catch (e) {
    modelError.value = e instanceof Error ? e.message : String(e);
  }
}

// The budget saves on change; empty removes the limit. The server refuses zero and negatives.
const budgetError = ref("");
async function setBudget(e: Event): Promise<void> {
  const i = it.value;
  const input = e.target as HTMLInputElement;
  if (!i) return;
  budgetError.value = "";
  try {
    await b.updateItem(i.id, { budget_usd: input.value.trim() === "" ? null : input.valueAsNumber });
  } catch (err) {
    budgetError.value = err instanceof Error ? err.message : String(err);
    input.value = i.budget_usd == null ? "" : String(i.budget_usd);
  }
}

function confirmDelete(id: string, title: string): void {
  if (window.confirm(`Delete “${title}”? This can't be undone.`)) void b.deleteItem(id);
}

// Publishing needs a branch and a story in progress (the server answers 409 otherwise).
const publishable = computed(() => !!it.value?.branch && it.value.state !== null && editable.value);
function confirmOpenPr(id: string): void {
  const msg = "Push the branch and open the PR now? A running agent is cancelled and review and CI are skipped.";
  if (window.confirm(msg)) void b.openPr(id);
}

const reopenable = computed(() => it.value?.column === "failed");
const cancellable = computed(() => it.value?.column === "working" || it.value?.column === "needs_human");
</script>

<template>
  <aside id="detail" :class="{ open: it, wide: sessionTask }">
    <template v-if="it">
      <div class="row">
        <button @click="b.closeItem()">✕</button>
        <button
          v-if="it.state === null"
          class="primary"
          :disabled="it.blocked.length > 0"
          :title="it.blocked.length ? `Blocked by ${it.blocked.join(', ')}` : undefined"
          @click="b.startItem(it.id)"
        >
          Start
        </button>
        <button v-if="reopenable" @click="b.reopenItem(it.id)">Back to backlog</button>
        <button v-if="reopenable" @click="confirmDelete(it.id, it.title)">Delete</button>
        <button v-if="cancellable" @click="b.cancelItem(it.id)">Cancel</button>
        <button v-if="publishable" @click="confirmOpenPr(it.id)">Open PR</button>
        <button v-if="editable && !editing" @click="startEdit()">Edit</button>
      </div>
      <template v-if="editing">
        <p v-if="it.progress?.queued" class="muted">Waiting for a slot: the agent starts with these changes.</p>
        <p v-else-if="it.progress?.task_id" class="muted">An agent is running: changes apply from the next agent step.</p>
        <label for="eTitle">Title</label>
        <input id="eTitle" v-model="draft.title" maxlength="300" style="width: 100%" />
        <label for="eBody">Description</label>
        <textarea id="eBody" v-model="draft.body" rows="8" style="width: 100%"></textarea>
        <div class="row">
          <label for="ePriority">Priority (higher runs first)</label>
          <input id="ePriority" v-model.number="draft.priority" type="number" min="-100" max="100" class="num" />
        </div>
        <label for="eBlockedBy">Blocked by (must succeed before this can start)</label>
        <select id="eBlockedBy" v-model="draft.blocked_by" multiple size="5" style="width: 100%">
          <option v-for="x in blockerChoices" :key="x.id" :value="x.id">{{ x.title }} · {{ x.state || "backlog" }}</option>
        </select>
        <div class="row" style="margin-top: 8px">
          <button class="primary" :disabled="editBusy" @click="saveEdit()">Save</button>
          <button @click="editing = false">Cancel edit</button>
          <span class="err">{{ editError }}</span>
        </div>
      </template>
      <h3 v-else>{{ it.title }}</h3>
      <p class="muted">
        {{ it.kind }} · {{ it.workflow }} · state <b>{{ it.state || "backlog" }}</b>
        <template v-if="it.priority"> · priority {{ it.priority }}</template>
        <template v-if="it.blocked.length"> · blocked by {{ it.blocked.join(", ") }}</template>
        <template v-if="it.branch"> · {{ it.branch }}</template>
        <template v-if="it.pr_url"> · <a :href="it.pr_url" target="_blank" rel="noopener">PR</a></template>
      </p>
      <div class="row model">
        <label for="iModel">Model</label>
        <select id="iModel" :value="it.model ?? ''" :disabled="!editable" @change="setModel(($event.target as HTMLSelectElement).value)">
          <option value="">default<template v-if="b.models.value"> ({{ b.models.value.default }})</template></option>
          <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
        </select>
        <span v-if="it.progress?.task_id" class="muted">the running step keeps its model; the next one uses this</span>
        <span class="err">{{ modelError }}</span>
      </div>
      <div class="row budget">
        <label for="iBudget">Budget, $</label>
        <input
          id="iBudget"
          type="number"
          min="0.01"
          step="any"
          class="num"
          placeholder="none"
          :value="it.budget_usd ?? ''"
          :disabled="!editable"
          :title="it.kind === 'epic' ? 'Covers the epic and everything below it' : 'Covers this item and its children'"
          @change="setBudget"
        />
        <span :class="overBudget(it.spent_usd, it.budget_usd) ? 'err' : 'muted'">
          spent {{ formatUsd(it.spent_usd) }}<template v-if="overBudget(it.spent_usd, it.budget_usd)"> · limit reached: raise it, then retry</template>
        </span>
        <span class="err">{{ budgetError }}</span>
      </div>
      <template v-if="it.progress">
        <h4>Now</h4>
        <ProgressBlock :progress="it.progress" :paused="b.isPaused()" />
        <div v-if="it.progress.task_id" class="row">
          <button @click="toggleSession(it.progress.task_id)">
            {{ sessionTask === it.progress.task_id ? "Hide session" : "Session" }}
          </button>
        </div>
        <ul v-if="it.progress.todos" class="todos">
          <li v-for="(t, n) in it.progress.todos" :key="n">{{ todoMark(t.status) }} {{ t.content }}</li>
        </ul>
      </template>
      <template v-if="sessionTask">
        <h4>
          Session<template v-if="sessionRun"> · {{ sessionRun.state }} · {{ sessionRun.role }}</template>
          <button style="margin-left: 8px" @click="sessionTask = null">✕</button>
        </h4>
        <SessionView :task-id="sessionTask" />
      </template>
      <pre v-if="it.body && !editing">{{ it.body }}</pre>
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
          <p>
            <b>Changes vs base</b>
            <template v-if="githubChanges"> · <a :href="githubChanges" target="_blank" rel="noopener">on GitHub</a></template>
          </p>
          <pre>{{ git.stat || "no changes" }}</pre>
          <template v-if="git.diff != null">
            <p><b>Diff</b></p>
            <pre>{{ git.diff }}</pre>
          </template>
        </template>
        <TerminalBlock :item-id="it.id" />
      </template>
      <h4>Artifacts</h4>
      <template v-for="a in artifacts" :key="a.id">
        <p><b :style="a.name === 'error' ? { color: 'var(--bad)' } : undefined">{{ a.name }}</b></p>
        <pre :class="{ error: a.name === 'error' }">{{ a.content }}</pre>
      </template>
      <p v-if="!artifacts.length" class="muted">none yet</p>
      <h4>Runs</h4>
      <div v-for="r in it.runs" :key="r.id">
        {{ r.state }}<span v-if="reruns.get(r.id)" class="muted"> ← {{ reruns.get(r.id) }}</span> · {{ r.role }} · {{ r.status }}<template v-if="r.verdict"> · result: {{ r.verdict }}</template>
        <template v-if="r.cost_usd != null"> · {{ formatUsd(r.cost_usd) }}</template>
        <span class="muted">task {{ r.task_id.slice(0, 8) }}</span>
        <button class="link" @click="toggleSession(r.task_id)">
          {{ sessionTask === r.task_id ? "hide session" : "session" }}
        </button>
        <div v-if="r.error" class="run-error">{{ r.error }}</div>
      </div>
      <p v-if="!it.runs.length" class="muted">none</p>
      <h4>History</h4>
      <div v-for="(e, n) in it.events" :key="n" class="muted">
        <!-- The verdict is the result of the step that ended (from_state), not the story's status. -->
        {{ new Date(e.at).toLocaleString() }} · {{ e.from_state || "created" }}<template v-if="e.verdict">
          (result: {{ e.verdict }})</template
        >
        → <b>{{ e.to_state }}</b> · {{ e.actor }}
      </div>
    </template>
  </aside>
</template>
