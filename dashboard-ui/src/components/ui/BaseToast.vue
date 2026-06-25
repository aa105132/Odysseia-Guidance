<script setup lang="ts">
/* BaseToast — 单条通知，role=alert，aria-live=assertive(error)/polite(其他)。
 * 滑入 var(--dur-list)；手动关闭按钮 aria-label="关闭"。 */
import { computed } from 'vue';
import { X, CheckCircle2, AlertTriangle, Info, XCircle } from 'lucide-vue-next';
import type { ToastType } from '@/stores/toast';

const props = defineProps<{
  id: number;
  type: ToastType;
  message: string;
  title?: string;
}>();

const emit = defineEmits<{ (e: 'dismiss', id: number): void }>();

const live = computed<'assertive' | 'polite'>(() => (props.type === 'error' ? 'assertive' : 'polite'));

const iconComp = computed(() => {
  switch (props.type) {
    case 'success': return CheckCircle2;
    case 'warning': return AlertTriangle;
    case 'error': return XCircle;
    default: return Info;
  }
});

const iconColor = computed(() => {
  switch (props.type) {
    case 'success': return 'var(--success)';
    case 'warning': return 'var(--warning)';
    case 'error': return 'var(--danger)';
    default: return 'var(--info)';
  }
});
</script>

<template>
  <div :class="['toast', `toast--${type}`]" role="alert" :aria-live="live">
    <component :is="iconComp" class="toast__icon" :style="{ color: iconColor }" aria-hidden="true" />
    <div class="toast__content">
      <p v-if="title" class="toast__title font-display">{{ title }}</p>
      <p class="toast__msg">{{ message }}</p>
    </div>
    <button class="toast__close" type="button" aria-label="关闭" @click="emit('dismiss', id)">
      <X :size="16" aria-hidden="true" />
    </button>
  </div>
</template>

<style scoped>
.toast {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  min-width: 18rem;
  max-width: 24rem;
  padding: var(--space-3) var(--space-4);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--border-strong);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  color: var(--text-primary);
}
.toast--success { border-left-color: var(--success); }
.toast--warning { border-left-color: var(--warning); }
.toast--error { border-left-color: var(--danger); }
.toast--info { border-left-color: var(--info); }

.toast__icon { flex: none; margin-top: 0.125rem; }
.toast__content { flex: 1 1 auto; display: flex; flex-direction: column; gap: var(--space-1); }
.toast__title { font-size: var(--text-sm); font-weight: var(--fw-semibold); color: var(--text-primary); }
.toast__msg { font-size: var(--text-sm); color: var(--text-secondary); line-height: var(--lh-snug); word-break: break-word; }

.toast__close {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.5rem;
  height: 1.5rem;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: background-color var(--dur-micro) var(--ease-out-quart), color var(--dur-micro) var(--ease-out-quart);
}
.toast__close:hover { background: var(--bg-surface-2); color: var(--text-primary); }
.toast__close:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
