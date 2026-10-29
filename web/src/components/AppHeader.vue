<script setup lang="ts">
import { computed, ref } from "vue";
import type { SchedulerLimits } from "../api/types";
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

function onLimit(key: keyof SchedulerLimits, e: Event) {
  const input = e.target as HTMLInputElement;
  const n = input.valueAsNumber;
  if (Number.isInteger(n) && n >= 1) b.setLimits({ [key]: n });
  else input.value = String(b.scheduler.value?.limits[key] ?? "");
}
function onToken(e: Event) {
  b.setToken((e.target as HTMLInputElement).value);
}
function onProject(e: Event) {
  b.selectProject((e.target as HTMLSelectElement).value);
}
function addItem() {
  if (b.slug.value) emit("new-item", title.value.trim());
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
    </template>
    <button v-if="b.scheduler.value" @click="b.togglePause()">{{ b.isPaused() ? "Resume" : "Pause" }}</button>
    <span id="err">{{ b.error.value }}</span>
  </header>
</template>
