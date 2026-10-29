<script setup lang="ts">
import type { ItemOut } from "../api/types";
import ProgressBlock from "./ProgressBlock.vue";

defineProps<{ item: ItemOut }>();
</script>

<template>
  <div class="card" draggable="true">
    <div>{{ item.title }}</div>
    <div class="meta">{{ item.kind }} · {{ item.state || "backlog" }}{{ item.pr_url ? " · PR" : "" }}{{ item.issue ? ` · #${item.issue}` : "" }}</div>
    <ProgressBlock v-if="item.progress" :progress="item.progress" />
  </div>
</template>
