<script setup lang="ts">
import { ref } from "vue";
import { useBoardStore } from "../composables/useBoard";
import AppDialog from "./ui/AppDialog.vue";
import Field from "./ui/Field.vue";

const b = useBoardStore();
const dlg = ref<InstanceType<typeof AppDialog>>();
const value = ref("");

function open() {
  value.value = b.token.value;
  dlg.value?.showModal();
}

function save() {
  b.setToken(value.value.trim());
  dlg.value?.close();
}

defineExpose({ open });
</script>

<template>
  <AppDialog ref="dlg" title="API token">
    <form id="tokenForm" @submit.prevent="save">
      <Field
        label="Token"
        for="tToken"
        hint="The server's HARNESS_API_TOKEN. It is kept in this browser only."
      >
        <input id="tToken" v-model="value" type="password" autocomplete="current-password" aria-describedby="tToken-hint" />
      </Field>
    </form>
    <template #footer>
      <button type="button" class="btn" @click="dlg?.close()">Cancel</button>
      <button type="submit" form="tokenForm" class="btn primary">Save</button>
    </template>
  </AppDialog>
</template>
