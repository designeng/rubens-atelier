<script setup lang="ts">
import { computed, ref } from "vue";
import type { NoteOut, NotesFile } from "../api/types";
import { itemHash, useBoardStore } from "../composables/useBoard";
import AppDialog from "./ui/AppDialog.vue";
import Field from "./ui/Field.vue";
import Icon from "./ui/Icon.vue";

const b = useBoardStore();
const dlg = ref<InstanceType<typeof AppDialog>>();
const notes = ref<NoteOut[]>([]);
const showArchived = ref(false);
const err = ref("");
const note = ref("");
const busy = ref(false);

// One form adds a note or edits the one picked with Edit.
const editing = ref<NoteOut | null>(null);
const title = ref("");
const tags = ref("");
const content = ref("");

// Copying a fragment of another project's memory: picked notes plus every note with one of the tags.
const from = ref("");
const fromNotes = ref<NoteOut[]>([]);
const picked = ref<string[]>([]);
const copyTags = ref("");
const fileInput = ref<HTMLInputElement>();
const MAX_FILE_BYTES = 5_000_000;

const proposed = computed(() => notes.value.filter((n) => n.status === "proposed"));
const kept = computed(() => notes.value.filter((n) => n.status === "active" || (showArchived.value && n.status === "archived")));
const others = computed(() => b.projects.value.filter((p) => p.slug !== b.slug.value));
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const splitTags = (s: string) => s.split(",").map((t) => t.trim()).filter(Boolean);
// The story whose agent proposed a note, if the board still shows it.
const storyTitle = (id: string) =>
  Object.values(b.board.value ?? {})
    .flat()
    .find((i) => i.id === id)?.title ?? "a story";

async function load() {
  if (!b.slug.value) return;
  notes.value = await b.api.listNotes(b.slug.value);
}

function open() {
  err.value = note.value = "";
  from.value = "";
  fromNotes.value = [];
  reset();
  dlg.value?.showModal();
  load().catch((e) => (err.value = message(e)));
}

function reset() {
  editing.value = null;
  title.value = tags.value = content.value = "";
}

function edit(n: NoteOut) {
  editing.value = n;
  title.value = n.title;
  tags.value = n.tags.join(", ");
  content.value = n.content;
}

async function guarded(work: () => Promise<unknown>) {
  busy.value = true;
  err.value = "";
  try {
    await work();
    await load();
    b.refresh().catch(b.fail); // the header counts proposed notes
  } catch (e) {
    err.value = message(e);
  } finally {
    busy.value = false;
  }
}

const save = () =>
  guarded(async () => {
    const body = { title: title.value.trim(), content: content.value.trim(), tags: splitTags(tags.value) };
    if (editing.value) await b.api.updateNote(editing.value.id, body);
    else await b.api.createNote(b.slug.value, body);
    reset();
  });

const setStatus = (n: NoteOut, status: NoteOut["status"]) => guarded(() => b.api.updateNote(n.id, { status }));

function remove(n: NoteOut) {
  if (n.status !== "proposed" && !confirm(`Delete "${n.title}" for good? Archive keeps it.`)) return;
  return guarded(() => b.api.deleteNote(n.id));
}

async function loadFrom() {
  picked.value = [];
  fromNotes.value = [];
  if (!from.value) return;
  try {
    fromNotes.value = (await b.api.listNotes(from.value)).filter((n) => n.status === "active");
  } catch (e) {
    err.value = message(e);
  }
}

const copy = () =>
  guarded(async () => {
    const res = await b.api.copyNotes(b.slug.value, {
      from_project: from.value,
      note_ids: picked.value,
      tags: splitTags(copyTags.value),
    });
    note.value = `Copied ${res.copied.length}` + (res.skipped ? `, ${res.skipped} already here` : "") + ".";
    picked.value = [];
  });

async function download(format: "json" | "markdown") {
  err.value = "";
  try {
    const url = URL.createObjectURL(await b.api.exportNotes(b.slug.value, format));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${b.slug.value}-notes.${format === "json" ? "json" : "md"}`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    err.value = message(e);
  }
}

function importFile(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  return guarded(async () => {
    if (file.size > MAX_FILE_BYTES) throw new Error(`${file.name} is too large for a notes file`);
    let data: NotesFile;
    try {
      data = JSON.parse(await file.text()) as NotesFile;
    } catch {
      throw new Error(`${file.name} is not a JSON export of notes`);
    }
    const res = await b.api.importNotes(b.slug.value, data);
    note.value = `Imported ${res.copied.length} for review` + (res.skipped ? `, ${res.skipped} already here` : "") + ".";
  });
}

defineExpose({ open });
</script>

<template>
  <AppDialog ref="dlg" title="Project memory" size="lg">
    <p class="muted intro">
      Lessons agents of <b>{{ b.slug.value }}</b> read at the start of every fresh session. Agents may propose new ones; they wait
      here until you approve them.
    </p>

    <section v-if="proposed.length">
      <h3 class="section-title">Proposed by agents · {{ proposed.length }}</h3>
      <ul class="notes">
        <li v-for="n in proposed" :key="n.id" class="note proposed">
          <div class="head">
            <b>{{ n.title }}</b>
            <span v-for="t in n.tags" :key="t" class="chip">{{ t }}</span>
            <span class="subtle by">{{ n.created_by }}</span>
            <a v-if="n.source_item_id" class="by" :href="itemHash(n.source_item_id, 'overview', b.slug.value)" @click="dlg?.close()">
              from {{ storyTitle(n.source_item_id) }}
            </a>
          </div>
          <p class="body">{{ n.content }}</p>
          <div class="row actions">
            <button type="button" class="btn sm primary" :disabled="busy" @click="setStatus(n, 'active')"><Icon name="check" :size="14" /> Approve</button>
            <button type="button" class="btn sm" :disabled="busy" @click="edit(n)"><Icon name="pencil" :size="14" /> Edit</button>
            <button type="button" class="btn sm" :disabled="busy" @click="remove(n)"><Icon name="x" :size="14" /> Reject</button>
          </div>
        </li>
      </ul>
    </section>

    <section>
      <div class="row">
        <h3 class="section-title">In memory · {{ notes.filter((n) => n.status === "active").length }}</h3>
        <span class="spacer"></span>
        <label class="check"><input v-model="showArchived" type="checkbox" /> Show archived</label>
      </div>
      <ul class="notes">
        <li v-for="n in kept" :key="n.id" class="note" :class="{ archived: n.status === 'archived' }">
          <div class="head">
            <b>{{ n.title }}</b>
            <span v-for="t in n.tags" :key="t" class="chip">{{ t }}</span>
            <span v-if="n.status === 'archived'" class="chip warn">archived</span>
            <span class="subtle by">{{ n.created_by }}</span>
            <span class="spacer"></span>
            <button type="button" class="icon-btn ghost sm" :disabled="busy" aria-label="Edit" title="Edit" @click="edit(n)">
              <Icon name="pencil" :size="14" />
            </button>
            <button
              type="button"
              class="icon-btn ghost sm"
              :disabled="busy"
              :aria-label="n.status === 'archived' ? 'Restore' : 'Archive'"
              :title="n.status === 'archived' ? 'Restore' : 'Archive'"
              @click="setStatus(n, n.status === 'archived' ? 'active' : 'archived')"
            >
              <Icon :name="n.status === 'archived' ? 'undo' : 'download'" :size="14" />
            </button>
            <button type="button" class="icon-btn ghost sm del" :disabled="busy" aria-label="Delete" title="Delete" @click="remove(n)">
              <Icon name="trash" :size="14" />
            </button>
          </div>
          <p class="body">{{ n.content }}</p>
        </li>
        <li v-if="!kept.length" class="subtle none">Nothing yet. Add a convention or a pitfall below.</li>
      </ul>
    </section>

    <section>
      <h3 class="section-title">{{ editing ? `Edit "${editing.title}"` : "Add a note" }}</h3>
      <form id="noteForm" @submit.prevent="save">
        <div class="row two">
          <Field label="Title" for="nTitle">
            <input id="nTitle" v-model="title" maxlength="200" required />
          </Field>
          <Field label="Tags" for="nTags" hint="Comma-separated: language, area. Copying selects by them.">
            <input id="nTags" v-model="tags" placeholder="python, tests" aria-describedby="nTags-hint" />
          </Field>
        </div>
        <Field label="Note" for="nContent">
          <textarea id="nContent" v-model="content" rows="4" required placeholder="The lesson and why, in a few lines."></textarea>
        </Field>
        <div class="row">
          <span class="spacer"></span>
          <button v-if="editing" type="button" class="btn" @click="reset">Cancel edit</button>
          <button type="submit" class="btn primary" :disabled="busy">{{ editing ? "Save" : "Add" }}</button>
        </div>
      </form>
    </section>

    <section>
      <h3 class="section-title">Copy, export, import</h3>
      <div class="row">
        <button type="button" class="btn sm" :disabled="busy" title="Active notes, to import here or on another factory" @click="download('json')">
          <Icon name="download" :size="14" /> Export
        </button>
        <button type="button" class="btn sm" :disabled="busy" title="Active notes as Markdown, for people to read" @click="download('markdown')">
          <Icon name="download" :size="14" /> Markdown
        </button>
        <button type="button" class="btn sm" :disabled="busy" title="Notes from an exported file arrive as proposals" @click="fileInput?.click()">
          <Icon name="upload" :size="14" /> Import…
        </button>
        <input ref="fileInput" type="file" accept=".json,application/json" hidden @change="importFile" />
      </div>
      <div v-if="others.length" class="row copy">
        <select v-model="from" aria-label="Source project" @change="loadFrom">
          <option value="">Pick a project…</option>
          <option v-for="p in others" :key="p.id" :value="p.slug">{{ p.slug }}</option>
        </select>
        <input v-model="copyTags" :disabled="!from" placeholder="and every note tagged… (python, ci)" aria-label="Tags to copy" class="grow" />
        <button type="button" class="btn" :disabled="busy || !from || (!picked.length && !copyTags.trim())" @click="copy">Copy</button>
      </div>
      <ul v-if="others.length && from" class="picks">
        <li v-for="n in fromNotes" :key="n.id">
          <label class="check">
            <input v-model="picked" type="checkbox" :value="n.id" />
            <span>{{ n.title }} <span v-for="t in n.tags" :key="t" class="chip">{{ t }}</span></span>
          </label>
        </li>
        <li v-if="!fromNotes.length" class="subtle">No active notes in {{ from }}.</li>
      </ul>
    </section>

    <template #footer>
      <span class="err" role="alert">{{ err }}</span>
      <span class="muted">{{ note }}</span>
      <button type="button" class="btn" @click="dlg?.close()">Close</button>
    </template>
  </AppDialog>
</template>

<style scoped>
.intro {
  margin: 0 0 var(--s4);
  font-size: var(--fs-md);
}
section + section {
  margin-top: var(--s5);
}
.notes,
.picks {
  list-style: none;
  margin: var(--s2) 0 0;
  padding: 0;
}
.notes {
  max-height: 34vh;
  overflow: auto;
}
.note {
  padding: var(--s3);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
}
.note + .note {
  margin-top: var(--s2);
}
.note.proposed {
  border-color: var(--accent);
  background: var(--accent-subtle);
}
.note.archived {
  opacity: 0.6;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s2);
}
.by {
  font-size: var(--fs-sm);
}
.body {
  margin: var(--s2) 0 0;
  white-space: pre-wrap;
  font-size: var(--fs-md);
}
.actions {
  margin-top: var(--s2);
}
.none {
  padding: var(--s4);
  text-align: center;
}
.two > * {
  flex: 1;
}
.grow {
  flex: 1;
}
.copy {
  margin-top: var(--s2);
}
.picks li {
  padding: 2px 0;
}
.del:hover:not(:disabled) {
  color: var(--danger);
  background: var(--danger-subtle);
}
</style>
