<script setup lang="ts">
import { computed, ref } from "vue";
import type { Repository } from "../api/types";
import { useBoardStore } from "../composables/useBoard";

const b = useBoardStore();
const dlg = ref<HTMLDialogElement>();
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
    err.value = "slug and repository are required";
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
  <dialog ref="dlg">
    <h3 style="margin-top: 0">New project</h3>
    <label for="pSlug">Slug (a-z, 0-9, -)</label><input id="pSlug" v-model="slug" />
    <label for="pInst">GitHub access</label>
    <select id="pInst" v-model="installation" @change="loadRepos().catch(b.fail)">
      <option v-for="i in live" :key="i.id" :value="String(i.id)">
        {{ i.account }} · {{ i.repository_selection === "all" ? "all repos" : "selected repos" }}
      </option>
      <option value="">Server token (HARNESS_GITHUB_TOKEN)</option>
    </select>
    <div class="muted" style="font-size: 12px; margin-top: 4px">
      <template v-if="gh.install_url">
        <a :href="gh.install_url" target="_blank" rel="noopener">Install the app / change repositories ↗</a> ·
        <a href="#" @click.prevent="reload">refresh</a>
      </template>
      <template v-else-if="!gh.configured">GitHub App is not configured on the server.</template>
    </div>
    <label for="pRepo">Repository (owner/name)</label>
    <input id="pRepo" v-model="repo" list="pRepos" autocomplete="off" />
    <datalist id="pRepos">
      <option v-for="r in repos" :key="r.full_name" :value="r.full_name"></option>
    </datalist>
    <label for="pVerify">Verify command (optional, e.g. make test)</label><input id="pVerify" v-model="verify" />
    <div class="row" style="margin-top: 14px">
      <button class="primary" :disabled="busy" @click="create">Create</button>
      <button @click="dlg?.close()">Cancel</button>
      <span class="err">{{ err }}</span>
    </div>
  </dialog>
</template>
