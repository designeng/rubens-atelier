<script setup lang="ts">
import { computed, onUnmounted, ref } from "vue";
import type { ItemDetail } from "../../api/types";
import { useBoardStore } from "../../composables/useBoard";
import { formatUsd, overBudget } from "../../format";
import Icon from "../ui/Icon.vue";

// Per-story agent settings. Each field saves as soon as it changes and flashes "Saved" for a moment.
const props = defineProps<{ item: ItemDetail; editable: boolean }>();

const b = useBoardStore();

const saved = ref<string | null>(null);
let savedTimer: ReturnType<typeof setTimeout> | undefined;
function flashSaved(field: string): void {
  saved.value = field;
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => (saved.value = null), 2000);
}
onUnmounted(() => clearTimeout(savedTimer));

// The model picker saves right away; "" is the default (the step's model, else the harness's).
const modelOptions = computed(() => {
  const list = b.models.value?.models ?? [];
  const current = props.item.model;
  return current && !list.includes(current) ? [current, ...list] : list;
});
const modelError = ref("");
// Lean context saves right away too; "" is the harness default (HARNESS_LEAN_CONTEXT).
const contextValue = computed(() => (props.item.lean_context == null ? "" : props.item.lean_context ? "lean" : "full"));
const contextError = ref("");
async function setContext(value: string): Promise<void> {
  contextError.value = "";
  try {
    await b.updateItem(props.item.id, { lean_context: value ? value === "lean" : null });
    flashSaved("context");
  } catch (e) {
    contextError.value = e instanceof Error ? e.message : String(e);
  }
}

async function setModel(value: string): Promise<void> {
  modelError.value = "";
  try {
    await b.updateItem(props.item.id, { model: value || null });
    flashSaved("model");
  } catch (e) {
    modelError.value = e instanceof Error ? e.message : String(e);
  }
}

// The budget saves on change; empty removes the limit. The server refuses zero and negatives.
const budgetError = ref("");
async function setBudget(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement;
  budgetError.value = "";
  try {
    await b.updateItem(props.item.id, { budget_usd: input.value.trim() === "" ? null : input.valueAsNumber });
    flashSaved("budget");
  } catch (err) {
    budgetError.value = err instanceof Error ? err.message : String(err);
    input.value = props.item.budget_usd == null ? "" : String(props.item.budget_usd);
  }
}
</script>

<template>
  <section class="section">
    <div class="section-title">Agent settings</div>
    <p v-if="item.progress?.task_id" class="note">The running step keeps its settings; the next step uses these.</p>
    <div class="settings">
      <label for="iModel">Model</label>
      <div class="setting">
        <select id="iModel" :value="item.model ?? ''" :disabled="!editable" @change="setModel(($event.target as HTMLSelectElement).value)">
          <option value="">Default<template v-if="b.models.value"> ({{ b.models.value.default }})</template></option>
          <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
        </select>
        <span v-if="saved === 'model'" class="saved"><Icon name="check" :size="14" /> Saved</span>
        <span class="err">{{ modelError }}</span>
      </div>
      <label for="iContext">Context</label>
      <div class="setting">
        <select
          id="iContext"
          :value="contextValue"
          :disabled="!editable"
          aria-describedby="iContext-hint"
          @change="setContext(($event.target as HTMLSelectElement).value)"
        >
          <option value="">Default<template v-if="b.models.value"> ({{ b.models.value.lean_context ? "lean" : "full" }})</template></option>
          <option value="full">Full</option>
          <option value="lean">Lean</option>
        </select>
        <span v-if="saved === 'context'" class="saved"><Icon name="check" :size="14" /> Saved</span>
        <span class="err">{{ contextError }}</span>
        <span id="iContext-hint" class="hint">Lean: a short system prompt, only the allowed tools, no CLAUDE.md preloaded, a new session per step.</span>
      </div>
      <label for="iBudget">Budget, $</label>
      <div class="setting">
        <input
          id="iBudget"
          type="number"
          min="0.01"
          step="any"
          class="num"
          placeholder="No limit"
          :value="item.budget_usd ?? ''"
          :disabled="!editable"
          aria-describedby="iBudget-hint"
          @change="setBudget"
        />
        <span v-if="saved === 'budget'" class="saved"><Icon name="check" :size="14" /> Saved</span>
        <span class="err">{{ budgetError }}</span>
        <span id="iBudget-hint" class="hint" :class="{ err: overBudget(item.spent_usd, item.budget_usd) }">
          Spent {{ formatUsd(item.spent_usd) }}<template v-if="overBudget(item.spent_usd, item.budget_usd)"> · limit reached: raise it, then retry</template>
          · {{ item.kind === "epic" ? "covers the epic and everything below it" : "covers this item and its children" }}
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.settings {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: var(--s3) var(--s4);
  align-items: start;
}
.settings > label {
  font-size: var(--fs-md);
  font-weight: 600;
  padding-top: 7px;
}
.setting {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--s1) var(--s2);
}
.setting .hint {
  flex-basis: 100%;
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
.saved {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--ok);
  font-size: var(--fs-sm);
}
@media (max-width: 767px) {
  .settings {
    grid-template-columns: 1fr;
    gap: var(--s1);
  }
  .settings > label {
    padding-top: var(--s2);
  }
}
</style>
