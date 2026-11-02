<script setup lang="ts">
import { nextTick, ref } from "vue";
import { useBoardStore } from "../composables/useBoard";
import { DEFAULT_URGENCY, URGENCY_LEVELS } from "../urgency";
import AppDialog from "./ui/AppDialog.vue";
import Field from "./ui/Field.vue";

const emit = defineEmits<{ created: [] }>();
const b = useBoardStore();
const dlg = ref<InstanceType<typeof AppDialog>>();
const title = ref("");
const body = ref("");
const budget = ref("");
const urgency = ref(DEFAULT_URGENCY);
const err = ref("");
const busy = ref(false);

function open(initialTitle = "") {
  title.value = initialTitle;
  body.value = "";
  budget.value = "";
  urgency.value = DEFAULT_URGENCY;
  err.value = "";
  dlg.value?.showModal();
  nextTick(() => document.getElementById("iTitle")?.focus());
}

async function create() {
  const t = title.value.trim();
  if (!t) {
    err.value = "Title is required";
    document.getElementById("iTitle")?.focus();
    return;
  }
  busy.value = true;
  try {
    await b.createItem({
      title: t,
      body: body.value,
      budget_usd: budget.value.trim() === "" ? null : Number(budget.value),
      priority: urgency.value,
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
  <AppDialog ref="dlg" :title="`New story in ${b.slug.value}`" size="md">
    <form id="newItemForm" novalidate @submit.prevent="create">
      <Field label="Title" for="iTitle">
        <input id="iTitle" v-model="title" maxlength="300" required :aria-invalid="!!err && !title.trim()" />
      </Field>
      <Field label="Description" for="iBody" hint="What to build and how to tell it is done (acceptance criteria).">
        <textarea id="iBody" v-model="body" rows="8" aria-describedby="iBody-hint"></textarea>
      </Field>
      <div class="field-row">
        <Field label="Urgency" for="iUrgency" hint="More urgent work is picked up first.">
          <select id="iUrgency" v-model.number="urgency" aria-describedby="iUrgency-hint">
            <option v-for="l in URGENCY_LEVELS" :key="l.value" :value="l.value">{{ l.label }}</option>
          </select>
        </Field>
        <Field label="Budget, $" for="iBudget" hint="Empty: no limit.">
          <input id="iBudget" v-model="budget" type="number" min="0.01" step="any" placeholder="No limit" aria-describedby="iBudget-hint" />
        </Field>
      </div>
    </form>
    <template #footer>
      <span class="err" role="alert">{{ err }}</span>
      <button type="button" class="btn" @click="dlg?.close()">Cancel</button>
      <button type="submit" form="newItemForm" class="btn primary" :disabled="busy">
        <span v-if="busy" class="spin"></span>Create &amp; start
      </button>
    </template>
  </AppDialog>
</template>
