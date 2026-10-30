<script setup lang="ts">
import { ref } from "vue";
import { useBoardStore } from "../composables/useBoard";

const emit = defineEmits<{ created: [] }>();
const b = useBoardStore();
const dlg = ref<HTMLDialogElement>();
const title = ref("");
const body = ref("");
const budget = ref("");
const err = ref("");
const busy = ref(false);

function open(initialTitle = "") {
  title.value = initialTitle;
  body.value = "";
  budget.value = "";
  err.value = "";
  dlg.value?.showModal();
}

async function create() {
  const t = title.value.trim();
  if (!t) {
    err.value = "title is required";
    return;
  }
  busy.value = true;
  try {
    await b.createItem({
      title: t,
      body: body.value,
      budget_usd: budget.value.trim() === "" ? null : Number(budget.value),
      start: true,
    });
    dlg.value?.close();
    emit("created");
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

defineExpose({ open });
</script>

<template>
  <dialog ref="dlg">
    <h3 style="margin-top: 0">New story in {{ b.slug.value }}</h3>
    <label for="iTitle">Title</label><input id="iTitle" v-model="title" maxlength="300" />
    <label for="iBody">Description (what to build, acceptance criteria)</label>
    <textarea id="iBody" v-model="body" rows="8"></textarea>
    <label for="iBudget">Budget, $ (empty: no limit)</label>
    <input id="iBudget" v-model="budget" type="number" min="0.01" step="any" />
    <div class="row" style="margin-top: 14px">
      <button class="primary" :disabled="busy" @click="create">Create &amp; start</button>
      <button @click="dlg?.close()">Cancel</button>
      <span class="err">{{ err }}</span>
    </div>
  </dialog>
</template>
