<script setup lang="ts">
/* BaseToastContainer — 消费 useToastStore 渲染队列，堆叠右下。
 * 滑入/滑出/位移 var(--dur-list) var(--ease-out-quart)；reduced-motion 由 base.css 全局禁用。 */
import { useToastStore } from '@/stores/toast';
import BaseToast from './BaseToast.vue';

const store = useToastStore();
</script>

<template>
  <Teleport to="body">
    <TransitionGroup name="toast" tag="div" class="toast-container" aria-label="通知列表">
      <BaseToast
        v-for="t in store.items"
        :key="t.id"
        :id="t.id"
        :type="t.type"
        :message="t.message"
        :title="t.title"
        @dismiss="store.dismiss"
      />
    </TransitionGroup>
  </Teleport>
</template>

<style scoped>
.toast-container {
  position: fixed;
  right: var(--space-6);
  bottom: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  z-index: 100;
  /* 容器自身不参与 tab 顺序 */
  pointer-events: none;
}
.toast-container > * { pointer-events: auto; }

.toast-enter-active,
.toast-leave-active { transition: all var(--dur-list) var(--ease-out-quart); }
.toast-enter-from { opacity: 0; transform: translateX(100%); }
.toast-leave-to { opacity: 0; transform: translateX(100%); }
.toast-leave-active { position: absolute; width: max-content; }
.toast-move { transition: transform var(--dur-list) var(--ease-out-quart); }
</style>
