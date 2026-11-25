<script setup lang="ts">
import { computed, ref } from "vue";
import { useBoardStore } from "../composables/useBoard";
import AppDialog from "./ui/AppDialog.vue";
import Field from "./ui/Field.vue";

const b = useBoardStore();
const dlg = ref<InstanceType<typeof AppDialog>>();
const base = ref("");
const verify = ref("");
const autoMerge = ref(false);
const mergeWithoutCi = ref(false);
const scopeCheck = ref(true);
const budget = ref("");
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
  autoMerge.value = p.auto_merge;
  mergeWithoutCi.value = p.merge_without_ci;
  scopeCheck.value = p.scope_check;
  budget.value = p.budget_usd == null ? "" : String(p.budget_usd);
  err.value = "";
  dlg.value?.showModal();
}

// Empty fields are sent as null: the API clears the value rather than storing a blank one.
async function save() {
  busy.value = true;
  try {
    await b.updateProject({
      base_branch: base.value.trim() || null,
      verify_command: verify.value.trim() || null,
      auto_merge: autoMerge.value,
      merge_without_ci: mergeWithoutCi.value,
      scope_check: scopeCheck.value,
      budget_usd: budget.value.trim() === "" ? null : Number(budget.value),
    });
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
  <AppDialog ref="dlg" title="Project settings" size="md">
    <p v-if="project" class="muted proj">
      <b>{{ project.slug }}</b> · <a :href="`https://github.com/${project.repo}`" target="_blank" rel="noopener">{{ project.repo }}</a>
    </p>
    <form id="settingsForm" @submit.prevent="save">
      <Field label="Base branch" for="sBase" hint="Empty: the repository's default branch. Applies to new stories only.">
        <input id="sBase" v-model="base" class="mono" aria-describedby="sBase-hint" />
      </Field>
      <Field label="Verify command" for="sVerify" hint="Empty: no harness checks.">
        <input id="sVerify" v-model="verify" class="mono" aria-describedby="sVerify-hint" />
      </Field>
      <Field
        label="Budget, $"
        for="sBudget"
        hint="Empty: no limit. Once the project's runs have cost this much, no new agent run starts; the story waits in Needs you until you raise it."
      >
        <input id="sBudget" v-model="budget" type="number" min="0.01" step="any" class="num" placeholder="No limit" aria-describedby="sBudget-hint" />
      </Field>
      <div class="field">
        <label class="check">
          <input v-model="autoMerge" type="checkbox" aria-describedby="sMerge-hint" />
          <span>
            <span class="field-label">Automerge</span>
            <span id="sMerge-hint" class="hint">
              Merge a story's PR once the reviewer approved it and CI passed. When it's off, or GitHub refuses the merge, the PR
              waits for you in Needs you.
            </span>
          </span>
        </label>
      </div>
      <div class="field">
        <label class="check">
          <input v-model="mergeWithoutCi" type="checkbox" :disabled="!autoMerge" aria-describedby="sNoCi-hint" />
          <span>
            <span class="field-label">Automerge without CI</span>
            <span id="sNoCi-hint" class="hint">
              Also merge a PR on which no CI check reported. When it's off, such a PR waits for you in Needs you.
            </span>
          </span>
        </label>
      </div>
      <div class="field">
        <label class="check">
          <input v-model="scopeCheck" type="checkbox" aria-describedby="sScope-hint" />
          <span>
            <span class="field-label">Compare the change with the plan</span>
            <span id="sScope-hint" class="hint">
              The planner lists the files it means to touch, and the reviewer sees where the change differs. Costs a few hundred
              tokens per story.
            </span>
          </span>
        </label>
      </div>
    </form>
    <template #footer>
      <span class="err" role="alert">{{ err }}</span>
      <button type="button" class="btn" @click="dlg?.close()">Cancel</button>
      <button type="submit" form="settingsForm" class="btn primary" :disabled="busy">Save</button>
    </template>
  </AppDialog>
</template>

<style scoped>
.proj {
  margin: 0 0 var(--s4);
}
.check .hint {
  display: block;
  margin-top: 2px;
}
</style>
