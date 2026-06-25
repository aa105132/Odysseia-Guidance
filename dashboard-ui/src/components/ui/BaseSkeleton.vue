<script setup lang="ts">
/* BaseSkeleton — 骨架占位，闪动动画；reduced-motion 改为静态灰块。 */
const props = withDefaults(
  defineProps<{
    width?: string;
    height?: string;
    rounded?: string;
  }>(),
  { width: '100%', height: '1rem', rounded: 'var(--radius-sm)' },
);
</script>

<template>
  <div class="skeleton" :style="{ width: props.width, height: props.height, borderRadius: props.rounded }" aria-hidden="true" />
</template>

<style scoped>
.skeleton {
  background: linear-gradient(
    90deg,
    var(--bg-surface) 0%,
    var(--bg-surface-2) 50%,
    var(--bg-surface) 100%
  );
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s ease-in-out infinite;
}

@keyframes skeleton-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton {
    animation: none;
    background: var(--bg-surface-2);
  }
}
</style>
