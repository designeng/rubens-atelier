<script setup lang="ts">
import { computed, ref } from "vue";
import { useBoardStore } from "../composables/useBoard";
import AppDialog from "./ui/AppDialog.vue";
import Icon from "./ui/Icon.vue";

const b = useBoardStore();
const dlg = ref<InstanceType<typeof AppDialog>>();
const gh = computed(() => b.github.value);
const refreshing = ref(false);

async function open() {
  await b.refreshGithub();
  dlg.value?.showModal();
}
async function refresh() {
  refreshing.value = true;
  await b.refreshGithub();
  refreshing.value = false;
}

defineExpose({ open });
</script>

<template>
  <AppDialog ref="dlg" title="GitHub">
    <p v-if="!gh.configured" class="muted">
      The GitHub App is not configured on the server. Set <code>HARNESS_GITHUB_APP_ID</code>, <code>HARNESS_GITHUB_APP_SLUG</code>
      and <code>HARNESS_GITHUB_APP_PRIVATE_KEY_FILE</code> and restart. Until then projects use <code>HARNESS_GITHUB_TOKEN</code>.
    </p>
    <template v-else>
      <ul v-if="gh.installations.length" class="installs">
        <li v-for="i in gh.installations" :key="i.id">
          <span class="acct">
            <b>{{ i.account }}</b>
            <span class="muted">{{ i.repository_selection === "all" ? "All repositories" : "Selected repositories" }}</span>
          </span>
          <span v-if="i.suspended" class="chip warn">Suspended</span>
          <span v-else class="chip ok"><Icon name="check" :size="12" /> Connected</span>
          <a v-if="i.manage_url" :href="i.manage_url" target="_blank" rel="noopener" class="btn ghost sm">
            Manage <Icon name="external" :size="12" />
          </a>
        </li>
      </ul>
      <p v-else class="muted">The app is not installed yet.</p>
      <p v-if="gh.install_url" class="install">
        <a :href="gh.install_url" target="_blank" rel="noopener" class="btn">
          <Icon name="plug" /> {{ gh.installations.length ? "Add account or change repositories" : "Install the app" }}
          <Icon name="external" :size="12" />
        </a>
        <span class="muted">Pick the repositories on GitHub, then come back and refresh.</span>
      </p>
    </template>
    <template #footer>
      <button type="button" class="btn" :disabled="refreshing" @click="refresh"><Icon name="refresh" :size="14" /> Refresh</button>
      <button type="button" class="btn primary" @click="dlg?.close()">Done</button>
    </template>
  </AppDialog>
</template>

<style scoped>
.installs {
  list-style: none;
  margin: 0 0 var(--s4);
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
}
.installs li {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: var(--s2) var(--s2) var(--s2) var(--s3);
}
.installs li + li {
  border-top: 1px solid var(--border);
}
.acct {
  flex: 1;
  display: flex;
  flex-direction: column;
  font-size: var(--fs-md);
}
.install {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--s2);
  margin: 0;
  font-size: var(--fs-md);
}
</style>
