/* toast.ts — 全局通知队列（Pinia）
 * BaseToastContainer 消费此 store 渲染队列。error 默认不自动消失，需手动关闭。 */
import { defineStore } from 'pinia';
import { ref } from 'vue';

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface ToastItem {
  id: number;
  type: ToastType;
  message: string;
  title?: string;
  duration: number; // 0 = 不自动消失
}

export interface ToastPushOptions {
  type: ToastType;
  message: string;
  title?: string;
  duration?: number;
}

let _seq = 0;

export const useToastStore = defineStore('toast', () => {
  const items = ref<ToastItem[]>([]);

  function push(opts: ToastPushOptions): number {
    const id = ++_seq;
    // error 默认驻留（duration 0），其余默认 4000ms
    const duration = opts.duration ?? (opts.type === 'error' ? 0 : 4000);
    items.value.push({ id, type: opts.type, message: opts.message, title: opts.title, duration });
    if (duration > 0) {
      window.setTimeout(() => dismiss(id), duration);
    }
    return id;
  }

  function dismiss(id: number): void {
    const idx = items.value.findIndex((t) => t.id === id);
    if (idx >= 0) items.value.splice(idx, 1);
  }

  function clear(): void {
    items.value = [];
  }

  return { items, push, dismiss, clear };
});
