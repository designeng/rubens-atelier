<script setup lang="ts">
import { computed, ref } from "vue";
import { useBoardStore } from "../composables/useBoard";
import { loadTheme, saveTheme, THEMES, type Theme } from "../theme";

const emit = defineEmits<{ settings: []; workspaces: []; github: []; "new-project": []; "new-item": [title: string] }>();
const b = useBoardStore();
const title = ref("");
const theme = ref<Theme>(loadTheme());

const liveInstallations = computed(() => b.github.value.installations.filter((i) => !i.suspended));
const githubLabel = computed(() =>
  !b.github.value.configured
    ? "GitHub: not configured"
    : liveInstallations.value.length
      ? `GitHub ✓ ${liveInstallations.value.map((i) => i.account).join(", ")}`
      : "Connect GitHub",
);
const schedulerText = computed(() => {
  const sc = b.scheduler.value;
  if (!sc) return "";
  const why = b.isPaused() ? "queue paused" : sc.reason;
  return `agents ${sc.active.length}/${sc.capacity} · queued ${sc.ready.length}` + (sc.reason !== "ok" ? ` · ${why}` : "");
});

function onLimit(key: "max_agents" | "max_agents_per_project", e: Event) {
  const input = e.target as HTMLInputElement;
  const n = input.valueAsNumber;
  if (Number.isInteger(n) && n >= 1) b.setLimits({ [key]: n });
  else input.value = String(b.scheduler.value?.limits[key] ?? "");
}
function onToken(e: Event) {
  b.setToken((e.target as HTMLInputElement).value);
}
function onProject(e: Event) {
  importText.value = "";
  b.selectProject((e.target as HTMLSelectElement).value);
}
function onTheme(e: Event) {
  theme.value = (e.target as HTMLSelectElement).value as Theme;
  saveTheme(theme.value);
}
function addItem() {
  if (b.slug.value) emit("new-item", title.value.trim());
}

const importing = ref(false);
const importText = ref("");
async function importIssues() {
  importing.value = true;
  importText.value = "";
  try {
    const res = await b.importIssues();
    importText.value = `imported ${res.imported.length}, skipped ${res.skipped}`;
  } catch (e) {
    b.fail(e);
  } finally {
    importing.value = false;
  }
}

defineExpose({ clearTitle: () => (title.value = "") });
</script>

<template>
  <header>
    <h1>Rubens Atelier</h1>
    <input type="password" placeholder="API token" size="14" :value="b.token.value" @change="onToken" />
    <select :value="b.slug.value" @change="onProject">
      <option v-for="p in b.projects.value" :key="p.id" :value="p.slug">{{ p.slug }}</option>
    </select>
    <button title="Project settings" :disabled="!b.slug.value" @click="emit('settings')">Settings</button>
    <button title="Working copies on disk: see them and clear the finished ones" @click="emit('workspaces')">
      Workspaces
    </button>
    <button
      title="GitHub App connection"
      :class="{ primary: b.github.value.configured && !liveInstallations.length }"
      @click="emit('github')"
    >
      {{ githubLabel }}
    </button>
    <button @click="emit('new-project')">+ project</button>
    <input v-model="title" placeholder="New story title" size="28" @keydown.enter="addItem" />
    <button class="primary" :disabled="!b.slug.value" @click="addItem">Add &amp; start</button>
    <button
      title="Add the repo's open GitHub issues to the backlog (already imported ones are skipped)"
      :disabled="!b.slug.value || importing"
      @click="importIssues"
    >
      Import issues
    </button>
    <span v-if="importText" class="muted">{{ importText }}</span>
    <span class="muted">{{ schedulerText }}</span>
    <template v-if="b.scheduler.value">
      <label class="muted" title="Agents at once (applies right away; running agents above a lowered limit finish)">
        slots
        <input
          type="number"
          min="1"
          max="64"
          class="num"
          :value="b.scheduler.value.limits.max_agents"
          @change="onLimit('max_agents', $event)"
        />
      </label>
      <label class="muted" title="Agents at once per project">
        per project
        <input
          type="number"
          min="1"
          max="64"
          class="num"
          :value="b.scheduler.value.limits.max_agents_per_project"
          @change="onLimit('max_agents_per_project', $event)"
        />
      </label>
      <label
        class="muted"
        title="Keep the slot limit while a subscription window is close to full (otherwise it drops to one agent). Only a window Claude reports as rejected still stops dispatch."
      >
        <input
          type="checkbox"
          :checked="b.scheduler.value.limits.force"
          @change="b.setLimits({ force: ($event.target as HTMLInputElement).checked })"
        />
        force
      </label>
    </template>
    <select title="Theme" :value="theme" @change="onTheme">
      <option v-for="t in THEMES" :key="t.value" :value="t.value">{{ t.label }}</option>
    </select>
    <button
      v-if="b.scheduler.value"
      :class="{ primary: b.isPaused() }"
      :title="
        b.isPaused()
          ? 'Agent steps are not dispatched. Resume to let queued steps start.'
          : 'Stop dispatching new agent steps; running ones finish'
      "
      @click="b.togglePause()"
    >
      {{ b.isPaused() ? "Resume queue" : "Pause queue" }}
    </button>
    <span id="err">{{ b.error.value }}</span>
  </header>
</template>
