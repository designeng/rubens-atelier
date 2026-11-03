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
const model = ref("");
const err = ref("");
const busy = ref(false);
const busyStart = ref(true);

function open(initialTitle = "") {
  title.value = initialTitle;
  body.value = "";
  budget.value = "";
  urgency.value = DEFAULT_URGENCY;
  model.value = "";
  err.value = "";
  dlg.value?.showModal();
  nextTick(() => document.getElementById("iTitle")?.focus());
}

async function create(start: boolean) {
  const t = title.value.trim();
  if (!t) {
    err.value = "Title is required";
    document.getElementById("iTitle")?.focus();
    return;
  }
  busy.value = true;
  busyStart.value = start;
  try {
    await b.createItem({
      title: t,
      body: body.value,
      budget_usd: budget.value.trim() === "" ? null : Number(budget.value),
      priority: urgency.value,
      model: model.value || null,
      start,
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
    <form id="newItemForm" novalidate @submit.prevent="create(true)">
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
      <Field label="Model" for="iModel" hint="Empty: the harness default. Can be changed later.">
        <select id="iModel" v-model="model" aria-describedby="iModel-hint">
          <option value="">Default<template v-if="b.models.value"> ({{ b.models.value.default }})</template></option>
          <option v-for="m in b.models.value?.models ?? []" :key="m" :value="m">{{ m }}</option>
        </select>
      </Field>
    </form>
    <template #footer>
      <span class="err" role="alert">{{ err }}</span>
      <button type="button" class="btn" @click="dlg?.close()">Cancel</button>
      <button type="button" class="btn" :disabled="busy" title="Add to the backlog without starting" @click="create(false)">
        <span v-if="busy && !busyStart" class="spin"></span>Create
      </button>
      <button type="submit" form="newItemForm" class="btn primary" :disabled="busy">
        <span v-if="busy && busyStart" class="spin"></span>Create &amp; start
      </button>
    </template>
  </AppDialog>
</template>
