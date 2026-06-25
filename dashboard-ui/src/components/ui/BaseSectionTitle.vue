<script setup lang="ts">
/* BaseSectionTitle — 区段标题，标题用展示宋体 semibold。 */
defineProps<{
  title: string;
  subtitle?: string;
  icon?: any;
}>();
</script>

<template>
  <div class="section-title">
    <component :is="icon" v-if="icon" class="section-title__icon" aria-hidden="true" />
    <div class="section-title__text">
      <h2 class="section-title__heading font-display">{{ title }}</h2>
      <p v-if="subtitle" class="section-title__sub">{{ subtitle }}</p>
    </div>
  </div>
</template>

<style scoped>
.section-title { display: flex; align-items: flex-start; gap: var(--space-3); }

.section-title__icon {
  flex: none;
  width: var(--space-5);
  height: var(--space-5);
  margin-top: 0.125rem;
  color: var(--accent);
}

.section-title__text { display: flex; flex-direction: column; gap: var(--space-1); }
.section-title__heading {
  font-size: var(--text-xl);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  line-height: var(--lh-tight);
}
.section-title__sub {
  font-size: var(--text-sm);
  color: var(--text-muted);
  line-height: var(--lh-snug);
}
</style>
