<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import type { ArtifactOut, GitSnapshot } from "../api/types";
import { useBoardStore, type ItemTab } from "../composables/useBoard";
import { formatUsd, overBudget, rerunReasons, todoMark } from "../format";
import { urgencyLabel } from "../urgency";
import ProgressBlock from "./ProgressBlock.vue";
import SessionView from "./SessionView.vue";
import TerminalBlock from "./TerminalBlock.vue";
import Field from "./ui/Field.vue";
import Icon from "./ui/Icon.vue";
import Popover from "./ui/Popover.vue";

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
// The error that sent the story to Needs you or Failed, shown next to the decision rather than in the artifacts list.
const latestError = computed(() =>
  it.value?.column === "needs_human" || it.value?.column === "failed" ? artifacts.value.find((a) => a.name === "error") : undefined,
);

const STATUS: Record<string, { label: string; tone: string }> = {
  backlog: { label: "Backlog", tone: "" },
  working: { label: "In progress", tone: "accent" },
  needs_human: { label: "Needs you", tone: "warn" },
  done: { label: "Done", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
};
const status = computed(() => (it.value ? STATUS[it.value.column] : null));

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
  b.setItemTab("overview");
  nextTick(() => document.getElementById("eTitle")?.focus());
}
async function saveEdit(): Promise<void> {
  const i = it.value;
  const d = draft.value;
  if (!i) return;
  if (!d.title.trim()) {
    editError.value = "Title is required";
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

// Autosaved fields flash "Saved" next to themselves for a moment.
const saved = ref<string | null>(null);
let savedTimer: ReturnType<typeof setTimeout> | undefined;
function flashSaved(field: string): void {
  saved.value = field;
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => (saved.value = null), 2000);
}

// The model picker saves right away; "" is the default (the step's model, else the harness's).
const modelOptions = computed(() => {
  const list = b.models.value?.models ?? [];
  const current = it.value?.model;
  return current && !list.includes(current) ? [current, ...list] : list;
});
const modelError = ref("");
// Lean context saves right away too; "" is the harness default (HARNESS_LEAN_CONTEXT).
const contextValue = computed(() => (it.value?.lean_context == null ? "" : it.value.lean_context ? "lean" : "full"));
const contextError = ref("");
async function setContext(value: string): Promise<void> {
  const i = it.value;
  if (!i) return;
  contextError.value = "";
  try {
    await b.updateItem(i.id, { lean_context: value ? value === "lean" : null });
    flashSaved("context");
  } catch (e) {
    contextError.value = e instanceof Error ? e.message : String(e);
  }
}

async function setModel(value: string): Promise<void> {
  const i = it.value;
  if (!i) return;
  modelError.value = "";
  try {
    await b.updateItem(i.id, { model: value || null });
    flashSaved("model");
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
    flashSaved("budget");
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
const hasMenu = computed(() => (editable.value && !editing.value) || publishable.value || cancellable.value || reopenable.value);

// The first answer is the suggested one; giving up is marked as destructive.
const optionClass = (o: string, n: number) => (/abandon|cancel|reject|give up/i.test(o) ? "danger" : n === 0 ? "primary" : "");

// One request at a time per story action, so a double click doesn't send it twice.
const busy = ref(false);
async function run(action: () => Promise<void>): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  try {
    await action();
  } finally {
    busy.value = false;
  }
}

const TABS: { tab: ItemTab; label: string }[] = [
  { tab: "overview", label: "Overview" },
  { tab: "runs", label: "Runs" },
  { tab: "workspace", label: "Workspace" },
];
function onTabKey(e: KeyboardEvent): void {
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  const i = TABS.findIndex((t) => t.tab === b.itemTab.value);
  const next = TABS[(i + (e.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length].tab;
  b.setItemTab(next);
  document.getElementById(`dtab-${next}`)?.focus();
}

// Focus moves into the drawer when a story opens and back to where it was when it closes; Esc closes it.
const heading = ref<HTMLElement>();
let returnFocus: HTMLElement | null = null;
watch(
  () => it.value?.id,
  async (id, prev) => {
    if (id && !prev) {
      returnFocus = document.activeElement as HTMLElement | null;
      await nextTick();
      heading.value?.focus({ preventScroll: true });
    } else if (!id && prev && returnFocus?.isConnected) {
      returnFocus.focus();
      returnFocus = null;
    }
  },
);
function onKey(e: KeyboardEvent): void {
  if (e.key !== "Escape" || !b.openItemId.value || document.querySelector("dialog[open]")) return;
  if (editing.value) editing.value = false;
  else b.closeItem();
}
onMounted(() => document.addEventListener("keydown", onKey));
onUnmounted(() => {
  document.removeEventListener("keydown", onKey);
  clearTimeout(savedTimer);
});
</script>

<template>
  <div v-if="it" class="scrim" aria-hidden="true"></div>
  <aside v-if="it" id="detail" class="drawer" :class="{ wide: sessionTask }" aria-labelledby="dTitle">
    <header class="d-head">
      <div class="d-bar">
        <span v-if="status" class="chip" :class="status.tone">{{ status.label }}</span>
        <span v-if="it.state && it.column !== 'backlog'" class="mono subtle">{{ it.state }}</span>
        <span class="spacer"></span>
        <button
          v-if="it.state === null"
          type="button"
          class="btn primary"
          :disabled="it.blocked.length > 0 || busy"
          :title="it.blocked.length ? `Blocked by ${it.blocked.join(', ')}` : 'Start the workflow now'"
          @click="run(() => b.startItem(it!.id))"
        >
          <Icon name="play" /> Start
        </button>
        <button v-if="reopenable" type="button" class="btn" :disabled="busy" @click="run(() => b.reopenItem(it!.id))">
          <Icon name="undo" /> Back to backlog
        </button>
        <Popover v-if="hasMenu" label="Story actions" menu align="end" button-class="icon-btn ghost">
          <template #button><Icon name="more" /></template>
          <button v-if="editable && !editing" type="button" class="menu-item" role="menuitem" @click="startEdit()">
            <Icon name="pencil" /> Edit story
          </button>
          <button v-if="publishable" type="button" class="menu-item" role="menuitem" @click="confirmOpenPr(it.id)">
            <Icon name="pr" /> Open PR now…
          </button>
          <template v-if="cancellable || reopenable">
            <div class="menu-sep" role="separator"></div>
            <button v-if="cancellable" type="button" class="menu-item danger" role="menuitem" @click="run(() => b.cancelItem(it!.id))">
              <Icon name="stop" /> Cancel story
              <span class="hint">stops the agent</span>
            </button>
            <button v-if="reopenable" type="button" class="menu-item danger" role="menuitem" @click="confirmDelete(it.id, it.title)">
              <Icon name="trash" /> Delete…
            </button>
          </template>
        </Popover>
        <button type="button" class="icon-btn ghost" aria-label="Close story" title="Close (Esc)" @click="b.closeItem()">
          <Icon name="x" />
        </button>
      </div>
      <div class="d-title-row">
        <h2 id="dTitle" ref="heading" tabindex="-1" class="d-title">{{ it.title }}</h2>
        <button
          v-if="editable && !editing"
          type="button"
          class="icon-btn ghost sm"
          aria-label="Edit story"
          title="Edit story"
          @click="startEdit()"
        >
          <Icon name="pencil" :size="14" />
        </button>
      </div>
      <div class="d-meta">
        <span v-if="it.kind !== 'story' || it.workflow !== 'story'">{{ it.kind }} · {{ it.workflow }}</span>
        <span v-if="it.priority">{{ urgencyLabel(it.priority) }}</span>
        <span v-if="it.issue">#{{ it.issue }}</span>
        <span v-if="it.branch" class="mono" title="Branch"><Icon name="branch" :size="13" /> {{ it.branch }}</span>
        <a v-if="it.pr_url" :href="it.pr_url" target="_blank" rel="noopener" class="pr-link">
          <Icon name="pr" :size="13" /> Pull request <Icon name="external" :size="12" />
        </a>
        <span v-if="it.blocked.length" class="chip">Blocked by {{ it.blocked.join(", ") }}</span>
        <span v-if="it.ready" class="chip ok">Ready to start · blockers done</span>
      </div>
      <div class="tabs d-tabs" role="tablist" aria-label="Story sections" @keydown="onTabKey">
        <button
          v-for="t in TABS"
          :id="`dtab-${t.tab}`"
          :key="t.tab"
          type="button"
          class="tab"
          role="tab"
          :aria-selected="b.itemTab.value === t.tab"
          :aria-controls="`dpanel-${t.tab}`"
          :tabindex="b.itemTab.value === t.tab ? 0 : -1"
          @click="b.setItemTab(t.tab)"
        >
          {{ t.label }}
          <span v-if="t.tab === 'runs' && it.runs.length" class="count">{{ it.runs.length }}</span>
        </button>
      </div>
    </header>

    <div class="d-body">
      <section v-if="sessionTask" class="section session-section">
        <div class="section-title">
          <Icon name="terminal" :size="14" /> Session<template v-if="sessionRun"> · {{ sessionRun.state }} · {{ sessionRun.role }}</template>
          <span class="spacer"></span>
          <button type="button" class="btn ghost sm" @click="sessionTask = null"><Icon name="x" :size="14" /> Close session</button>
        </div>
        <SessionView :task-id="sessionTask" />
      </section>

      <!-- Overview: what needs doing now, then what the story is, then how its agents are set up. -->
      <div v-show="b.itemTab.value === 'overview'" id="dpanel-overview" role="tabpanel" aria-labelledby="dtab-overview">
        <form v-if="editing" class="section edit" @submit.prevent="saveEdit()">
          <p v-if="it.progress?.queued" class="note">Waiting for a slot: the agent starts with these changes.</p>
          <p v-else-if="it.progress?.task_id" class="note">An agent is running: changes apply from the next agent step.</p>
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
            <button type="button" class="btn" @click="editing = false">Cancel</button>
            <button type="submit" class="btn primary" :disabled="editBusy">Save changes</button>
          </div>
        </form>

        <section v-if="req" class="section decision" aria-labelledby="dDecision">
          <h3 id="dDecision" class="decision-title"><Icon name="alert" /> Decision needed</h3>
          <p class="decision-q">{{ req.question }}</p>
          <pre v-if="latestError" class="error">{{ latestError.content }}</pre>
          <label for="dComment" class="sr-only">Comment for the next agent</label>
          <textarea id="dComment" v-model="comment" rows="3" placeholder="Comment for the next agent (optional)"></textarea>
          <div class="row">
            <button
              v-for="(o, n) in req.options"
              :key="o"
              type="button"
              class="btn"
              :class="optionClass(o, n)"
              :disabled="busy"
              @click="run(() => b.answer(req!.id, o, comment))"
            >
              {{ o.charAt(0).toUpperCase() + o.slice(1) }}
            </button>
          </div>
        </section>
        <section v-else-if="latestError" class="section">
          <div class="section-title">Latest error</div>
          <pre class="error">{{ latestError.content }}</pre>
        </section>
        <section v-else-if="it.attention?.text" class="section">
          <p class="note">{{ it.attention.text }}</p>
        </section>

        <section v-if="it.progress" class="section">
          <div class="section-title">Now</div>
          <ProgressBlock :progress="it.progress" :paused="b.isPaused()" />
          <ul v-if="it.progress.todos" class="todos">
            <li v-for="(t, n) in it.progress.todos" :key="n" :class="t.status">
              <span class="todo-mark" aria-hidden="true">{{ todoMark(t.status) }}</span>{{ t.content }}
            </li>
          </ul>
          <button v-if="it.progress.task_id" type="button" class="btn sm" @click="toggleSession(it.progress.task_id)">
            <Icon name="terminal" :size="14" /> {{ sessionTask === it.progress.task_id ? "Hide session" : "Watch session" }}
          </button>
        </section>

        <section v-if="!editing" class="section">
          <div class="section-title">Description</div>
          <div v-if="it.body" class="body-text">{{ it.body }}</div>
          <p v-else class="subtle">No description.</p>
        </section>

        <section class="section">
          <div class="section-title">Agent settings</div>
          <p v-if="it.progress?.task_id" class="note">The running step keeps its settings; the next step uses these.</p>
          <div class="settings">
            <label for="iModel">Model</label>
            <div class="setting">
              <select id="iModel" :value="it.model ?? ''" :disabled="!editable" @change="setModel(($event.target as HTMLSelectElement).value)">
                <option value="">Default<template v-if="b.models.value"> ({{ b.models.value.default }})</template></option>
                <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
              </select>
              <span v-if="saved === 'model'" class="saved"><Icon name="check" :size="14" /> Saved</span>
              <span class="err">{{ modelError }}</span>
            </div>
            <label for="iContext">Context</label>
            <div class="setting">
              <select
                id="iContext"
                :value="contextValue"
                :disabled="!editable"
                aria-describedby="iContext-hint"
                @change="setContext(($event.target as HTMLSelectElement).value)"
              >
                <option value="">Default<template v-if="b.models.value"> ({{ b.models.value.lean_context ? "lean" : "full" }})</template></option>
                <option value="full">Full</option>
                <option value="lean">Lean</option>
              </select>
              <span v-if="saved === 'context'" class="saved"><Icon name="check" :size="14" /> Saved</span>
              <span class="err">{{ contextError }}</span>
              <span id="iContext-hint" class="hint">Lean: a short system prompt, only the allowed tools, no CLAUDE.md preloaded, a new session per step.</span>
            </div>
            <label for="iBudget">Budget, $</label>
            <div class="setting">
              <input
                id="iBudget"
                type="number"
                min="0.01"
                step="any"
                class="num"
                placeholder="No limit"
                :value="it.budget_usd ?? ''"
                :disabled="!editable"
                aria-describedby="iBudget-hint"
                @change="setBudget"
              />
              <span v-if="saved === 'budget'" class="saved"><Icon name="check" :size="14" /> Saved</span>
              <span class="err">{{ budgetError }}</span>
              <span id="iBudget-hint" class="hint" :class="{ err: overBudget(it.spent_usd, it.budget_usd) }">
                Spent {{ formatUsd(it.spent_usd) }}<template v-if="overBudget(it.spent_usd, it.budget_usd)"> · limit reached: raise it, then retry</template>
                · {{ it.kind === "epic" ? "covers the epic and everything below it" : "covers this item and its children" }}
              </span>
            </div>
          </div>
        </section>
      </div>

      <!-- Runs: each agent step, what it produced, and the story's transitions. -->
      <div v-show="b.itemTab.value === 'runs'" id="dpanel-runs" role="tabpanel" aria-labelledby="dtab-runs">
        <section class="section">
          <div class="section-title">Runs</div>
          <ol v-if="it.runs.length" class="runs">
            <li v-for="r in it.runs" :key="r.id" class="run" :class="{ current: sessionTask === r.task_id }">
              <div class="run-main">
                <span class="run-state">{{ r.state }}</span>
                <span class="chip" :class="r.status === 'succeeded' ? 'ok' : r.status === 'failed' ? 'bad' : r.status === 'running' ? 'accent' : ''">{{
                  r.status
                }}</span>
                <span class="muted">{{ r.role }}</span>
                <span v-if="r.verdict" class="muted">→ {{ r.verdict }}</span>
                <span class="spacer"></span>
                <span v-if="r.cost_usd != null" class="cost">{{ formatUsd(r.cost_usd) }}</span>
                <button
                  type="button"
                  class="btn sm"
                  :class="{ ghost: sessionTask !== r.task_id }"
                  :aria-pressed="sessionTask === r.task_id"
                  :title="`Session of task ${r.task_id.slice(0, 8)}`"
                  @click="toggleSession(r.task_id)"
                >
                  <Icon name="terminal" :size="14" /> {{ sessionTask === r.task_id ? "Hide" : "Session" }}
                </button>
              </div>
              <div v-if="reruns.get(r.id)" class="run-why subtle">ran again after {{ reruns.get(r.id) }}</div>
              <div v-if="r.error" class="run-error">{{ r.error }}</div>
            </li>
          </ol>
          <p v-else class="subtle">No agent has run yet.</p>
        </section>
        <section class="section">
          <div class="section-title">Artifacts</div>
          <details v-for="a in artifacts" :key="a.id" class="artifact" :open="a.name === 'error'">
            <summary :class="{ err: a.name === 'error' }">{{ a.name }}</summary>
            <pre :class="{ error: a.name === 'error' }">{{ a.content }}</pre>
          </details>
          <p v-if="!artifacts.length" class="subtle">None yet.</p>
        </section>
        <section class="section">
          <div class="section-title">History</div>
          <ol class="history">
            <li v-for="(e, n) in it.events" :key="n">
              <time :datetime="e.at" class="subtle">{{ new Date(e.at).toLocaleString() }}</time>
              <span>
                <!-- The verdict is the result of the step that ended (from_state), not the story's status. -->
                {{ e.from_state || "created" }}<template v-if="e.verdict"> ({{ e.verdict }})</template> → <b>{{ e.to_state }}</b>
                <span class="subtle"> · {{ e.actor }}</span>
              </span>
            </li>
          </ol>
        </section>
      </div>

      <!-- Workspace: the story's working copy, read-only git report plus a shell. -->
      <div v-show="b.itemTab.value === 'workspace'" id="dpanel-workspace" role="tabpanel" aria-labelledby="dtab-workspace">
        <template v-if="it.branch">
          <section class="section">
            <div class="section-title">
              Git
              <span class="spacer"></span>
              <a v-if="githubChanges" :href="githubChanges" target="_blank" rel="noopener" class="btn ghost sm">
                Changes on GitHub <Icon name="external" :size="12" />
              </a>
            </div>
            <div class="row">
              <button type="button" class="btn sm" :disabled="gitBusy" @click="loadGit()">
                <Icon name="refresh" :size="14" /> {{ git ? "Refresh report" : "Load report" }}
              </button>
              <button type="button" class="btn sm" :disabled="gitBusy" @click="loadGit(true)">Show diff</button>
              <span v-if="gitBusy" class="spin"></span>
            </div>
            <p v-if="gitError" class="err">{{ gitError }}</p>
            <template v-if="git">
              <p class="mono subtle git-head">
                {{ git.branch }} @ {{ git.head }}<template v-if="git.merge_in_progress"> · merge in progress</template>
              </p>
              <h4 class="git-sub">Commits</h4>
              <pre>{{ git.commits || "No commits on top of the base branch" }}</pre>
              <h4 class="git-sub">Status</h4>
              <pre>{{ git.status || "Clean" }}</pre>
              <h4 class="git-sub">Changes vs base</h4>
              <pre>{{ git.stat || "No changes" }}</pre>
              <template v-if="git.diff != null">
                <h4 class="git-sub">Diff</h4>
                <pre class="diff">{{ git.diff }}</pre>
              </template>
            </template>
          </section>
          <section class="section">
            <div class="section-title">Terminal</div>
            <TerminalBlock :item-id="it.id" />
          </section>
        </template>
        <div v-else class="empty">
          <Icon name="branch" :size="24" />
          <p>No working copy yet. The agent creates a branch when the story starts.</p>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 30;
  background: var(--scrim);
  animation: fade 0.18s var(--ease);
}
@keyframes fade {
  from {
    opacity: 0;
  }
}
/* Wide screens keep the board usable next to the drawer: clicking another card switches the story. */
@media (min-width: 1280px) {
  .scrim {
    display: none;
  }
}
.drawer {
  position: fixed;
  inset: 0 0 0 auto;
  z-index: 35;
  width: min(600px, 100%);
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border-left: 1px solid var(--border);
  box-shadow: var(--e3);
  animation: slide 0.2s var(--ease);
}
.drawer.wide {
  width: min(980px, 100%);
}
@keyframes slide {
  from {
    transform: translateX(24px);
    opacity: 0;
  }
}
.d-head {
  flex: none;
  padding: var(--s3) var(--s4) 0 var(--s5);
  border-bottom: 1px solid var(--border);
}
.d-bar {
  display: flex;
  align-items: center;
  gap: var(--s2);
  min-height: var(--control);
}
.d-title-row {
  display: flex;
  align-items: flex-start;
  gap: var(--s1);
  margin-top: var(--s2);
}
.d-title {
  font-size: var(--fs-xl);
  font-weight: 600;
  letter-spacing: -0.01em;
  overflow-wrap: anywhere;
}
.d-title:focus {
  outline: none;
}
.d-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s1) var(--s3);
  margin: var(--s2) 0 var(--s3);
  font-size: var(--fs-md);
  color: var(--text-muted);
}
.d-meta > span,
.pr-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.pr-link {
  font-weight: 500;
  text-decoration: none;
}
.d-tabs {
  border-bottom: none;
  margin-left: calc(-1 * var(--s3));
}
.d-body {
  flex: 1;
  overflow: auto;
  padding: var(--s5);
  overscroll-behavior: contain;
}
.d-body > [role="tabpanel"] > .section:first-child,
.session-section + [role="tabpanel"] > .section:first-child {
  margin-top: 0;
}
.session-section {
  padding-bottom: var(--s5);
  margin-bottom: var(--s5);
  border-bottom: 1px solid var(--border);
}
.note {
  margin: 0 0 var(--s3);
  padding: var(--s2) var(--s3);
  background: var(--info-subtle);
  color: var(--info);
  border-radius: var(--r-sm);
  font-size: var(--fs-md);
}
.decision {
  padding: var(--s4);
  background: var(--warn-subtle);
  border: 1px solid color-mix(in srgb, var(--warn) 30%, transparent);
  border-radius: var(--r-md);
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}
.decision-title {
  display: flex;
  align-items: center;
  gap: var(--s2);
  font-size: var(--fs-base);
  font-weight: 600;
  color: var(--warn);
}
.decision-q {
  margin: 0;
}
.decision textarea {
  width: 100%;
}
.decision pre.error {
  max-height: 180px;
}
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
.body-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.55;
}
.settings {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: var(--s3) var(--s4);
  align-items: start;
}
.settings > label {
  font-size: var(--fs-md);
  font-weight: 600;
  padding-top: 7px;
}
.setting {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s1) var(--s2);
}
.setting .hint {
  flex-basis: 100%;
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.saved {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--ok);
  font-size: var(--fs-sm);
}
.edit .row {
  margin-top: var(--s2);
}
.runs,
.history {
  list-style: none;
  margin: 0;
  padding: 0;
}
.run {
  padding: var(--s2) var(--s2) var(--s2) var(--s3);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  margin-bottom: var(--s2);
}
.run.current {
  border-color: var(--accent);
  background: var(--accent-subtle);
}
.run-main {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s2);
  font-size: var(--fs-md);
}
.run-state {
  font-weight: 600;
}
.cost {
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
}
.run-why {
  font-size: var(--fs-sm);
  margin-top: 2px;
}
.run-error {
  margin-top: var(--s1);
  color: var(--danger);
  font: var(--fs-sm) / 1.45 var(--font-mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.artifact {
  margin-bottom: var(--s2);
}
.artifact summary {
  cursor: pointer;
  font-weight: 600;
  font-size: var(--fs-md);
  padding: var(--s1) 0;
}
.artifact pre {
  margin-top: var(--s1);
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
.git-head {
  margin: var(--s3) 0 0;
}
.git-sub {
  margin: var(--s3) 0 var(--s1);
  font-size: var(--fs-md);
}
pre.diff {
  max-height: 60vh;
}
@media (max-width: 767px) {
  .drawer,
  .drawer.wide {
    width: 100%;
    border-left: none;
  }
  .d-head {
    padding: var(--s2) var(--s2) 0 var(--s4);
  }
  .d-body {
    padding: var(--s4);
  }
  .settings {
    grid-template-columns: 1fr;
    gap: var(--s1);
  }
  .settings > label {
    padding-top: var(--s2);
  }
  .history li {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
