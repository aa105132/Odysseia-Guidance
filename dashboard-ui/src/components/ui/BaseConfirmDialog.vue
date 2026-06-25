<script setup lang="ts">
/* BaseConfirmDialog — 基于 BaseModal，替代原生 confirm()。
 * variant=danger 时显示危险图标 + 危险确认键；emits confirm/cancel。 */
import { AlertTriangle } from 'lucide-vue-next';
import BaseModal from './BaseModal.vue';
import BaseButton from './BaseButton.vue';

withDefaults(
  defineProps<{
    modelValue: boolean;
    title: string;
    message: string;
    confirmText?: string;
    cancelText?: string;
    variant?: 'danger' | 'primary';
  }>(),
  { confirmText: '确认', cancelText: '取消', variant: 'primary' },
);

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void;
  (e: 'confirm'): void;
  (e: 'cancel'): void;
}>();

function onModel(v: boolean): void {
  emit('update:modelValue', v);
}
function confirm(): void {
  emit('confirm');
  emit('update:modelValue', false);
}
function cancel(): void {
  emit('cancel');
  emit('update:modelValue', false);
}
</script>

<template>
  <BaseModal :model-value="modelValue" :title="title" size="sm" @update:model-value="onModel">
    <div class="confirm">
      <div v-if="variant === 'danger'" class="confirm__icon-wrap">
        <AlertTriangle class="confirm__icon" aria-hidden="true" />
      </div>
      <p class="confirm__msg">{{ message }}</p>
    </div>
    <template #footer>
      <div class="confirm__actions">
        <BaseButton variant="ghost" size="md" @click="cancel">{{ cancelText }}</BaseButton>
        <BaseButton
          :variant="variant === 'danger' ? 'danger' : 'primary'"
          size="md"
          @click="confirm"
        >
          {{ confirmText }}
        </BaseButton>
      </div>
    </template>
  </BaseModal>
</template>

<style scoped>
.confirm { display: flex; align-items: flex-start; gap: var(--space-3); }
.confirm__icon-wrap {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--space-6);
  height: var(--space-6);
  border-radius: var(--radius-sm);
  background: color-mix(in oklch, var(--danger) 14%, transparent);
}
.confirm__icon { color: var(--danger); }
.confirm__msg {
  font-size: var(--text-base);
  color: var(--text-secondary);
  line-height: var(--lh-relaxed);
  padding-top: 0.125rem;
}
.confirm__actions { display: flex; align-items: center; justify-content: flex-end; gap: var(--space-2); width: 100%; }
</style>
