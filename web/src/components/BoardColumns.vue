<script setup lang="ts">
import { ref } from "vue";
import type { Column } from "../api/types";
import { canShowMoreDone, useBoardStore } from "../composables/useBoard";
import ItemCard from "./ItemCard.vue";

const COLS: [Column, string][] = [
  ["backlog", "Backlog"],
  ["working", "In progress"],
  ["needs_human", "Needs you"],
  ["done", "Done"],
  ["failed", "Failed / cancelled"],
];
const b = useBoardStore();

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
  <main id="board">
    <p v-if="!b.slug.value" class="muted">Create a project to start.</p>
    <template v-else-if="b.board.value">
      <section v-for="[col, name] in COLS" :key="col" class="col"
        :class="{ drop: over === col }"
        :data-col="col"
        @dragover="dragOver($event, col)"
        @dragleave="over === col && (over = null)"
        @drop="drop($event, col)"
      >
        <h2>{{ name }} · {{ col === "done" ? b.doneTotal.value : b.board.value[col].length }}</h2>
        <ItemCard
          v-for="i in b.board.value[col]"
          :key="i.id"
          :item="i"
          :class="{ dragging: dragged?.id === i.id }"
          @click="open(i.id)"
          @dragstart="dragStart($event, i.id, col)"
          @dragend="dragEnd"
        />
        <button
          v-if="col === 'done' && canShowMoreDone(b.doneTotal.value, b.board.value.done.length, b.doneLimit.value)"
          type="button"
          class="more"
          @click="b.showMoreDone()"
        >
          Show more ({{ b.doneTotal.value - b.board.value.done.length }} left)
        </button>
      </section>
    </template>
  </main>
</template>
