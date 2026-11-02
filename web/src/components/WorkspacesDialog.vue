<script setup lang="ts">
import { computed, ref } from "vue";
import type { PruneResult, WorkspaceOut, WorkspacePhase } from "../api/types";
import { useBoardStore } from "../composables/useBoard";
import { elapsed, formatBytes } from "../format";
import AppDialog from "./ui/AppDialog.vue";
import Icon from "./ui/Icon.vue";

const PHASES: WorkspacePhase[] = ["orphan", "finished", "failed", "idle", "busy"];
const PHASE_TONE: Record<string, string> = { orphan: "warn", finished: "ok", failed: "bad", idle: "", busy: "accent" };

const b = useBoardStore();
const dlg = ref<InstanceType<typeof AppDialog>>();
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
const note = ref("");
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
    rows.value = quick.workspaces;
    err.value = "";
    const sized = await b.api.listWorkspaces(filter);
    if (mine === loads) rows.value = sized.workspaces;
  } catch (e) {
    if (mine === loads) err.value = message(e);
  }
}

function open() {
  preview.value = null;
  err.value = "";
  note.value = "";
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
    await settle("Deleting…");
  });
}

// The worker owns the volume and deletes after the request returned: show the list again once it had a moment.
async function settle(text: string) {
  note.value = text;
  await load();
  setTimeout(async () => {
    await load();
    note.value = "";
  }, 2000);
}

const check = () =>
  guarded(async () => {
    preview.value = await b.api.pruneWorkspaces(pruneBody(true));
  });

function prune() {
  const n = preview.value?.would_remove?.length ?? 0;
  if (!n || !confirm(`Delete ${n} working copies?`)) return;
  return guarded(async () => {
    await b.api.pruneWorkspaces(pruneBody(false));
    preview.value = null;
    await settle(`Deleting ${n} copies…`);
  });
}

defineExpose({ open });
</script>

<template>
  <AppDialog ref="dlg" title="Working copies" size="lg" @close="loads++">
    <div class="row toolbar">
      <select v-model="phase" aria-label="Phase" @change="load">
        <option value="">Any phase</option>
        <option v-for="p in PHASES" :key="p" :value="p">{{ p }}</option>
      </select>
      <select v-model="project" aria-label="Project" @change="load">
        <option value="">Any project</option>
        <option v-for="p in b.projects.value" :key="p.id" :value="p.slug">{{ p.slug }}</option>
      </select>
      <button type="button" class="icon-btn" :disabled="busy" aria-label="Refresh" title="Refresh" @click="load">
        <Icon name="refresh" />
      </button>
      <span class="spacer"></span>
      <span class="muted summary">
        {{ rows.length }} {{ rows.length === 1 ? "copy" : "copies" }}<template v-if="measured"> · {{ formatBytes(total) }}</template>
        <template v-else> · measuring…</template>
      </span>
    </div>
    <div class="ws-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>Belongs to</th>
            <th>Phase</th>
            <th class="num">Size</th>
            <th class="num">Changed</th>
            <th><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="w in rows" :key="w.id">
            <td :title="w.path">
              <template v-if="w.item">{{ w.item.project }} · {{ w.item.title }}</template>
              <template v-else-if="w.task">task · {{ w.task.prompt.slice(0, 80) }}</template>
              <template v-else>{{ w.id }}</template>
              <div class="subtle reason">{{ w.reason }}</div>
            </td>
            <td><span class="chip" :class="PHASE_TONE[w.phase]">{{ w.phase }}</span></td>
            <td class="num">{{ w.size_bytes === null ? "…" : formatBytes(w.size_bytes) }}</td>
            <td class="num">{{ elapsed(w.modified_at) }} ago</td>
            <td class="num">
              <button
                type="button"
                class="icon-btn ghost sm del"
                :disabled="busy || !w.removable"
                :aria-label="w.removable ? 'Delete the working copy' : w.reason"
                :title="w.removable ? 'Delete the working copy' : w.reason"
                @click="remove(w)"
              >
                <Icon name="trash" :size="14" />
              </button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="5" class="subtle none">No working copies.</td>
          </tr>
        </tbody>
      </table>
    </div>
    <section class="cleanup">
      <h3 class="section-title">Clean up</h3>
      <div class="row">
        <label class="inline">
          Not changed for
          <input v-model.number="hours" type="number" min="0" class="num" @input="preview = null" /> hours
        </label>
        <label class="check">
          <input v-model="includeFailed" type="checkbox" @change="preview = null" />
          Also failed and cancelled
        </label>
        <span class="spacer"></span>
        <button type="button" class="btn" :disabled="busy" @click="check">Preview</button>
        <button type="button" class="btn danger" :disabled="busy || !preview || !preview.would_remove?.length" @click="prune">
          <Icon name="trash" :size="14" /> Prune
        </button>
      </div>
      <p v-if="preview" class="muted preview">
        {{ preview.would_remove?.length ?? 0 }} copies to remove, {{ formatBytes(preview.size_bytes) }} to free. Finished and
        orphaned copies go<template v-if="includeFailed">, failed and backlog ones too</template>.
      </p>
    </section>
    <template #footer>
      <span class="err" role="alert">{{ err }}</span>
      <span class="muted">{{ note }}</span>
      <button type="button" class="btn" @click="dlg?.close()">Close</button>
    </template>
  </AppDialog>
</template>

<style scoped>
.toolbar {
  margin-bottom: var(--s3);
}
.summary {
  font-size: var(--fs-md);
  font-variant-numeric: tabular-nums;
}
.ws-scroll {
  max-height: 46vh;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
}
.reason {
  font-size: var(--fs-sm);
}
.none {
  text-align: center;
  padding: var(--s5);
}
.del:hover:not(:disabled) {
  color: var(--danger);
  background: var(--danger-subtle);
}
.cleanup {
  margin-top: var(--s5);
}
.inline {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
}
.preview {
  margin: var(--s2) 0 0;
  font-size: var(--fs-md);
}
</style>
