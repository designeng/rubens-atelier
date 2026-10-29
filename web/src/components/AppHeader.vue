<script setup lang="ts">
import { computed, ref } from "vue";
import { useBoardStore } from "../composables/useBoard";

const emit = defineEmits<{ settings: []; github: []; "new-project": []; "new-item": [title: string] }>();
const b = useBoardStore();
const title = ref("");

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
  return `agents ${sc.active.length}/${sc.capacity} · queued ${sc.ready.length}` + (sc.reason !== "ok" ? ` · ${sc.reason}` : "");
});

function onToken(e: Event) {
  b.setToken((e.target as HTMLInputElement).value);
}
function onProject(e: Event) {
  importText.value = "";
  b.selectProject((e.target as HTMLSelectElement).value);
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
    <h1>Factory</h1>
    <input type="password" placeholder="API token" size="14" :value="b.token.value" @change="onToken" />
    <select :value="b.slug.value" @change="onProject">
      <option v-for="p in b.projects.value" :key="p.id" :value="p.slug">{{ p.slug }}</option>
    </select>
    <button title="Project settings" :disabled="!b.slug.value" @click="emit('settings')">Settings</button>
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
    <button v-if="b.scheduler.value" @click="b.togglePause()">{{ b.isPaused() ? "Resume" : "Pause" }}</button>
    <span id="err">{{ b.error.value }}</span>
  </header>
</template>
