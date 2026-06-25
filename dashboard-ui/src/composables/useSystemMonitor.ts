/* useSystemMonitor — 系统监控状态 + 5s 高频轮询 + 容器重启/关机。
 * restart/shutdown 与 Dashboard 同容器，调用成功后连接会立即断开 10-30s（restart）
 * 或需手动 docker start 恢复（shutdown）。响应可能完整收到也可能中途断连丢失，
 * 故 composable 在 success=true 或请求抛错时置 reconnecting=true，由视图层据此
 * 显示"连接断开"遮罩 + 提示重连；composable 只管调用与 toast，不假设能继续交互。 */
import { inject, onMounted, ref, type Ref } from 'vue';
import {
  getSystemInfo,
  restartBot as apiRestartBot,
  shutdownBot as apiShutdownBot,
  type SystemCurrent,
} from '@/api/domains/system';
import { usePolling } from '@/composables/usePolling';
import { useToastStore } from '@/stores/toast';

/** 24h 历史采样点（60s 间隔，最多 1440 点）。net_sent/net_recv 为累计字节数。 */
export interface SystemSample {
  t: string; // ISO 时间戳
  cpu: number;
  mem_percent: number;
  disk_percent: number;
  net_sent: number;
  net_recv: number;
}

export interface UseSystemMonitorReturn {
  current: Ref<SystemCurrent | null>;
  history: Ref<SystemSample[]>;
  loading: Ref<boolean>;
  error: Ref<string | null>;
  lastUpdated: Ref<number | null>;
  /** 手动 / 顶栏 force 刷新（走 usePolling.refresh → doRefresh） */
  refresh: () => Promise<void>;
  restarting: Ref<boolean>;
  shuttingDown: Ref<boolean>;
  /** restart/shutdown 成功或请求断连后置位，视图据此显示"连接断开"遮罩 */
  reconnecting: Ref<boolean>;
  restartBot: () => Promise<void>;
  shutdownBot: () => Promise<void>;
}

export function useSystemMonitor(pollIntervalMs = 5000): UseSystemMonitorReturn {
  const toast = useToastStore();

  const current = ref<SystemCurrent | null>(null);
  const history = ref<SystemSample[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const lastUpdated = ref<number | null>(null);
  const restarting = ref(false);
  const shuttingDown = ref(false);
  const reconnecting = ref(false);

  // 强制拉取最新快照：系统监控每次都要联网最新值，无需 TTL 去重
  async function doRefresh(): Promise<void> {
    error.value = null;
    loading.value = true;
    try {
      const data = await getSystemInfo();
      current.value = data.current;
      // 后端 history 为 Record<string,unknown>[]（t 为 ISO 字符串，余为数值），按 SystemSample 断言
      history.value = (data.history ?? []) as unknown as SystemSample[];
      lastUpdated.value = Date.now();
    } catch (e) {
      error.value = e instanceof Error ? e.message : '系统监控数据拉取失败';
    } finally {
      loading.value = false;
    }
  }

  // 轮询与手动刷新均走 doRefresh（force 路径，每次拉最新）
  const polling = usePolling({
    poll: doRefresh,
    forceRefresh: doRefresh,
    intervalMs: pollIntervalMs,
  });

  // 顶栏手动刷新注入：注册 usePolling 的 refresh（force=true）
  const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');

  onMounted(() => {
    registerRefresh?.(polling.refresh);
  });

  // restart：成功 → 容器即将被杀，连接断开；服务端返回失败 → docker 异常但连接仍在；
  // 请求抛错 → 容器已被杀/中途断连。后两者中仅"请求抛错"算断连。
  async function restartBot(): Promise<void> {
    if (restarting.value) return;
    restarting.value = true;
    try {
      const res = await apiRestartBot();
      if (res.success) {
        reconnecting.value = true;
        toast.push({
          type: 'warning',
          message:
            res.message ||
            'Bot 重启中，Dashboard 同容器，连接将断开，约 10-30 秒后自动恢复，请稍候刷新页面。',
          duration: 0,
        });
      } else {
        toast.push({ type: 'error', message: res.message || 'Bot 重启失败' });
      }
    } catch {
      // 请求中途断连（容器已被杀）→ 视为正在重启，提示用户等待重连
      reconnecting.value = true;
      toast.push({
        type: 'warning',
        message: '重启请求已发出，连接已断开，约 10-30 秒后刷新页面重连。',
        duration: 0,
      });
    } finally {
      restarting.value = false;
    }
  }

  // shutdown：成功 → 容器停止，连接断开且不会自动恢复（unless-stopped 需手动 start）；
  // 服务端返回失败 → 连接仍在；请求抛错 → 已断连。
  async function shutdownBot(): Promise<void> {
    if (shuttingDown.value) return;
    shuttingDown.value = true;
    try {
      const res = await apiShutdownBot();
      if (res.success) {
        reconnecting.value = true;
        toast.push({
          type: 'warning',
          message:
            res.message ||
            'Bot 已关机，连接已断开；容器重启策略为 unless-stopped，需手动 docker start 才能恢复。',
          duration: 0,
        });
      } else {
        toast.push({ type: 'error', message: res.message || 'Bot 关机失败' });
      }
    } catch {
      reconnecting.value = true;
      toast.push({
        type: 'warning',
        message: '关机请求已发出，连接已断开，需手动 docker start 恢复。',
        duration: 0,
      });
    } finally {
      shuttingDown.value = false;
    }
  }

  return {
    current,
    history,
    loading,
    error,
    lastUpdated,
    refresh: polling.refresh,
    restarting,
    shuttingDown,
    reconnecting,
    restartBot,
    shutdownBot,
  };
}
