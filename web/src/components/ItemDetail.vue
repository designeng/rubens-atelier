<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import type { ArtifactOut } from "../api/types";
import { useBoardStore, type ItemTab } from "../composables/useBoard";
import { rerunReasons } from "../format";
import { urgencyLabel } from "../urgency";
import ItemArtifacts from "./item/ItemArtifacts.vue";
import ItemDecision from "./item/ItemDecision.vue";
import ItemEditForm from "./item/ItemEditForm.vue";
import ItemHistory from "./item/ItemHistory.vue";
import ItemNow from "./item/ItemNow.vue";
import ItemRuns from "./item/ItemRuns.vue";
import ItemSettings from "./item/ItemSettings.vue";
import ItemTerminal from "./item/ItemTerminal.vue";
import SessionView from "./SessionView.vue";
import Icon from "./ui/Icon.vue";
import Popover from "./ui/Popover.vue";

// The drawer of one story: header, tabs and the session; the panels are the components in item/.
const b = useBoardStore();
const it = computed(() => b.item.value);
const req = computed(() => it.value?.open_request ?? null);

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

const pendingComments = computed(() => artifacts.value.filter((a) => a.name.startsWith("human:comment:") && !a.run_id));

const STATUS: Record<string, { label: string; tone: string }> = {
  backlog: { label: "Backlog", tone: "" },
  working: { label: "In progress", tone: "accent" },
  needs_human: { label: "Needs you", tone: "warn" },
  done: { label: "Done", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
};
const status = computed(() => (it.value ? STATUS[it.value.column] : null));

// Edit the story in place (the form is ItemEditForm); the header menu and the pencil open it.
const editing = ref(false);
const editable = computed(() => it.value?.column !== "done" && it.value?.column !== "failed");
watch(
  () => it.value?.id,
  () => (editing.value = false),
);
function startEdit(): void {
  if (!it.value) return;
  editing.value = true;
  b.setItemTab("overview");
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
onUnmounted(() => document.removeEventListener("keydown", onKey));
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
        <span v-if="it.external" class="chip warn" title="Untrusted text: confirm before starting">
          External author<template v-if="it.author"> @{{ it.author }}</template>
        </span>
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
        <ItemEditForm v-if="editing" :item="it" @close="editing = false" />
        <ItemDecision
          :request="req"
          :latest-error="latestError"
          :attention="it.attention?.text"
          :busy="busy"
          @answer="(id, option, comment) => run(() => b.answer(id, option, comment))"
        />
        <ItemNow
          v-if="it.progress"
          :item-id="it.id"
          :progress="it.progress"
          :session-task="sessionTask"
          :pending-comments="pendingComments"
          @toggle-session="toggleSession"
        />

        <section v-if="!editing" class="section">
          <div class="section-title">Description</div>
          <div v-if="it.body" class="body-text">{{ it.body }}</div>
          <p v-else class="subtle">No description.</p>
        </section>

        <ItemSettings :item="it" :editable="editable" />
      </div>

      <!-- Runs: each agent step, what it produced, and the story's transitions. -->
      <div v-show="b.itemTab.value === 'runs'" id="dpanel-runs" role="tabpanel" aria-labelledby="dtab-runs">
        <ItemRuns :runs="it.runs" :reruns="reruns" :session-task="sessionTask" @toggle-session="toggleSession" />
        <ItemArtifacts :artifacts="artifacts" />
        <ItemHistory :events="it.events" />
      </div>

      <!-- Workspace: the story's working copy, read-only git report plus a shell. -->
      <div v-show="b.itemTab.value === 'workspace'" id="dpanel-workspace" role="tabpanel" aria-labelledby="dtab-workspace">
        <ItemTerminal v-if="it.branch" :item="it" />
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
/* The sections are the root elements of the panel components, so :deep reaches them. */
.d-body > [role="tabpanel"] > :deep(.section:first-child),
.session-section + [role="tabpanel"] > :deep(.section:first-child) {
  margin-top: 0;
}
.session-section {
  padding-bottom: var(--s5);
  margin-bottom: var(--s5);
  border-bottom: 1px solid var(--border);
}
.body-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.55;
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
}
</style>
