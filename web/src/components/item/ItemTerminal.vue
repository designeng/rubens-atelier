<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { GitSnapshot, ItemDetail } from "../../api/types";
import { useBoardStore } from "../../composables/useBoard";
import TerminalBlock from "../TerminalBlock.vue";
import Icon from "../ui/Icon.vue";

// The story's working copy: a read-only git report of what the agent has committed (and left
// uncommitted), plus a shell in the workspace.
const props = defineProps<{ item: ItemDetail }>();

const b = useBoardStore();

const git = ref<GitSnapshot | null>(null);
const gitError = ref("");
const gitBusy = ref(false);
watch(
  () => props.item.id,
  () => {
    git.value = null;
    gitError.value = "";
  },
);
async function loadGit(diff = false): Promise<void> {
  const id = props.item.id;
  gitBusy.value = true;
  gitError.value = "";
  try {
    const snap = await b.api.getItemGit(id, diff);
    if (props.item.id === id) git.value = snap;
  } catch (e) {
    gitError.value = e instanceof Error ? e.message : String(e);
  } finally {
    gitBusy.value = false;
  }
}

// The same changes on github.com: the PR's files once there is one, else a compare view
// (without a base GitHub compares against the default branch). Needs the branch to be pushed.
const githubChanges = computed(() => {
  const i = props.item;
  const project = b.projects.value.find((p) => p.id === i.project_id);
  if (!i.branch || !project) return null;
  if (i.pr_url) return `${i.pr_url}/files`;
  const base = project.base_branch ? `${encodeURIComponent(project.base_branch)}...` : "";
  return `https://github.com/${project.repo}/compare/${base}${i.branch.split("/").map(encodeURIComponent).join("/")}`;
});
</script>

<template>
  <section class="section">
    <div class="section-title">
      Git
      <span class="spacer"></span>
      <a v-if="githubChanges" :href="githubChanges" target="_blank" rel="noopener" class="btn ghost sm">
        Changes on GitHub <Icon name="external" :size="12" />
      </a>
    </div>
    <div class="row">
      <button type="button" class="btn sm" :disabled="gitBusy" @click="loadGit()">
        <Icon name="refresh" :size="14" /> {{ git ? "Refresh report" : "Load report" }}
      </button>
      <button type="button" class="btn sm" :disabled="gitBusy" @click="loadGit(true)">Show diff</button>
      <span v-if="gitBusy" class="spin"></span>
    </div>
    <p v-if="gitError" class="err">{{ gitError }}</p>
    <template v-if="git">
      <p class="mono subtle git-head">
        {{ git.branch }} @ {{ git.head }}<template v-if="git.merge_in_progress"> · merge in progress</template>
      </p>
      <h4 class="git-sub">Commits</h4>
      <pre>{{ git.commits || "No commits on top of the base branch" }}</pre>
      <h4 class="git-sub">Status</h4>
      <pre>{{ git.status || "Clean" }}</pre>
      <h4 class="git-sub">Changes vs base</h4>
      <pre>{{ git.stat || "No changes" }}</pre>
      <template v-if="git.diff != null">
        <h4 class="git-sub">Diff</h4>
        <pre class="diff">{{ git.diff }}</pre>
      </template>
    </template>
  </section>
  <section class="section">
    <div class="section-title">Terminal</div>
    <TerminalBlock :item-id="item.id" />
  </section>
</template>

<style scoped>
.git-head {
  margin: var(--s3) 0 0;
}
.git-sub {
  margin: var(--s3) 0 var(--s1);
  font-size: var(--fs-md);
}
pre.diff {
  max-height: 60vh;
}
</style>
