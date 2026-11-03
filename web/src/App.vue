<script setup lang="ts">
import { onMounted, onUnmounted, ref, watchEffect } from "vue";
import AppHeader from "./components/AppHeader.vue";
import BoardColumns from "./components/BoardColumns.vue";
import GithubDialog from "./components/GithubDialog.vue";
import ItemDetail from "./components/ItemDetail.vue";
import MemoryDialog from "./components/MemoryDialog.vue";
import NewItemDialog from "./components/NewItemDialog.vue";
import NewProjectDialog from "./components/NewProjectDialog.vue";
import SettingsDialog from "./components/SettingsDialog.vue";
import TokenDialog from "./components/TokenDialog.vue";
import Toasts from "./components/ui/Toasts.vue";
import WorkspacesDialog from "./components/WorkspacesDialog.vue";
import { provideBoard, useBoard } from "./composables/useBoard";

const b = useBoard();
provideBoard(b);

const settingsDialog = ref<InstanceType<typeof SettingsDialog>>();
const memoryDialog = ref<InstanceType<typeof MemoryDialog>>();
const workspacesDialog = ref<InstanceType<typeof WorkspacesDialog>>();
const githubDialog = ref<InstanceType<typeof GithubDialog>>();
const projectDialog = ref<InstanceType<typeof NewProjectDialog>>();
const itemDialog = ref<InstanceType<typeof NewItemDialog>>();
const tokenDialog = ref<InstanceType<typeof TokenDialog>>();

// The tab names the open story, so browser history and tabs are told apart.
watchEffect(() => {
  document.title = b.item.value ? `${b.item.value.title} · Rubens Atelier` : "Rubens Atelier";
});

// N opens the New story dialog from anywhere on the board, except while typing or in a dialog.
function onKey(e: KeyboardEvent): void {
  if (e.key !== "n" || e.metaKey || e.ctrlKey || e.altKey || !b.slug.value) return;
  const t = e.target as HTMLElement;
  if (t.closest("input, textarea, select, [contenteditable]") || document.querySelector("dialog[open]")) return;
  e.preventDefault();
  itemDialog.value?.open();
}
onMounted(() => document.addEventListener("keydown", onKey));
onUnmounted(() => document.removeEventListener("keydown", onKey));
</script>

<template>
  <AppHeader
    @settings="settingsDialog?.open()"
    @memory="memoryDialog?.open()"
    @workspaces="workspacesDialog?.open()"
    @github="githubDialog?.open()"
    @new-project="projectDialog?.open()"
    @new-item="itemDialog?.open()"
    @token="tokenDialog?.open()"
  />
  <div v-if="b.isPaused() && !b.unauthorized.value" class="banner warn" role="status">
    <strong>Queue paused.</strong>
    <span>Running agents finish; no new agent step starts.</span>
    <button type="button" class="btn sm" @click="b.togglePause()">Resume queue</button>
  </div>
  <BoardColumns @new-project="projectDialog?.open()" @new-item="itemDialog?.open()" @token="tokenDialog?.open()" />
  <ItemDetail />
  <SettingsDialog ref="settingsDialog" />
  <MemoryDialog ref="memoryDialog" />
  <WorkspacesDialog ref="workspacesDialog" />
  <GithubDialog ref="githubDialog" />
  <NewProjectDialog ref="projectDialog" />
  <NewItemDialog ref="itemDialog" />
  <TokenDialog ref="tokenDialog" />
  <Toasts />
</template>

<style scoped>
.banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s2) var(--s3);
  padding: var(--s2) var(--s4);
  font-size: var(--fs-md);
  border-bottom: 1px solid var(--border);
}
.banner.warn {
  background: var(--warn-subtle);
  color: var(--warn);
}
.banner .btn {
  margin-left: auto;
}
</style>
