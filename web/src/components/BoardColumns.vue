<script setup lang="ts">
import type { Column } from "../api/types";
import { useBoardStore } from "../composables/useBoard";
import ItemCard from "./ItemCard.vue";

const COLS: [Column, string][] = [
  ["backlog", "Backlog"],
  ["working", "Agents working"],
  ["needs_human", "Needs you"],
  ["done", "Done"],
  ["failed", "Failed / cancelled"],
];
const b = useBoardStore();
const open = (id: string) => b.openItem(id).catch(b.fail);
</script>

<template>
  <main id="board">
    <p v-if="!b.slug.value" class="muted">Create a project to start.</p>
    <template v-else-if="b.board.value">
      <section v-for="[col, name] in COLS" :key="col" class="col" :data-col="col">
        <h2>{{ name }} · {{ b.board.value[col].length }}</h2>
        <ItemCard v-for="i in b.board.value[col]" :key="i.id" :item="i" @click="open(i.id)" />
      </section>
    </template>
  </main>
</template>
