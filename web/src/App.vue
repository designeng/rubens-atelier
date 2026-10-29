<script setup lang="ts">
import { ref } from "vue";
import AppHeader from "./components/AppHeader.vue";
import BoardColumns from "./components/BoardColumns.vue";
import GithubDialog from "./components/GithubDialog.vue";
import ItemDetail from "./components/ItemDetail.vue";
import NewItemDialog from "./components/NewItemDialog.vue";
import NewProjectDialog from "./components/NewProjectDialog.vue";
import { provideBoard, useBoard } from "./composables/useBoard";

provideBoard(useBoard());

const githubDialog = ref<InstanceType<typeof GithubDialog>>();
const projectDialog = ref<InstanceType<typeof NewProjectDialog>>();
const itemDialog = ref<InstanceType<typeof NewItemDialog>>();
const header = ref<InstanceType<typeof AppHeader>>();
</script>

<template>
  <AppHeader
    ref="header"
    @github="githubDialog?.open()"
    @new-project="projectDialog?.open()"
    @new-item="(title) => itemDialog?.open(title)"
  />
  <BoardColumns />
  <ItemDetail />
  <GithubDialog ref="githubDialog" />
  <NewProjectDialog ref="projectDialog" />
  <NewItemDialog ref="itemDialog" @created="header?.clearTitle()" />
</template>
