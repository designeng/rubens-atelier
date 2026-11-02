<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Column } from "../api/types";
import { canShowMoreDone, useBoardStore } from "../composables/useBoard";
import { NARROW, useMedia } from "../media";
import ItemCard from "./ItemCard.vue";
import Icon from "./ui/Icon.vue";

const emit = defineEmits<{ "new-project": []; "new-item": []; token: [] }>();

const COLS: { col: Column; name: string; empty: string }[] = [
  { col: "backlog", name: "Backlog", empty: "Nothing waiting. New stories and imported issues land here." },
  { col: "working", name: "In progress", empty: "No agent is working right now." },
  { col: "needs_human", name: "Needs you", empty: "Nothing needs your decision." },
  { col: "done", name: "Done", empty: "Finished stories show up here." },
  { col: "failed", name: "Failed / cancelled", empty: "No failed or cancelled stories." },
];
const b = useBoardStore();
const narrow = useMedia(NARROW);

const count = (col: Column) => (col === "done" ? b.doneTotal.value : (b.board.value?.[col].length ?? 0));

// Phones show one column at a time. The first pick goes where attention is needed; then it is remembered for the tab session.
const STORE_KEY = "board-column";
const readStored = (): Column | null => {
  try {
    return sessionStorage.getItem(STORE_KEY) as Column | null;
  } catch {
    return null;
  }
};
const active = ref<Column | null>(readStored());
watch(
  () => b.board.value,
  (board) => {
    if (active.value || !board) return;
    active.value = board.needs_human.length ? "needs_human" : board.working.length ? "working" : "backlog";
  },
  { immediate: true },
);
function pick(col: Column): void {
  active.value = col;
  try {
    sessionStorage.setItem(STORE_KEY, col);
  } catch {
    /* storage unavailable */
  }
}
function onTabKey(e: KeyboardEvent): void {
  if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
  const i = COLS.findIndex((c) => c.col === active.value);
  const next = COLS[(i + (e.key === "ArrowRight" ? 1 : -1) + COLS.length) % COLS.length];
  pick(next.col);
  (document.getElementById(`tab-${next.col}`) as HTMLElement | null)?.focus();
}
const visible = computed(() => (narrow.value ? COLS.filter((c) => c.col === active.value) : COLS));

// Dragging a card to a column is a shortcut for the matching button; other moves belong to the workflow.
const DROPS: Record<string, (id: string) => Promise<void>> = {
  "failed>backlog": (id) => b.reopenItem(id),
  "backlog>working": (id) => b.startItem(id),
  "working>failed": (id) => b.cancelItem(id),
  "needs_human>failed": (id) => b.cancelItem(id),
};
const dragged = ref<{ id: string; from: Column } | null>(null);
const over = ref<Column | null>(null);
const canDrop = (to: Column) => !!dragged.value && !!DROPS[`${dragged.value.from}>${to}`];

function dragStart(e: DragEvent, id: string, from: Column): void {
  dragged.value = { id, from };
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", id);
  }
}
function dragEnd(): void {
  dragged.value = null;
  over.value = null;
}
function dragOver(e: DragEvent, to: Column): void {
  if (!canDrop(to)) return;
  e.preventDefault();
  over.value = to;
}
function drop(e: DragEvent, to: Column): void {
  if (!dragged.value || !canDrop(to)) return;
  e.preventDefault();
  const { id, from } = dragged.value;
  dragEnd();
  DROPS[`${from}>${to}`](id);
}
const open = (id: string) => b.openItem(id).catch(b.fail);
</script>

<template>
  <main id="board" :class="{ narrow }">
    <div v-if="b.unauthorized.value" class="empty">
      <Icon name="key" :size="28" />
      <h2>Sign in to the board</h2>
      <p>The API needs a token. Enter the server's <code>HARNESS_API_TOKEN</code>; it stays in this browser.</p>
      <button type="button" class="btn primary" @click="emit('token')">Enter API token</button>
    </div>
    <div v-else-if="b.loaded.value && !b.slug.value" class="empty">
      <Icon name="folder" :size="28" />
      <h2>No project yet</h2>
      <p>A project ties the board to a GitHub repository. Agents work on its issues and open PRs there.</p>
      <button type="button" class="btn primary" @click="emit('new-project')"><Icon name="plus" /> New project</button>
    </div>
    <template v-else>
      <div v-if="narrow" class="col-tabs tabs" role="tablist" aria-label="Board columns" @keydown="onTabKey">
        <button
          v-for="c in COLS"
          :id="`tab-${c.col}`"
          :key="c.col"
          type="button"
          class="tab"
          role="tab"
          :data-col="c.col"
          :aria-selected="active === c.col"
          :tabindex="active === c.col ? 0 : -1"
          @click="pick(c.col)"
        >
          {{ c.col === "failed" ? "Failed" : c.name }}
          <span class="count">{{ count(c.col) }}</span>
        </button>
      </div>
      <div class="cols">
        <section
          v-for="c in visible"
          :key="c.col"
          class="col"
          :class="{ drop: over === c.col, droppable: dragged && canDrop(c.col) }"
          :data-col="c.col"
          :aria-labelledby="`col-${c.col}`"
          :role="narrow ? 'tabpanel' : undefined"
          @dragover="dragOver($event, c.col)"
          @dragleave="over === c.col && (over = null)"
          @drop="drop($event, c.col)"
        >
          <h2 :id="`col-${c.col}`" class="col-head" :class="{ 'sr-only': narrow }">
            <span class="col-dot"></span>{{ c.name }}
            <span class="col-count">{{ count(c.col) }}</span>
            <button
              v-if="c.col === 'backlog' && b.board.value"
              type="button"
              class="icon-btn ghost sm col-add"
              aria-label="New story"
              title="New story (N)"
              @click="emit('new-item')"
            >
              <Icon name="plus" :size="14" />
            </button>
          </h2>
          <div class="col-body">
            <template v-if="b.board.value">
              <ItemCard
                v-for="i in b.board.value[c.col]"
                :key="i.id"
                :item="i"
                :draggable="!narrow"
                :class="{ dragging: dragged?.id === i.id, current: b.openItemId.value === i.id }"
                @open="open(i.id)"
                @dragstart="dragStart($event, i.id, c.col)"
                @dragend="dragEnd"
              />
              <p v-if="!b.board.value[c.col].length" class="col-empty">{{ c.empty }}</p>
              <button
                v-if="c.col === 'done' && canShowMoreDone(b.doneTotal.value, b.board.value.done.length, b.doneLimit.value)"
                type="button"
                class="btn ghost more"
                @click="b.showMoreDone()"
              >
                Show more · {{ b.doneTotal.value - b.board.value.done.length }} left
              </button>
            </template>
            <template v-else>
              <div v-for="n in 3" :key="n" class="skeleton card-skel" :style="{ height: `${64 + ((n * 23) % 40)}px` }"></div>
            </template>
          </div>
        </section>
      </div>
    </template>
  </main>
</template>

<style scoped>
#board {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.cols {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: repeat(5, minmax(240px, 1fr));
  gap: var(--s3);
  padding: var(--s4);
  overflow-x: auto;
  scroll-snap-type: x proximity;
  scroll-padding: var(--s4);
}
/* Below 1280 px the lanes keep their width and scroll sideways; the next lane peeks in as a hint. */
@media (max-width: 1279px) {
  .cols {
    grid-template-columns: repeat(5, 264px);
  }
}
.col {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--lane);
  border: 1px solid transparent;
  border-radius: var(--r-lg);
  scroll-snap-align: start;
  transition: border-color 0.12s, background 0.12s;
}
.col.droppable {
  border-style: dashed;
  border-color: var(--border-strong);
}
.col.drop {
  border-color: var(--accent);
  background: var(--accent-subtle);
}
.col-head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  min-height: 44px;
  padding: var(--s2) var(--s2) var(--s2) var(--s3);
  font-size: var(--fs-sm);
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--text-muted);
}
.col-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-subtle);
}
[data-col="working"] .col-dot {
  background: var(--accent);
}
[data-col="needs_human"] .col-dot {
  background: var(--warn);
}
[data-col="done"] .col-dot {
  background: var(--ok);
}
[data-col="failed"] .col-dot {
  background: var(--danger);
}
[data-col="needs_human"] .col-head {
  color: var(--warn);
}
.col-count {
  font-variant-numeric: tabular-nums;
  color: var(--text-subtle);
  font-weight: 500;
}
.col-add {
  margin-left: auto;
}
.col-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 var(--s2) var(--s2);
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  overscroll-behavior: contain;
}
.col-empty {
  margin: 0;
  padding: var(--s4) var(--s3);
  border: 1px dashed var(--border);
  border-radius: var(--r-md);
  color: var(--text-subtle);
  font-size: var(--fs-md);
  text-align: center;
}
.card-skel {
  flex: none;
}
.more {
  width: 100%;
  color: var(--text-muted);
}
.empty {
  margin: auto;
}
.empty code {
  font-size: var(--fs-sm);
}
/* One column at a time on phones; tabs switch between them. */
.col-tabs {
  flex: none;
  gap: 0;
  padding: 0 var(--s1);
  background: var(--bg);
}
.col-tabs {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
}
.col-tabs .tab {
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  min-height: 52px;
  padding: var(--s1) 2px;
  font-size: var(--fs-sm);
  white-space: normal;
  line-height: 1.2;
  text-align: center;
}
.col-tabs .tab[data-col="needs_human"] .count {
  background: var(--warn-subtle);
  color: var(--warn);
}
#board.narrow .cols {
  display: block;
  padding: var(--s3);
  overflow: auto;
}
#board.narrow .col {
  background: none;
  border: none;
  min-height: auto;
}
#board.narrow .col-body {
  padding: 0;
  overflow: visible;
}
</style>
