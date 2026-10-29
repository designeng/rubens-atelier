<script setup lang="ts">
import { computed, ref } from "vue";
import { useBoardStore } from "../composables/useBoard";

const b = useBoardStore();
const dlg = ref<HTMLDialogElement>();
const base = ref("");
const verify = ref("");
const err = ref("");
const busy = ref(false);

const project = computed(() => b.projects.value.find((p) => p.slug === b.slug.value));

function open() {
  const p = project.value;
  if (!p) {
    b.fail(new Error("no project"));
    return;
  }
  base.value = p.base_branch || "";
  verify.value = p.verify_command || "";
  err.value = "";
  dlg.value?.showModal();
}

// Empty fields are sent as null: the API clears the value rather than storing a blank one.
async function save() {
  busy.value = true;
  try {
    await b.updateProject({ base_branch: base.value.trim() || null, verify_command: verify.value.trim() || null });
    dlg.value?.close();
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
    <h3 style="margin-top: 0">Project settings</h3>
    <div v-if="project" class="muted">{{ project.slug }} · {{ project.repo }}</div>
    <label for="sBase">Base branch (empty: the repository's default branch; applies to new stories only)</label>
    <input id="sBase" v-model="base" />
    <label for="sVerify">Verify command (empty: no harness checks)</label><input id="sVerify" v-model="verify" />
    <div class="row" style="margin-top: 14px">
      <button class="primary" :disabled="busy" @click="save">Save</button>
      <button @click="dlg?.close()">Cancel</button>
      <span class="err">{{ err }}</span>
    </div>
  </dialog>
</template>
