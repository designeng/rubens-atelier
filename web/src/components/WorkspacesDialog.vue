<script setup lang="ts">
import { computed, ref } from "vue";
import type { PruneResult, WorkspaceOut, WorkspacePhase } from "../api/types";
import { useBoardStore } from "../composables/useBoard";
import { elapsed, formatBytes } from "../format";

const PHASES: WorkspacePhase[] = ["active", "idle", "finished", "failed", "orphan"];

const b = useBoardStore();
const dlg = ref<HTMLDialogElement>();
const rows = ref<WorkspaceOut[]>([]);
const phase = ref<WorkspacePhase | "">("");
const project = ref("");
const hours = ref(24);
const includeFailed = ref(false);
const preview = ref<PruneResult | null>(null);
const err = ref("");
const busy = ref(false);
let loads = 0;

const total = computed(() => rows.value.reduce((n, r) => n + (r.size_bytes ?? 0), 0));
const measured = computed(() => rows.value.every((r) => r.size_bytes !== null));
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const pruneBody = (dry_run: boolean) => ({
  older_than_hours: Math.max(0, Number(hours.value) || 0),
  include_failed: includeFailed.value,
  dry_run,
});

// The list appears at once, without sizes; the sizes follow when the disk has been walked.
async function load() {
  const mine = ++loads;
  const filter = { phase: phase.value, project: project.value };
  try {
    const quick = await b.api.listWorkspaces({ ...filter, size: false });
    if (mine !== loads) return;
    rows.value = quick;
    err.value = "";
    const sized = await b.api.listWorkspaces(filter);
    if (mine === loads) rows.value = sized;
  } catch (e) {
    if (mine === loads) err.value = message(e);
  }
}

function open() {
  preview.value = null;
  err.value = "";
  dlg.value?.showModal();
  load();
}

async function guarded(work: () => Promise<void>) {
  busy.value = true;
  try {
    await work();
  } catch (e) {
    err.value = message(e);
  } finally {
    busy.value = false;
  }
}

function remove(w: WorkspaceOut) {
  const what = w.item?.title ?? w.task?.prompt ?? w.id;
  if (!confirm(`Delete the working copy of "${what}"?`)) return;
  return guarded(async () => {
    await b.api.deleteWorkspace(w.id);
    preview.value = null;
    await load();
  });
}

const check = () =>
  guarded(async () => {
    preview.value = await b.api.pruneWorkspaces(pruneBody(true));
  });

function prune() {
  if (!preview.value || !confirm(`Delete ${preview.value.removed.length} working copies?`)) return;
  return guarded(async () => {
    const done = await b.api.pruneWorkspaces(pruneBody(false));
    preview.value = null;
    await load();
    if (done.skipped.length) err.value = `Not removed: ${done.skipped.map((s) => `${s.id}: ${s.error}`).join("; ")}`;
  });
}

defineExpose({ open });
</script>

<template>
  <dialog ref="dlg" class="workspaces" @close="loads++">
    <h3 style="margin-top: 0">Working copies</h3>
    <div class="row">
      <select v-model="phase" aria-label="Phase" @change="load">
        <option value="">any phase</option>
        <option v-for="p in PHASES" :key="p" :value="p">{{ p }}</option>
      </select>
      <select v-model="project" aria-label="Project" @change="load">
        <option value="">any project</option>
        <option v-for="p in b.projects.value" :key="p.id" :value="p.slug">{{ p.slug }}</option>
      </select>
      <button :disabled="busy" @click="load">Refresh</button>
      <span class="muted">
        {{ rows.length }} copies<template v-if="measured">, {{ formatBytes(total) }}</template>
        <template v-else>, measuring…</template>
      </span>
    </div>
    <div class="ws-scroll">
      <table>
        <thead>
          <tr><th>Belongs to</th><th>Phase</th><th>Size</th><th>Changed</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="w in rows" :key="w.id">
            <td :title="w.path">
              <template v-if="w.item">{{ w.item.project }} · {{ w.item.title }}</template>
              <template v-else-if="w.task">task · {{ w.task.prompt.slice(0, 80) }}</template>
              <template v-else>{{ w.id }}</template>
              <div class="muted">{{ w.reason }}</div>
            </td>
            <td>{{ w.phase }}</td>
            <td>{{ w.size_bytes === null ? "…" : formatBytes(w.size_bytes) }}</td>
            <td>{{ elapsed(w.modified_at) }} ago</td>
            <td>
              <button :disabled="busy || !w.removable" :title="w.removable ? 'Delete the working copy' : w.reason" @click="remove(w)">
                Delete
              </button>
            </td>
          </tr>
          <tr v-if="!rows.length"><td colspan="5" class="muted">No working copies</td></tr>
        </tbody>
      </table>
    </div>
    <h4>Clean up</h4>
    <div class="row">
      <label>
        Not changed for
        <input v-model.number="hours" type="number" min="0" style="width: 5em" @input="preview = null" /> hours
      </label>
      <label>
        <input v-model="includeFailed" type="checkbox" @change="preview = null" />
        also failed and cancelled
      </label>
      <button :disabled="busy" @click="check">Preview</button>
      <button class="primary" :disabled="busy || !preview || !preview.removed.length" @click="prune">Prune</button>
    </div>
    <div v-if="preview" class="muted">
      {{ preview.removed.length }} copies to remove, {{ formatBytes(preview.freed_bytes) }} to free. Finished and orphaned
      copies go<template v-if="includeFailed">, failed ones too</template>.
    </div>
    <div class="row" style="margin-top: 14px">
      <button @click="dlg?.close()">Close</button>
      <span class="err">{{ err }}</span>
    </div>
  </dialog>
</template>

<style scoped>
.workspaces {
  width: min(900px, 95vw);
}
.ws-scroll {
  max-height: 50vh;
  overflow: auto;
  margin: 10px 0;
}
.ws-scroll th,
.ws-scroll td {
  text-align: left;
  padding: 4px 8px;
  vertical-align: top;
}
.row label {
  margin: 0;
}
.row input[type="checkbox"] {
  width: auto;
}
</style>
