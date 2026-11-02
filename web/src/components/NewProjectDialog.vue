<script setup lang="ts">
import { computed, ref } from "vue";
import type { Repository } from "../api/types";
import { useBoardStore } from "../composables/useBoard";
import AppDialog from "./ui/AppDialog.vue";
import Field from "./ui/Field.vue";
import Icon from "./ui/Icon.vue";

const b = useBoardStore();
const dlg = ref<InstanceType<typeof AppDialog>>();
const slug = ref("");
const installation = ref(""); // installation id, "" for the server token
const repo = ref("");
const verify = ref("");
const repos = ref<Repository[]>([]);
const err = ref("");
const busy = ref(false);

const gh = computed(() => b.github.value);
const live = computed(() => gh.value.installations.filter((i) => !i.suspended));

async function loadInstallations() {
  await b.refreshGithub();
  if (!live.value.some((i) => String(i.id) === installation.value)) {
    installation.value = live.value.length ? String(live.value[0].id) : "";
  }
}

async function loadRepos() {
  repos.value = [];
  const id = installation.value;
  if (!id) return;
  const list = await b.api.installationRepositories(Number(id));
  if (installation.value !== id) return;
  repos.value = list;
  if (list.length === 1) repo.value = list[0].full_name;
}

async function open() {
  err.value = "";
  await loadInstallations();
  await loadRepos().catch(b.fail);
  dlg.value?.showModal();
}

function reload() {
  loadInstallations().then(loadRepos).catch(b.fail);
}

async function create() {
  const s = slug.value.trim();
  const r = repo.value.trim();
  if (!s || !r) {
    err.value = "Slug and repository are required";
    return;
  }
  busy.value = true;
  try {
    await b.createProject({
      slug: s,
      repo: r,
      verify_command: verify.value.trim() || null,
      installation_id: Number(installation.value) || null,
    });
    dlg.value?.close();
    slug.value = repo.value = verify.value = "";
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e);
  } finally {
    busy.value = false;
  }
}

defineExpose({ open });
</script>

<template>
  <AppDialog ref="dlg" title="New project">
    <form id="newProjectForm" novalidate @submit.prevent="create">
      <Field label="Slug" for="pSlug" hint="Lowercase letters, digits and dashes.">
        <input id="pSlug" v-model="slug" pattern="[a-z0-9-]+" autocomplete="off" aria-describedby="pSlug-hint" />
      </Field>
      <Field label="GitHub access" for="pInst">
        <select id="pInst" v-model="installation" @change="loadRepos().catch(b.fail)">
          <option v-for="i in live" :key="i.id" :value="String(i.id)">
            {{ i.account }} · {{ i.repository_selection === "all" ? "all repos" : "selected repos" }}
          </option>
          <option value="">Server token (HARNESS_GITHUB_TOKEN)</option>
        </select>
        <div class="hint">
          <template v-if="gh.install_url">
            <a :href="gh.install_url" target="_blank" rel="noopener">Install the app or change repositories <Icon name="external" :size="11" /></a>
            ·
            <button type="button" class="btn link" @click="reload">Refresh</button>
          </template>
          <template v-else-if="!gh.configured">The GitHub App is not configured on the server.</template>
        </div>
      </Field>
      <Field label="Repository" for="pRepo" hint="owner/name">
        <input id="pRepo" v-model="repo" list="pRepos" autocomplete="off" aria-describedby="pRepo-hint" />
        <datalist id="pRepos">
          <option v-for="r in repos" :key="r.full_name" :value="r.full_name"></option>
        </datalist>
      </Field>
      <Field label="Verify command" for="pVerify" hint="Optional, e.g. make test. The harness runs it before review.">
        <input id="pVerify" v-model="verify" class="mono" aria-describedby="pVerify-hint" />
      </Field>
    </form>
    <template #footer>
      <span class="err" role="alert">{{ err }}</span>
      <button type="button" class="btn" @click="dlg?.close()">Cancel</button>
      <button type="submit" form="newProjectForm" class="btn primary" :disabled="busy">Create project</button>
    </template>
  </AppDialog>
</template>
