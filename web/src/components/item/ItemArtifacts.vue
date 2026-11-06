<script setup lang="ts">
import type { ArtifactOut } from "../../api/types";

// What the agents produced: the latest version of each artifact.
defineProps<{ artifacts: ArtifactOut[] }>();
</script>

<template>
  <section class="section">
    <div class="section-title">Artifacts</div>
    <details v-for="a in artifacts" :key="a.id" class="artifact" :open="a.name === 'error'">
      <summary :class="{ err: a.name === 'error' }">{{ a.name.startsWith("human:comment:") ? "comment" : a.name }}</summary>
      <pre :class="{ error: a.name === 'error' }">{{ a.content }}</pre>
    </details>
    <p v-if="!artifacts.length" class="subtle">None yet.</p>
  </section>
</template>

<style scoped>
.artifact {
  margin-bottom: var(--s2);
}
.artifact summary {
  cursor: pointer;
  font-weight: 600;
  font-size: var(--fs-md);
  padding: var(--s1) 0;
}
.artifact pre {
  margin-top: var(--s1);
}
</style>
