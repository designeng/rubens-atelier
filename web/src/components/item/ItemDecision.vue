<script setup lang="ts">
import { ref, watch } from "vue";
import type { ArtifactOut, RequestOut } from "../../api/types";
import Icon from "../ui/Icon.vue";

// What needs a person right now: the open request with its options, else the latest error.
const props = defineProps<{ request: RequestOut | null; latestError?: ArtifactOut; attention?: string; busy: boolean }>();
const emit = defineEmits<{ answer: [requestId: string, option: string, comment: string] }>();

// Polling replaces the item every 5 s; the comment survives as long as the request is the same.
const comment = ref("");
watch(
  () => props.request?.id,
  () => (comment.value = ""),
);

// The first answer is the suggested one; giving up is marked as destructive.
const optionClass = (o: string, n: number) => (/abandon|cancel|reject|give up/i.test(o) ? "danger" : n === 0 ? "primary" : "");
</script>

<template>
  <section v-if="request" class="section decision" aria-labelledby="dDecision">
    <h3 id="dDecision" class="decision-title"><Icon name="alert" /> Decision needed</h3>
    <p class="decision-q">{{ request.question }}</p>
    <pre v-if="latestError" class="error">{{ latestError.content }}</pre>
    <label for="dComment" class="sr-only">Comment for the next agent</label>
    <textarea id="dComment" v-model="comment" rows="3" placeholder="Comment for the next agent (optional)"></textarea>
    <div class="row">
      <button
        v-for="(o, n) in request.options"
        :key="o"
        type="button"
        class="btn"
        :class="optionClass(o, n)"
        :disabled="busy"
        @click="emit('answer', request.id, o, comment)"
      >
        {{ o.charAt(0).toUpperCase() + o.slice(1) }}
      </button>
    </div>
  </section>
  <section v-else-if="latestError" class="section">
    <div class="section-title">Latest error</div>
    <pre class="error">{{ latestError.content }}</pre>
  </section>
  <section v-else-if="attention" class="section">
    <p class="note">{{ attention }}</p>
  </section>
</template>

<style scoped>
.decision {
  padding: var(--s4);
  background: var(--warn-subtle);
  border: 1px solid color-mix(in srgb, var(--warn) 30%, transparent);
  border-radius: var(--r-md);
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}
.decision-title {
  display: flex;
  align-items: center;
  gap: var(--s2);
  font-size: var(--fs-base);
  font-weight: 600;
  color: var(--warn);
}
.decision-q {
  margin: 0;
}
.decision textarea {
  width: 100%;
}
.decision pre.error {
  max-height: 180px;
}
</style>
