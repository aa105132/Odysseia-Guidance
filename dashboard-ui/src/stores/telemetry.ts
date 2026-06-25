/* telemetry.ts — 运行态遥测聚合（Pinia）
 * 统一替代旧 SPA 67处散落 fetch + 切 tab 重拉。带 TTL 去重：30s 内同指标复用缓存。
 * 阶段0骨架：仅暴露拉取动作与缓存 state；可见 tab 轮询守卫由视图层阶段1补。 */
// TODO: 阶段1用 openapi-typescript 自动生成类型替换手写 any
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { client } from '@/api/client';

const TTL = 30_000; // 同指标 30s 内复用缓存，过期才重拉

type MetricName = 'status' | 'stats' | 'health';
type LoadingKey = '' | MetricName;

export const useTelemetryStore = defineStore('telemetry', () => {
  const status = ref<any | null>(null);
  const stats = ref<any | null>(null);
  const health = ref<any | null>(null);
  // 空串表示无在途请求；仅有一个拉取进行时占位对应 key
  const loading = ref<LoadingKey>('');

  // 各指标上次成功拉取的时间戳；0 表示从未拉过
  const lastFetch = ref<Record<MetricName, number>>({
    status: 0,
    stats: 0,
    health: 0,
  });

  // 是否过期（可重拉）；force 拉取时由调用处绕过此判断
  function isStale(name: MetricName): boolean {
    return Date.now() - lastFetch.value[name] >= TTL;
  }

  // 三个端点：/api/status、/api/stats/today、/api/health
  // TTL 守卫：未过期且非 force 时直接复用缓存，不打网络
  async function refreshStatus(force = false): Promise<void> {
    if (!force && !isStale('status')) return;
    loading.value = 'status';
    try {
      status.value = await client.get('/api/status');
      lastFetch.value.status = Date.now();
    } finally {
      loading.value = '';
    }
  }

  async function refreshStats(force = false): Promise<void> {
    if (!force && !isStale('stats')) return;
    loading.value = 'stats';
    try {
      stats.value = await client.get('/api/stats/today');
      lastFetch.value.stats = Date.now();
    } finally {
      loading.value = '';
    }
  }

  async function refreshHealth(force = false): Promise<void> {
    if (!force && !isStale('health')) return;
    loading.value = 'health';
    try {
      health.value = await client.get('/api/health');
      lastFetch.value.health = Date.now();
    } finally {
      loading.value = '';
    }
  }

  return {
    status,
    stats,
    health,
    loading,
    lastFetch,
    isStale,
    refreshStatus,
    refreshStats,
    refreshHealth,
  };
});
