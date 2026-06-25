/* usePolling — 可见 tab 守卫的节流轮询 + 手动刷新。
 * 轮询调用本身走 store.refreshX(force=false) 的 TTL 守卫，未过期直接复用缓存不联网；
 * 手动刷新走 force=true 绕过 TTL。tab 不可见时暂停 interval，恢复可见立即补拉一次再重启 interval。 */
import { onMounted, onUnmounted } from 'vue';

export interface UsePollingOptions {
  /** 轮询调用（TTL-respecting，调用处用 store.refreshX() force=false） */
  poll: () => Promise<void>;
  /** 手动刷新（force=true，调用处用 store.refreshX(true)） */
  forceRefresh: () => Promise<void>;
  /** 轮询间隔，默认 30000ms */
  intervalMs?: number;
  /** 挂载即 poll() 一次，默认 true */
  immediate?: boolean;
}

export interface UsePollingReturn {
  /** 手动刷新：触发 forceRefresh（force=true） */
  refresh: () => Promise<void>;
}

export function usePolling(options: UsePollingOptions): UsePollingReturn {
  const { poll, forceRefresh, intervalMs = 30_000, immediate = true } = options;

  let timer: ReturnType<typeof setInterval> | null = null;

  function clearTimer(): void {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  function startTimer(): void {
    clearTimer();
    timer = setInterval(() => {
      poll().catch((err) => console.warn('[usePolling] 轮询失败', err));
    }, intervalMs);
  }

  // tab 隐藏暂停、恢复可见立即补拉 + 重启 interval
  function onVisibility(): void {
    if (document.hidden) {
      clearTimer();
    } else {
      poll().catch((err) => console.warn('[usePolling] 恢复可见补拉失败', err));
      startTimer();
    }
  }

  onMounted(() => {
    if (immediate) {
      poll().catch((err) => console.warn('[usePolling] 首次拉取失败', err));
    }
    startTimer();
    document.addEventListener('visibilitychange', onVisibility);
  });

  onUnmounted(() => {
    clearTimer();
    document.removeEventListener('visibilitychange', onVisibility);
  });

  async function refresh(): Promise<void> {
    try {
      await forceRefresh();
    } catch (err) {
      console.warn('[usePolling] 手动刷新失败', err);
    }
  }

  return { refresh };
}
