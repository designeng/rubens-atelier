<script setup lang="ts">
import { computed, ref } from "vue";
import { useBoardStore } from "../composables/useBoard";
import { overBudget, spendText } from "../format";
import { loadTheme, saveTheme, THEMES, type Theme } from "../theme";
import { toasts } from "../toasts";
import Icon from "./ui/Icon.vue";
import Popover from "./ui/Popover.vue";

const emit = defineEmits<{ settings: []; memory: []; workspaces: []; github: []; "new-project": []; "new-item": []; token: [] }>();
const b = useBoardStore();
const theme = ref<Theme>(loadTheme());

const liveInstallations = computed(() => b.github.value.installations.filter((i) => !i.suspended));
// The app is set up on the server but no account has installed it: worth a visible nudge.
const needsGithub = computed(() => b.github.value.configured && !liveInstallations.value.length);
const githubHint = computed(() =>
  !b.github.value.configured ? "not configured" : liveInstallations.value.map((i) => i.account).join(", ") || "not connected",
);
const current = computed(() => b.projects.value.find((p) => p.slug === b.slug.value));
const projectMoney = computed(() => (current.value ? spendText(current.value.spent_usd, current.value.budget_usd) : null));

const sc = computed(() => b.scheduler.value);
const noWorker = computed(() => !!sc.value && !sc.value.workers.length);
const capacityTone = computed(() => {
  const s = sc.value;
  if (!s) return "";
  if (noWorker.value) return "err";
  if (b.isPaused() || s.reason !== "ok") return "warn";
  return s.active.length ? "ok" : "";
});
const capacityWhy = computed(() => {
  const s = sc.value;
  if (!s) return "";
  if (noWorker.value) return "No Celery worker is running: agent steps get a slot but never start. Start one with `celery -A app.celery_app worker`.";
  if (b.isPaused()) return "The queue is paused: no new agent step starts.";
  return s.reason === "ok" ? "Dispatching normally." : s.reason;
});

function onLimit(key: "max_agents" | "max_agents_per_project", e: Event) {
  const input = e.target as HTMLInputElement;
  const n = input.valueAsNumber;
  if (Number.isInteger(n) && n >= 1) b.setLimits({ [key]: n });
  else input.value = String(b.scheduler.value?.limits[key] ?? "");
}

// The button cycles Auto → Day → Midnight; its icon shows the current one.
const THEME_ICON = { auto: "monitor", day: "sun", midnight: "moon" } as const;
const themeLabel = computed(() => {
  const i = THEMES.findIndex((t) => t.value === theme.value);
  const next = THEMES[(i + 1) % THEMES.length];
  return `Theme: ${THEMES[i].label}. Switch to ${next.label}`;
});
function cycleTheme() {
  const i = THEMES.findIndex((t) => t.value === theme.value);
  theme.value = THEMES[(i + 1) % THEMES.length].value;
  saveTheme(theme.value);
}

const importing = ref(false);
async function importIssues() {
  importing.value = true;
  try {
    const res = await b.importIssues();
    const n = res.imported.length;
    toasts.push(
      n ? `Imported ${n} issue${n === 1 ? "" : "s"}${res.skipped ? `, skipped ${res.skipped} already on the board` : ""}` : "No new open issues to import",
      "success",
    );
  } catch (e) {
    b.fail(e);
  } finally {
    importing.value = false;
  }
}
</script>

<template>
  <header class="topbar">
    <h1 class="brand">Rubens Atelier</h1>

    <Popover v-if="b.projects.value.length" class="project" label="Switch project" menu button-class="btn ghost project-btn">
      <template #button>
        <span class="project-name">{{ b.slug.value }}</span>
        <span
          v-if="projectMoney"
          class="project-money"
          :class="{ err: current && overBudget(current.spent_usd, current.budget_usd) }"
          title="Spent on agent runs / project budget"
          >{{ projectMoney }}</span
        >
        <Icon name="chevronDown" :size="14" />
      </template>
      <div class="menu-label">Projects</div>
      <button
        v-for="p in b.projects.value"
        :key="p.id"
        type="button"
        class="menu-item"
        role="menuitemradio"
        :aria-checked="p.slug === b.slug.value"
        @click="b.selectProject(p.slug)"
      >
        <Icon :name="p.slug === b.slug.value ? 'check' : 'folder'" />
        {{ p.slug }}
        <span class="hint">{{ p.repo }}</span>
      </button>
      <div class="menu-sep" role="separator"></div>
      <button type="button" class="menu-item" role="menuitem" :disabled="!b.slug.value" @click="emit('settings')">
        <Icon name="sliders" /> Project settings…
      </button>
      <button type="button" class="menu-item" role="menuitem" :disabled="!b.slug.value" @click="emit('memory')">
        <Icon name="book" /> Project memory…
        <span v-if="b.proposedNotes.value" class="hint">{{ b.proposedNotes.value }} to review</span>
      </button>
      <button type="button" class="menu-item" role="menuitem" @click="emit('new-project')"><Icon name="plus" /> New project…</button>
    </Popover>

    <span class="spacer"></span>

    <button
      v-if="b.proposedNotes.value"
      type="button"
      class="btn sm chip-btn accent"
      :title="`Agents proposed notes for the memory of ${b.slug.value}; they wait for your review`"
      @click="emit('memory')"
    >
      <Icon name="book" :size="14" /> {{ b.proposedNotes.value }}<span class="wide-only">
        {{ b.proposedNotes.value === 1 ? "note" : "notes" }} to review</span
      >
    </button>

    <button v-if="needsGithub" type="button" class="btn sm chip-btn warn" @click="emit('github')">
      <Icon name="plug" :size="14" /> Connect GitHub
    </button>

    <template v-if="sc">
      <Popover class="capacity" label="Agent capacity" align="end" button-class="btn ghost capacity-btn">
        <template #button>
          <span class="dot" :class="capacityTone"></span>
          <span><b>{{ sc.active.length }}</b>/{{ sc.capacity }}<span class="wide-only"> agents</span></span>
          <span class="subtle">·</span>
          <span><b>{{ sc.ready.length }}</b><span class="wide-only"> queued</span></span>
          <Icon name="chevronDown" :size="14" />
        </template>
        <div class="capacity-panel">
          <div class="menu-label">Agent capacity</div>
          <p class="cap-why" :class="capacityTone">{{ capacityWhy }}</p>
          <div class="cap-row">
            <span>Workers</span>
            <span :class="{ 'cap-err': noWorker }">{{ noWorker ? "none online" : `${sc.workers.length} online` }}</span>
          </div>
          <div class="cap-row">
            <label for="capMax">Agents at once</label>
            <input
              id="capMax"
              type="number"
              min="1"
              max="64"
              class="num"
              :value="sc.limits.max_agents"
              title="Applies right away; running agents above a lowered limit finish"
              @change="onLimit('max_agents', $event)"
            />
          </div>
          <div class="cap-row">
            <label for="capProject">Per project</label>
            <input
              id="capProject"
              type="number"
              min="1"
              max="64"
              class="num"
              :value="sc.limits.max_agents_per_project"
              @change="onLimit('max_agents_per_project', $event)"
            />
          </div>
          <label class="check cap-force">
            <input
              type="checkbox"
              :checked="sc.limits.force"
              @change="b.setLimits({ force: ($event.target as HTMLInputElement).checked })"
            />
            <span>
              Keep the limit near a subscription cap
              <span class="hint"
                >Otherwise it drops to one agent while a window is close to full. A window Claude reports as rejected still
                stops dispatch.</span
              >
            </span>
          </label>
          <button type="button" class="btn" :class="{ primary: b.isPaused() }" @click="b.togglePause()">
            <Icon :name="b.isPaused() ? 'play' : 'pause'" /> {{ b.isPaused() ? "Resume queue" : "Pause queue" }}
          </button>
        </div>
      </Popover>
      <button
        type="button"
        class="icon-btn ghost pause"
        :class="{ paused: b.isPaused() }"
        :aria-label="b.isPaused() ? 'Resume queue' : 'Pause queue'"
        :title="b.isPaused() ? 'Resume queue: let queued agent steps start' : 'Pause queue: stop dispatching new agent steps; running ones finish'"
        @click="b.togglePause()"
      >
        <Icon :name="b.isPaused() ? 'play' : 'pause'" />
      </button>
    </template>

    <button type="button" class="icon-btn ghost theme" :aria-label="themeLabel" :title="themeLabel" @click="cycleTheme">
      <Icon :name="THEME_ICON[theme]" />
    </button>

    <Popover class="more" label="More" menu align="end" button-class="icon-btn ghost">
      <template #button><Icon name="more" /></template>
      <button type="button" class="menu-item" role="menuitem" @click="emit('workspaces')"><Icon name="folder" /> Working copies…</button>
      <button type="button" class="menu-item" role="menuitem" @click="emit('github')">
        <Icon name="plug" /> GitHub… <span class="hint">{{ githubHint }}</span>
      </button>
      <button type="button" class="menu-item" role="menuitem" @click="emit('token')">
        <Icon name="key" /> API token… <span class="hint">{{ b.token.value ? "set" : "not set" }}</span>
      </button>
      <div class="menu-sep" role="separator"></div>
      <div class="menu-label">Shortcuts</div>
      <div class="menu-item" aria-disabled="true"><kbd>N</kbd> New story <span class="hint">Esc closes a story</span></div>
    </Popover>

    <div class="btn-group new">
      <button
        type="button"
        class="btn primary"
        :disabled="!b.slug.value"
        title="New story (N)"
        @click="emit('new-item')"
      >
        <Icon name="plus" /> New story
      </button>
      <Popover label="More ways to add stories" menu align="end" button-class="btn primary split">
        <template #button><Icon name="chevronDown" :size="14" /></template>
        <button type="button" class="menu-item" role="menuitem" :disabled="!b.slug.value || importing" @click="importIssues">
          <Icon name="download" /> Import open GitHub issues
          <span class="hint">{{ importing ? "importing…" : "skips imported" }}</span>
        </button>
      </Popover>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: relative;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: var(--s2);
  min-height: var(--header-h);
  padding: var(--s2) var(--s4);
  background: color-mix(in srgb, var(--bg) 82%, transparent);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border);
}
.brand {
  font: 700 34px/1 var(--font-brand);
  color: var(--brand);
  margin: -4px var(--s2) -2px 0;
  white-space: nowrap;
  letter-spacing: 0.01em;
}
:deep(.project-btn) {
  gap: var(--s2);
  max-width: 340px;
}
.project-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
}
.project-money {
  color: var(--text-muted);
  font-weight: 400;
  font-variant-numeric: tabular-nums;
}
:deep(.capacity-btn) {
  gap: 6px;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
}
.dot {
  color: var(--text-subtle);
}
.dot.ok {
  color: var(--ok);
}
.dot.warn {
  color: var(--warn);
}
.dot.err {
  color: var(--danger);
}
.capacity-panel {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  padding: 0 var(--s2) var(--s2);
  width: 300px;
  max-width: 100%;
}
.cap-why {
  margin: 0;
  font-size: var(--fs-md);
  color: var(--text-muted);
}
.cap-why.warn {
  color: var(--warn);
}
.cap-why.err,
.cap-err {
  color: var(--danger);
}
.cap-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s3);
}
.cap-force {
  font-size: var(--fs-md);
}
.cap-force .hint {
  display: block;
  margin-top: 2px;
  color: var(--text-muted);
  font-size: var(--fs-sm);
}
.pause.paused {
  color: var(--warn);
  background: var(--warn-subtle);
}
.chip-btn.warn {
  color: var(--warn);
  background: var(--warn-subtle);
  border-color: transparent;
}
.chip-btn.accent {
  color: var(--accent);
  background: var(--accent-subtle);
  border-color: transparent;
}
:deep(.split) {
  padding: 0 var(--s2);
}
kbd {
  display: inline-block;
  min-width: 20px;
  padding: 1px 5px;
  border: 1px solid var(--border-strong);
  border-bottom-width: 2px;
  border-radius: 4px;
  text-align: center;
  font-size: var(--fs-xs);
}
@media (max-width: 1023px) {
  .wide-only,
  .project-money {
    display: none;
  }
  .brand {
    font-size: 28px;
  }
  .project {
    min-width: 0;
  }
}
/* Two rows on phones: brand and icon actions on top, project, capacity and New story under them. */
@media (max-width: 767px) {
  .topbar {
    flex-wrap: wrap;
    padding: var(--s2) var(--s3);
    row-gap: var(--s1);
  }
  .brand {
    font-size: 28px;
    order: 0;
    flex: 1;
  }
  /* A zero-height full-width break between the two rows. */
  .spacer {
    order: 2;
    flex-basis: 100%;
    height: 0;
  }
  .chip-btn,
  .pause,
  .theme,
  .more {
    order: 1;
  }
  .project {
    order: 3;
    flex: 1;
    min-width: 0;
  }
  :deep(.project-btn) {
    max-width: 100%;
    padding-left: var(--s1);
  }
  .capacity {
    order: 4;
  }
  .new {
    order: 5;
  }
}
</style>
