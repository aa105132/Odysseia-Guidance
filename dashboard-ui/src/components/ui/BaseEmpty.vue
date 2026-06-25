<script setup lang="ts">
/* BaseEmpty — 空状态：图标 + 标题(展示宋体) + 描述 + CTA。 */
import BaseButton from './BaseButton.vue';

defineProps<{
  icon?: any;
  title: string;
  description?: string;
  actionText?: string;
  actionIcon?: any;
}>();

const emit = defineEmits<{ (e: 'action'): void }>();
</script>

<template>
  <div class="empty" role="status">
    <component :is="icon" v-if="icon" class="empty__icon" aria-hidden="true" />
    <h3 class="empty__title font-display">{{ title }}</h3>
    <p v-if="description" class="empty__desc">{{ description }}</p>
    <BaseButton
      v-if="actionText"
      variant="secondary"
      size="md"
      :icon="actionIcon"
      class="empty__action"
      @click="emit('action')"
    >
      {{ actionText }}
    </BaseButton>
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-8) var(--space-4);
  text-align: center;
}

.empty__icon {
  width: var(--space-8);
  height: var(--space-8);
  color: var(--text-muted);
}

.empty__title {
  font-size: var(--text-lg);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

.empty__desc {
  max-width: 32rem;
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: var(--lh-relaxed);
}

.empty__action { margin-top: var(--space-2); }
</style>
