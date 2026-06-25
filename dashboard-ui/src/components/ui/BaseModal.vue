<script setup lang="ts">
/* BaseModal — 对话框外壳，role=dialog aria-modal=true。
 * Escape 关闭、焦点陷阱（Tab/Shift+Tab 循环）、开时聚焦首个可聚焦元素、关时还原焦点。
 * 背景遮罩 var(--bg-base / 0.7) 非 blur；过渡 var(--dur-view) var(--ease-out-quart)。 */
import { ref, watch, nextTick, onBeforeUnmount } from 'vue';
import { X } from 'lucide-vue-next';

const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    title?: string;
    size?: 'sm' | 'md' | 'lg';
  }>(),
  { size: 'md' },
);

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>();

const panelRef = ref<HTMLElement | null>(null);
let lastFocused: HTMLElement | null = null;

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function close(): void {
  emit('update:modelValue', false);
}

function getFocusables(): HTMLElement[] {
  if (!panelRef.value) return [];
  return Array.from(panelRef.value.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

function onKeyDown(e: KeyboardEvent): void {
  if (!props.modelValue) return;
  if (e.key === 'Escape') {
    e.preventDefault();
    close();
    return;
  }
  if (e.key === 'Tab' && panelRef.value) {
    const f = getFocusables();
    if (f.length === 0) {
      e.preventDefault();
      panelRef.value.focus();
      return;
    }
    const first = f[0];
    const last = f[f.length - 1];
    const active = document.activeElement as HTMLElement;
    if (e.shiftKey && (active === first || !panelRef.value.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }
}

watch(
  () => props.modelValue,
  async (open) => {
    if (open) {
      lastFocused = document.activeElement as HTMLElement;
      window.addEventListener('keydown', onKeyDown);
      await nextTick();
      const f = getFocusables();
      (f[0] ?? panelRef.value)?.focus();
    } else {
      window.removeEventListener('keydown', onKeyDown);
      if (lastFocused && typeof lastFocused.focus === 'function') lastFocused.focus();
      lastFocused = null;
    }
  },
);

onBeforeUnmount(() => window.removeEventListener('keydown', onKeyDown));
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="modelValue" class="modal-overlay" @click.self="close">
        <div
          ref="panelRef"
          :class="['modal-panel', `modal-panel--${size}`]"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
          tabindex="-1"
        >
          <header v-if="title || $slots.header" class="modal-header">
            <h2 class="modal-title font-display">{{ title }}</h2>
            <button class="modal-close" type="button" aria-label="关闭" @click="close">
              <X :size="18" aria-hidden="true" />
            </button>
          </header>
          <div class="modal-body"><slot /></div>
          <footer v-if="$slots.footer" class="modal-footer"><slot name="footer" /></footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-4);
  /* 暖深底遮罩，禁 backdrop-blur；由 --bg-base 派生 70% 透明度 */
  background: color-mix(in oklch, var(--bg-base) 70%, transparent);
  z-index: 90;
}

.modal-panel {
  width: 100%;
  max-height: calc(100vh - var(--space-8));
  overflow: auto;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  /* 面板自身可作焦点回退 */
  outline: none;
}
.modal-panel--sm { max-width: 28rem; }
.modal-panel--md { max-width: 40rem; }
.modal-panel--lg { max-width: 56rem; }

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--border);
}
.modal-title { font-size: var(--text-lg); font-weight: var(--fw-semibold); color: var(--text-primary); }

.modal-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--space-6);
  height: var(--space-6);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: background-color var(--dur-micro) var(--ease-out-quart), color var(--dur-micro) var(--ease-out-quart);
}
.modal-close:hover { background: var(--bg-surface-2); color: var(--text-primary); }
.modal-close:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.modal-body { padding: var(--space-5); }
.modal-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid var(--border);
}

/* 过渡：视图切换时长 + ease-out-quart */
.modal-enter-active,
.modal-leave-active { transition: opacity var(--dur-view) var(--ease-out-quart); }
.modal-enter-active .modal-panel,
.modal-leave-active .modal-panel {
  transition: opacity var(--dur-view) var(--ease-out-quart),
    transform var(--dur-view) var(--ease-out-quart);
}
.modal-enter-from,
.modal-leave-to { opacity: 0; }
.modal-enter-from .modal-panel,
.modal-leave-to .modal-panel { opacity: 0; transform: translateY(8px) scale(0.98); }
</style>
