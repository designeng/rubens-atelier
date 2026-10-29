<script setup lang="ts">
import { computed, ref } from "vue";
import { useBoardStore } from "../composables/useBoard";

const b = useBoardStore();
const dlg = ref<HTMLDialogElement>();
const gh = computed(() => b.github.value);

async function open() {
  await b.refreshGithub();
  dlg.value?.showModal();
}

defineExpose({ open });
</script>

<template>
  <dialog ref="dlg">
    <h3 style="margin-top: 0">GitHub</h3>
    <p v-if="!gh.configured">
      The GitHub App is not configured on the server. Set <code>HARNESS_GITHUB_APP_ID</code>,
      <code>HARNESS_GITHUB_APP_SLUG</code> and <code>HARNESS_GITHUB_APP_PRIVATE_KEY_FILE</code> and restart. Until then
      projects use <code>HARNESS_GITHUB_TOKEN</code>.
    </p>
    <template v-else>
      <div v-for="i in gh.installations" :key="i.id" class="row" style="justify-content: space-between">
        <span>
          <b>{{ i.account }}</b>
          <span class="muted">
            · {{ i.repository_selection === "all" ? "all repositories" : "selected repositories"
            }}{{ i.suspended ? " · suspended" : "" }}</span
          >
        </span>
        <a v-if="i.manage_url" :href="i.manage_url" target="_blank" rel="noopener">manage ↗</a>
      </div>
      <p v-if="!gh.installations.length" class="muted">The app is not installed yet.</p>
      <p v-if="gh.install_url">
        <a :href="gh.install_url" target="_blank" rel="noopener"
          >{{ gh.installations.length ? "Add account / change repositories" : "Install the app" }} ↗</a
        ><br />
        <span class="muted">Pick the repositories on GitHub, come back and press Refresh.</span>
      </p>
    </template>
    <div class="row" style="margin-top: 14px">
      <button @click="b.refreshGithub()">Refresh</button>
      <button @click="dlg?.close()">Close</button>
    </div>
  </dialog>
</template>
