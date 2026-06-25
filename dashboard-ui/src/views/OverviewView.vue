<script setup lang="ts">
/* OverviewView — 总览：接 /api/status、/api/stats/today、/api/health、/api/config/all。
 * KPI 按 importance 分层错落（禁等宽四卡）：2 个 primary 跨 2 列 + 6 个 secondary 跨 1 列。
 * 切走不重拉靠 store TTL 守卫；手动刷新经 registerRefresh 绑顶栏。 */
import { computed, inject, onMounted, ref } from 'vue';
import { Activity, Bot, Coins, Radio, ServerOff, Sparkles } from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseKpiTile from '@/components/ui/BaseKpiTile.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import { useTelemetryStore } from '@/stores/telemetry';
import { useConfigStore } from '@/stores/config';
import { useToastStore } from '@/stores/toast';
import { usePolling } from '@/composables/usePolling';

const telemetry = useTelemetryStore();
const config = useConfigStore();
const toast = useToastStore();

// 顶栏手动刷新注入：注册当前视图的 force 刷新函数
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh')!;

// 遥测拉取错误（telemetry store 不持有 error，视图层捕获）
const telemetryError = ref<string | null>(null);

// 轮询：TTL-respecting；手动刷新 force=true 绕 TTL
async function pollAll(): Promise<void> {
  telemetryError.value = null;
  try {
    await Promise.all([
      telemetry.refreshStatus(),
      telemetry.refreshStats(),
      telemetry.refreshHealth(),
    ]);
  } catch (e) {
    telemetryError.value = e instanceof Error ? e.message : '遥测加载失败';
  }
}

async function forceRefreshAll(): Promise<void> {
  telemetryError.value = null;
  try {
    await Promise.all([
      telemetry.refreshStatus(true),
      telemetry.refreshStats(true),
      telemetry.refreshHealth(true),
    ]);
    toast.push({ type: 'success', message: '总览已刷新', duration: 2000 });
  } catch (e) {
    telemetryError.value = e instanceof Error ? e.message : '刷新失败';
    toast.push({ type: 'error', message: '总览刷新失败，请重试' });
  }
}

const polling = usePolling({
  poll: pollAll,
  forceRefresh: forceRefreshAll,
  intervalMs: 30_000,
});

onMounted(() => {
  // 配置快照：仅在未加载时拉一次
  if (!config.snapshot) {
    config.load().catch(() => {
      toast.push({ type: 'error', message: '配置加载失败，配置概览暂不可用' });
    });
  }
  // 顶栏 @refresh → 当前视图 force 刷新
  registerRefresh?.(polling.refresh);
});

// ===== KPI 字段映射（端点 → 字段）=====
const status = computed(() => telemetry.status);
const stats = computed(() => telemetry.stats);
const health = computed(() => telemetry.health);

// /api/status.bot_status：running/starting/not_initialized/兜底 unknown
const botStatusRaw = computed(() => status.value?.bot_status ?? 'unknown');
const botStatusLabel = computed(() => {
  const map: Record<string, string> = {
    running: '运行中',
    starting: '启动中',
    not_initialized: '未初始化',
    unknown: '未知',
  };
  return map[botStatusRaw.value] ?? '未知';
});
const botUser = computed(() => status.value?.bot_user ?? null);

// /api/stats/today.messages / tokens
const messagesTotal = computed(() => stats.value?.messages?.total ?? null);
const messagesImage = computed(() => stats.value?.messages?.image ?? null);
const tokensTotal = computed(() => stats.value?.tokens?.total ?? null);
const apiCalls = computed(() => stats.value?.tokens?.call_count ?? null);

// /api/status 其余
const latencyMs = computed(() => status.value?.latency_ms ?? null);
const guildsCount = computed(() => status.value?.guilds_count ?? null);
const geminiAvailable = computed(() => status.value?.gemini_service_available ?? null);

// /api/health.status（仅静态 ok 探活）
const healthOk = computed(() => health.value?.status === 'ok');

// 任一错误（遥测视图层捕获 / 配置 store.error）
const error = computed(() => telemetryError.value || config.error || null);

// 骨架屏：核心数据未到且无错误
const showSkeleton = computed(() => !status.value && !stats.value && !error.value);

// 空状态：状态已到但 Bot 未初始化
const showEmpty = computed(
  () => !!status.value && botStatusRaw.value === 'not_initialized' && !stats.value,
);

// ===== 配置概览（/api/config/all 关键开关）=====
interface ConfigRow {
  label: string;
  value: string;
  tone: 'ok' | 'warn' | 'danger' | 'neutral';
}
const configRows = computed<ConfigRow[]>(() => {
  const snap = config.snapshot;
  if (!snap) return [];
  const ai = snap.ai ?? {};
  const imagen = snap.imagen ?? {};
  const voice = snap.voice ?? {};
  const coin = snap.coin ?? {};
  const webSearch = snap.web_search ?? {};
  const imageSearch = snap.image_search ?? {};
  const thread = snap.thread_auto_speaker ?? {};
  const spring = snap.spring_festival ?? {};
  const boolLabel = (v: unknown): string => (v ? '已启用' : '已停用');
  const webReady = !!(webSearch.grok_configured || webSearch.tavily_configured);
  return [
    { label: '人设', value: ai.persona_name ?? '—', tone: 'neutral' },
    { label: '当前模型', value: ai.model ?? '—', tone: 'neutral' },
    { label: 'AI API Key', value: ai.has_api_key ? '已配置' : '未配置', tone: ai.has_api_key ? 'ok' : 'danger' },
    { label: 'Imagen', value: boolLabel(imagen.enabled), tone: imagen.enabled ? 'ok' : 'warn' },
    { label: '语音合成', value: boolLabel(voice.enabled), tone: voice.enabled ? 'ok' : 'warn' },
    { label: 'Web 搜索', value: webReady ? '已配置' : '未配置', tone: webReady ? 'ok' : 'warn' },
    { label: '图片搜索', value: imageSearch.configured ? '已配置' : '未配置', tone: imageSearch.configured ? 'ok' : 'warn' },
    { label: '串门助手', value: boolLabel(thread.enabled), tone: thread.enabled ? 'ok' : 'neutral' },
    { label: '春节活动', value: boolLabel(spring.enabled), tone: spring.enabled ? 'ok' : 'neutral' },
    { label: '货币名称', value: coin.currency_name ?? '—', tone: 'neutral' },
  ] satisfies ConfigRow[];
});

function retry(): void {
  polling.refresh();
}

function reloadConfig(): void {
  config.load().catch(() => toast.push({ type: 'error', message: '配置加载失败' }));
}
</script>

<template>
  <div class="view">
    <BaseSectionTitle :icon="Activity" title="总览" subtitle="月月工坊台实时状态" />

    <!-- 元信息行：Bot 用户名 / 同进程标记 / 探活 -->
    <div v-if="status || botUser" class="meta">
      <span v-if="botUser" class="meta__item">
        <Bot :size="14" aria-hidden="true" />
        <span>{{ botUser }}</span>
      </span>
      <span v-if="status?.integrated_mode" class="meta__chip">同进程</span>
      <span class="meta__item" :class="healthOk ? 'is-ok' : 'is-down'">
        <Radio :size="14" aria-hidden="true" />
        <span>探活 {{ healthOk ? '正常' : '异常' }}</span>
      </span>
    </div>

    <!-- 错误横幅：inline + 重试 -->
    <div v-if="error && !showSkeleton" class="error-banner" role="alert">
      <div class="error-banner__text">
        <ServerOff :size="18" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>
      <BaseButton variant="ghost" size="sm" :icon="Activity" @click="retry">重试</BaseButton>
    </div>

    <!-- 骨架屏：按 primary/secondary 尺寸占位 -->
    <div v-if="showSkeleton" class="kpi-grid" aria-busy="true" aria-live="polite">
      <div class="kpi-grid__primary"><BaseSkeleton height="6.5rem" rounded="var(--radius-lg)" /></div>
      <div class="kpi-grid__secondary"><BaseSkeleton height="5rem" rounded="var(--radius-lg)" /></div>
      <div class="kpi-grid__primary"><BaseSkeleton height="6.5rem" rounded="var(--radius-lg)" /></div>
      <div class="kpi-grid__secondary"><BaseSkeleton height="5rem" rounded="var(--radius-lg)" /></div>
      <div class="kpi-grid__secondary"><BaseSkeleton height="5rem" rounded="var(--radius-lg)" /></div>
      <div class="kpi-grid__secondary"><BaseSkeleton height="5rem" rounded="var(--radius-lg)" /></div>
      <div class="kpi-grid__secondary"><BaseSkeleton height="5rem" rounded="var(--radius-lg)" /></div>
      <div class="kpi-grid__secondary"><BaseSkeleton height="5rem" rounded="var(--radius-lg)" /></div>
    </div>

    <!-- 空状态：Bot 未初始化 -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="Bot"
      title="月月尚未就绪"
      description="Bot 进程未初始化，等待启动后即可显示实时数据。"
      action-text="立即刷新"
      :action-icon="Activity"
      @action="retry"
    />

    <!-- KPI 分层网格：2 primary(span2) + 6 secondary(span1) -->
    <div v-else class="kpi-grid">
      <BaseKpiTile
        class="kpi-grid__primary"
        :class="`is-${botStatusRaw}`"
        label="Bot 运行状态"
        :value="botStatusLabel"
        importance="primary"
      />
      <BaseKpiTile
        class="kpi-grid__secondary"
        label="Discord 延迟"
        :value="latencyMs === null ? '--' : latencyMs"
        unit="ms"
        importance="secondary"
      />
      <BaseKpiTile
        class="kpi-grid__primary"
        label="今日消息总数"
        :value="messagesTotal === null ? '--' : messagesTotal"
        unit="条"
        importance="primary"
      />
      <BaseKpiTile
        class="kpi-grid__secondary"
        label="已加入服务器"
        :value="guildsCount === null ? '--' : guildsCount"
        unit="个"
        importance="secondary"
      />
      <BaseKpiTile
        class="kpi-grid__secondary"
        label="今日 Token"
        :value="tokensTotal === null ? '--' : tokensTotal"
        importance="secondary"
      />
      <BaseKpiTile
        class="kpi-grid__secondary"
        label="API 调用"
        :value="apiCalls === null ? '--' : apiCalls"
        unit="次"
        importance="secondary"
      />
      <BaseKpiTile
        class="kpi-grid__secondary"
        label="Gemini 服务"
        :value="geminiAvailable === null ? '--' : geminiAvailable ? '已就绪' : '未就绪'"
        importance="secondary"
      />
      <BaseKpiTile
        class="kpi-grid__secondary"
        label="今日图片消息"
        :value="messagesImage === null ? '--' : messagesImage"
        unit="张"
        importance="secondary"
      />
    </div>

    <!-- 配置概览：关键开关一览 -->
    <section class="config-overview">
      <BaseSectionTitle :icon="Sparkles" title="配置概览" subtitle="关键开关一览" />

      <div v-if="config.loading && !config.snapshot" class="config-overview__skeleton">
        <BaseSkeleton v-for="i in 8" :key="i" height="1.5rem" rounded="var(--radius-md)" />
      </div>

      <BaseEmpty
        v-else-if="!config.snapshot && config.error"
        :icon="Coins"
        title="配置未加载"
        description="配置快照拉取失败，配置概览暂不可用。"
        action-text="重新加载"
        :action-icon="Activity"
        @action="reloadConfig"
      />

      <BaseEmpty
        v-else-if="config.snapshot && !configRows.length"
        :icon="Coins"
        title="暂无配置概览"
        description="配置快照已加载，但暂无可展示字段。"
      />

      <ul v-else-if="configRows.length" class="config-list">
        <li v-for="row in configRows" :key="row.label" class="config-list__row">
          <span class="config-list__label">{{ row.label }}</span>
          <span class="config-list__value" :class="`tone-${row.tone}`">{{ row.value }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

/* ===== 元信息行 ===== */
.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}
.meta__item {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}
.meta__item.is-ok { color: var(--success); }
.meta__item.is-down { color: var(--danger); }
.meta__chip {
  padding: 0 var(--space-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: var(--text-xs);
}

/* ===== 错误横幅 ===== */
.error-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  background: color-mix(in oklch, var(--danger) 10%, var(--bg-surface));
  border: 1px solid color-mix(in oklch, var(--danger) 40%, transparent);
  border-radius: var(--radius-md);
}
.error-banner__text {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--danger);
  font-size: var(--text-sm);
}

/* ===== KPI 分层网格：3 列，primary 跨 2 列，secondary 跨 1 列 ===== */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-4);
}
.kpi-grid__primary { grid-column: span 2; }
.kpi-grid__secondary { grid-column: span 1; }

/* 悬停 / 激活：暖面浮起，禁发光 */
.kpi-grid :deep(.kpi) {
  transition: border-color var(--dur-micro) var(--ease-out-quart),
    background-color var(--dur-micro) var(--ease-out-quart);
}
.kpi-grid :deep(.kpi:hover) {
  border-color: var(--border-strong);
  background: var(--bg-surface-2);
}
.kpi-grid :deep(.kpi:active) { filter: brightness(0.97); }

/* Bot 状态数值语义色（非装饰强调） */
.is-running :deep(.kpi__value) { color: var(--success); }
.is-starting :deep(.kpi__value) { color: var(--warning); }
.is-not_initialized :deep(.kpi__value) { color: var(--danger); }
.is-unknown :deep(.kpi__value) { color: var(--text-muted); }

/* ===== 配置概览 ===== */
.config-overview {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.config-overview__skeleton {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-3);
}
.config-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-2) var(--space-6);
  list-style: none;
  padding: var(--space-4);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.config-list__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--text-sm);
}
.config-list__label { color: var(--text-muted); }
.config-list__value { color: var(--text-primary); font-weight: var(--fw-medium); }
.config-list__value.tone-ok { color: var(--success); }
.config-list__value.tone-warn { color: var(--warning); }
.config-list__value.tone-danger { color: var(--danger); }
.config-list__value.tone-neutral { color: var(--text-secondary); }

/* ===== 移动端：单列堆叠 ===== */
@media (max-width: 768px) {
  .kpi-grid { grid-template-columns: 1fr; }
  .kpi-grid__primary,
  .kpi-grid__secondary { grid-column: span 1; }
  .config-overview__skeleton,
  .config-list { grid-template-columns: 1fr; }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .kpi-grid :deep(.kpi) { transition: none; }
}
</style>
