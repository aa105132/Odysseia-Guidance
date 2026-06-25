<script setup lang="ts">
/* StatsView — 今日统计与模型分布。
 * 接 /api/stats/today（telemetry.refreshStats），TTL 30s + 可见 tab 轮询守卫 + 顶栏手动刷新。
 * 模型分布用 models_today（已降序，无占比字段，前端按 usage_count/sum 自算百分比），
 * 纯 CSS 水平 bar 图，bar 用 var(--accent) 琥珀，轨道用 var(--bg-inset) 凹陷底。
 * KPI 按 importance 分层错落（primary 跨 2 列、均次跨 2 列），禁等宽四卡网格。 */
import { computed, inject, onMounted, ref } from 'vue';
import { BarChart3, RefreshCw, AlertCircle } from 'lucide-vue-next';
import { useTelemetryStore } from '@/stores/telemetry';
import { useToastStore } from '@/stores/toast';
import { usePolling } from '@/composables/usePolling';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseKpiTile from '@/components/ui/BaseKpiTile.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseButton from '@/components/ui/BaseButton.vue';

// /api/stats/today 响应结构（recon 提取，仅取本视图所用字段）
interface ModelUsage {
  model_name: string;
  usage_count: number;
}
interface StatsToday {
  date?: string;
  messages?: { channel: number; dm: number; image: number; total: number };
  tokens?: {
    input: number;
    output: number;
    total: number;
    call_count: number;
    avg_per_call: number;
  };
  models_today?: ModelUsage[];
}

const telemetry = useTelemetryStore();
const toast = useToastStore();

// 视图内错误态：refreshStats 抛错时捕获，置 inline 错误 + toast；成功清空
const errorMsg = ref<string | null>(null);

// poll/forceRefresh 包装：捕获错误，置 errorMsg + toast；usePolling 不重复抛
async function poll(): Promise<void> {
  try {
    await telemetry.refreshStats(); // TTL-respecting，未过期直接复用缓存
    errorMsg.value = null;
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '加载统计失败';
    errorMsg.value = msg;
    toast.push({ type: 'error', message: msg, title: '统计加载失败' });
  }
}

async function forceRefresh(): Promise<void> {
  try {
    await telemetry.refreshStats(true); // 绕 TTL 强拉
    errorMsg.value = null;
    toast.push({ type: 'success', message: '已刷新统计', duration: 2000 });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : '加载统计失败';
    errorMsg.value = msg;
    toast.push({ type: 'error', message: msg, title: '统计加载失败' });
  }
}

// 轮询：60s 间隔（统计变化比状态慢）；挂载即拉；tab 不可见暂停
const polling = usePolling({
  poll,
  forceRefresh,
  intervalMs: 60_000,
  immediate: true,
});

// 顶栏手动刷新注册：inject App.vue provide 的 registerRefresh
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh')!;
onMounted(() => {
  registerRefresh(polling.refresh); // 顶栏刷新 → force=true
});

// 数据抽取
const stats = computed<StatsToday | null>(() => telemetry.stats as StatsToday | null);
const messages = computed(() => stats.value?.messages ?? null);
const tokens = computed(() => stats.value?.tokens ?? null);
const modelsToday = computed<ModelUsage[]>(() => stats.value?.models_today ?? []);

// 模型分布：总次数 + 每行占比（防 0 除）
const totalUsage = computed(() =>
  modelsToday.value.reduce((sum, m) => sum + (m.usage_count ?? 0), 0),
);
const modelRows = computed(() => {
  const total = totalUsage.value || 1;
  return modelsToday.value
    .map((m) => ({
      name: m.model_name ?? '未知模型',
      count: m.usage_count ?? 0,
      percent: ((m.usage_count ?? 0) / total) * 100,
    }))
    .sort((a, b) => b.count - a.count); // 兜底降序（端点已降序，防御）
});

// 状态派生
const isLoading = computed(() => telemetry.loading === 'stats');
const hasData = computed(() => stats.value != null);
// 首次加载（无缓存）走骨架；有缓存时静默重拉，不闪骨架
const showSkeleton = computed(() => isLoading.value && !hasData.value);
const showEmpty = computed(() => !hasData.value && !isLoading.value && !errorMsg.value);
const showError = computed(() => !!errorMsg.value && !hasData.value);

// 数字格式化：千分位；null 显示 --
function formatNum(n: number | null | undefined): string {
  if (n == null) return '--';
  return n.toLocaleString('zh-CN');
}
// 均次 token：浮点保留 1 位
function formatAvg(n: number | null | undefined): string {
  if (n == null) return '--';
  return n.toFixed(1);
}

// 区段副标题：拼上今日日期（stats.date 为 Asia/Shanghai ISO YYYY-MM-DD）
const subtitle = computed(() => {
  const d = stats.value?.date;
  if (!d || typeof d !== 'string') return '今日数据与模型分布';
  const m = d.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return '今日数据与模型分布';
  return `今日数据与模型分布 · ${Number(m[2])}月${Number(m[3])}日`;
});

// 重试：force 刷新
function onRetry(): void {
  void polling.refresh();
}
</script>

<template>
  <div class="view">
    <BaseSectionTitle :icon="BarChart3" title="统计" :subtitle="subtitle" />

    <!-- inline 错误（有缓存时不阻塞数据，仅置顶提示） -->
    <div v-if="errorMsg && hasData" class="notice notice--error" role="alert">
      <AlertCircle class="notice__icon" aria-hidden="true" />
      <span class="notice__text">{{ errorMsg }}</span>
      <BaseButton variant="ghost" size="sm" :icon="RefreshCw" :loading="isLoading" @click="onRetry">
        重试
      </BaseButton>
    </div>

    <!-- 骨架（首次加载） -->
    <template v-if="showSkeleton">
      <div class="kpi-grid">
        <BaseSkeleton class="kpi-grid__primary" height="7.5rem" />
        <BaseSkeleton height="5.5rem" />
        <BaseSkeleton height="5.5rem" />
        <BaseSkeleton height="5.5rem" />
        <BaseSkeleton height="5.5rem" />
        <BaseSkeleton height="5.5rem" />
        <BaseSkeleton class="kpi-grid__wide" height="5.5rem" />
      </div>
      <div class="chart-skeleton">
        <BaseSkeleton height="1.25rem" width="40%" />
        <BaseSkeleton v-for="i in 4" :key="i" height="2.5rem" />
      </div>
    </template>

    <!-- 错误（无缓存） -->
    <div v-else-if="showError" class="error-block" role="alert">
      <AlertCircle class="error-block__icon" aria-hidden="true" />
      <p class="error-block__title font-display">统计加载失败</p>
      <p class="error-block__desc">{{ errorMsg }}</p>
      <BaseButton variant="secondary" size="md" :icon="RefreshCw" :loading="isLoading" @click="onRetry">
        重新加载
      </BaseButton>
    </div>

    <!-- 空状态 -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="BarChart3"
      title="今日暂无数据"
      description="Bot 启动后尚未产生今日统计，稍后会自动刷新。"
      action-text="手动刷新"
      :action-icon="RefreshCw"
      @action="onRetry"
    />

    <!-- 数据态 -->
    <template v-else-if="hasData">
      <!-- KPI 分层：primary 跨 2 列、均次跨 2 列，禁等宽四卡 -->
      <div class="kpi-grid">
        <BaseKpiTile
          class="kpi-grid__primary"
          label="今日总消息量"
          :value="formatNum(messages?.total)"
          importance="primary"
        />
        <BaseKpiTile label="图片消息" :value="formatNum(messages?.image)" importance="secondary" />
        <BaseKpiTile label="频道消息" :value="formatNum(messages?.channel)" importance="secondary" />
        <BaseKpiTile label="私信消息" :value="formatNum(messages?.dm)" importance="secondary" />
        <BaseKpiTile label="Token 总量" :value="formatNum(tokens?.total)" importance="secondary" />
        <BaseKpiTile label="API 调用" :value="formatNum(tokens?.call_count)" importance="secondary" />
        <BaseKpiTile
          class="kpi-grid__wide"
          label="均次 Token"
          :value="formatAvg(tokens?.avg_per_call)"
          unit="tok/次"
          importance="secondary"
        />
      </div>

      <!-- 模型分布：纯 CSS 水平 bar 图 -->
      <section class="distro" aria-label="模型分布">
        <header class="distro__head">
          <h3 class="distro__title font-display">模型分布</h3>
          <p class="distro__meta">
            共 {{ modelRows.length }} 个模型 · {{ formatNum(totalUsage) }} 次调用
          </p>
        </header>

        <ul v-if="modelRows.length" class="distro__list">
          <li v-for="(row, idx) in modelRows" :key="row.name + idx" class="bar-row">
            <span class="bar-row__name" :title="row.name">{{ row.name }}</span>
            <div class="bar-row__track" :aria-label="`${row.name} 占比 ${row.percent.toFixed(1)}%`">
              <span
                class="bar-row__fill"
                :style="{ width: Math.max(row.percent, 0.5) + '%' }"
              />
            </div>
            <span class="bar-row__count font-display">{{ formatNum(row.count) }}</span>
            <span class="bar-row__pct">{{ row.percent.toFixed(1) }}%</span>
          </li>
        </ul>

        <BaseEmpty
          v-else
          :icon="BarChart3"
          title="今日暂无模型调用"
          description="尚未产生模型调用记录，产生后将自动显示分布。"
        />
      </section>
    </template>
  </div>
</template>

<style scoped>
.view {
  display: flex;
  flex-direction: column;
  gap: var(--space-8);
}

/* KPI 网格：3 列错落，primary 跨 2、wide 跨 3，禁等宽四卡 */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-4);
}
.kpi-grid__primary {
  grid-column: span 2;
}
.kpi-grid__wide {
  grid-column: span 2;
}
@media (max-width: 768px) {
  .kpi-grid {
    grid-template-columns: 1fr;
  }
  .kpi-grid__primary,
  .kpi-grid__wide {
    grid-column: span 1;
  }
}

/* 骨架占位块 */
.chart-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}

/* inline 提示条（有缓存时的非阻塞错误） */
.notice {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
}
.notice--error {
  background: color-mix(in oklch, var(--danger) 8%, var(--bg-surface));
  border-color: color-mix(in oklch, var(--danger) 35%, transparent);
}
.notice__icon {
  flex: none;
  width: 1.125rem;
  height: 1.125rem;
  color: var(--danger);
}
.notice__text {
  flex: 1;
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

/* 错误块（无缓存） */
.error-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-8) var(--space-4);
  text-align: center;
}
.error-block__icon {
  width: var(--space-8);
  height: var(--space-8);
  color: var(--danger);
}
.error-block__title {
  font-size: var(--text-lg);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}
.error-block__desc {
  max-width: 32rem;
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

/* 模型分布 */
.distro {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.distro__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.distro__title {
  font-size: var(--text-lg);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}
.distro__meta {
  font-size: var(--text-sm);
  color: var(--text-muted);
}

.distro__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* 每行：模型名 | 轨道+fill | 次数 | 占比 */
.bar-row {
  display: grid;
  grid-template-columns: minmax(8rem, 16rem) 1fr auto auto;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  transition: background-color var(--dur-micro) var(--ease-out-quart);
}
.bar-row:hover {
  background: var(--bg-surface-2);
}
.bar-row:active {
  background: var(--bg-inset);
}

.bar-row__name {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bar-row__track {
  position: relative;
  height: 0.625rem;
  background: var(--bg-inset);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.bar-row__fill {
  display: block;
  height: 100%;
  background: var(--accent);
  border-radius: var(--radius-sm);
  /* fill 宽度过渡：首次渲染有微动效；reduced-motion 下禁用 */
  transition: width var(--dur-list) var(--ease-out-quart);
}

.bar-row__count {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  min-width: 3rem;
  text-align: right;
}
.bar-row__pct {
  font-size: var(--text-xs);
  color: var(--text-muted);
  min-width: 3.5rem;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

@media (max-width: 768px) {
  .bar-row {
    grid-template-columns: minmax(6rem, 10rem) 1fr auto;
  }
  .bar-row__pct {
    display: none;
  }
}

/* reduced-motion：禁 fill 宽度过渡 + 骨架由组件自身处理 */
@media (prefers-reduced-motion: reduce) {
  .bar-row,
  .bar-row__fill {
    transition: none;
  }
}
</style>
