<script setup lang="ts">
/* SystemView — 系统监控 + 容器运维。
 * useSystemMonitor 提供 5s 轮询的 current/history + restart/shutdown，
 * 内部已 inject('registerRefresh') 并 onMounted 注册顶栏 force 刷新，视图无需重复注册。
 * KPI 分层：CPU primary，内存/磁盘/网络 secondary（非等宽 bento）；
 * Sparkline 画 24h 趋势，网络为 psutil 累计计数器需算相邻样本速率（KB/s）。
 * 重启/关机与 Dashboard 同容器，成功或断连后 composable 置 reconnecting，
 * 视图据此显示断连遮罩，提示用户刷新页面重连。 */
import { computed, nextTick, ref, watch } from 'vue';
import { AlertTriangle, PowerOff, RefreshCw, RotateCw, Server } from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseKpiTile from '@/components/ui/BaseKpiTile.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import Sparkline from '@/components/system/Sparkline.vue';
import { useSystemMonitor } from '@/composables/useSystemMonitor';

// 5s 高频轮询；composable 内部已注册顶栏刷新
const {
  current,
  history,
  loading,
  error,
  lastUpdated,
  refresh,
  restarting,
  shuttingDown,
  reconnecting,
  restartBot,
  shutdownBot,
} = useSystemMonitor(5000);

// ===== 格式化助手 =====
function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}
function formatPct(n: number): string {
  return Number.isFinite(n) ? n.toFixed(1) : '--';
}

// ===== 当前快照派生显示值 =====
const cpuPct = computed(() => (current.value ? formatPct(current.value.cpu) : '--'));
const memPct = computed(() => (current.value ? formatPct(current.value.mem_percent) : '--'));
const memDetail = computed(() =>
  current.value ? `${formatBytes(current.value.mem_used)} / ${formatBytes(current.value.mem_total)}` : '--',
);
const diskPct = computed(() => (current.value ? formatPct(current.value.disk_percent) : '--'));
const diskDetail = computed(() =>
  current.value ? `${formatBytes(current.value.disk_used)} / ${formatBytes(current.value.disk_total)}` : '--',
);
const netTotal = computed(() =>
  current.value ? formatBytes(current.value.net_sent + current.value.net_recv) : '--',
);
const netDetail = computed(() =>
  current.value ? `↑${formatBytes(current.value.net_sent)} ↓${formatBytes(current.value.net_recv)}` : '--',
);

// ===== Sparkline 数据序列 =====
const cpuSeries = computed(() => history.value.map((h) => h.cpu));
const memSeries = computed(() => history.value.map((h) => h.mem_percent));
const diskSeries = computed(() => history.value.map((h) => h.disk_percent));

// 网络：history 的 net_sent/net_recv 为 psutil 累计计数器，画速率需相邻样本差/时间差。
// t 为 ISO 字符串（无 Z 后缀），仅取相对差，时区解释不影响 delta；解析失败回退 60s 采样间隔。
const netRateSeries = computed<number[]>(() => {
  const h = history.value;
  if (h.length < 2) return [];
  const out: number[] = [];
  for (let i = 1; i < h.length; i++) {
    const prev = h[i - 1];
    const cur = h[i];
    const tPrev = new Date(prev.t).getTime();
    const tCur = new Date(cur.t).getTime();
    const dt = Number.isFinite(tPrev) && Number.isFinite(tCur) && tCur > tPrev ? (tCur - tPrev) / 1000 : 60;
    if (dt <= 0) continue;
    const dSent = Math.max(0, cur.net_sent - prev.net_sent);
    const dRecv = Math.max(0, cur.net_recv - prev.net_recv);
    out.push((dSent + dRecv) / dt / 1024); // KB/s
  }
  return out;
});

const historyEmpty = computed(() => history.value.length === 0);

// ===== 状态分支 =====
const hasCurrent = computed(() => !!current.value);
const showSkeleton = computed(() => loading.value && !hasCurrent.value);
const showLoadError = computed(() => !loading.value && !!error.value && !hasCurrent.value);
const showEmpty = computed(() => !loading.value && !error.value && !hasCurrent.value);

const lastUpdatedText = computed(() => {
  const t = lastUpdated.value;
  if (!t) return '--';
  return new Date(t).toLocaleTimeString('zh-CN', { hour12: false });
});

// ===== 容器运维确认 =====
const restartConfirm = ref(false);
const shutdownConfirm = ref(false);
const reconnectReason = ref<'restart' | 'shutdown' | null>(null);

// 断连遮罩出现时把焦点移入对话框（致命重连遮罩，唯一动作刷新页面）
const reconnectCard = ref<HTMLElement | null>(null);
watch(reconnecting, (v) => {
  if (!v) return;
  nextTick(() => reconnectCard.value?.querySelector('button')?.focus());
});

async function onConfirmRestart(): Promise<void> {
  reconnectReason.value = 'restart';
  await restartBot();
}
async function onConfirmShutdown(): Promise<void> {
  reconnectReason.value = 'shutdown';
  await shutdownBot();
}

const reconnectMessage = computed(() => {
  if (reconnectReason.value === 'shutdown') {
    return 'Bot 已关机，连接已断开。容器重启策略为 unless-stopped，需手动执行 docker start Odysseia_Guidance 才能恢复。';
  }
  return 'Bot 正在重启，Dashboard 与 Bot 同容器，连接将断开，约 10-30 秒后自动恢复。请稍候刷新页面重连。';
});

function reloadPage(): void {
  window.location.reload();
}
function retry(): void {
  refresh().catch(() => {
    /* 错误已由 composable 置入 error */
  });
}
</script>

<template>
  <div class="view">
    <BaseSectionTitle :icon="Server" title="系统" subtitle="资源监控与容器运维" />

    <!-- 元信息：最后更新 + 手动刷新 -->
    <div v-if="hasCurrent || lastUpdatedText !== '--'" class="meta-row">
      <span class="meta-row__time">最后更新：{{ lastUpdatedText }}</span>
      <BaseButton
        variant="ghost"
        size="sm"
        :icon="RefreshCw"
        :loading="loading"
        :disabled="reconnecting"
        aria-label="手动刷新系统监控"
        @click="retry"
      >
        刷新
      </BaseButton>
    </div>

    <!-- 错误横幅（有旧数据时 inline 提示） -->
    <div v-if="error && hasCurrent" class="error-banner" role="alert">
      <div class="error-banner__text">
        <AlertTriangle :size="18" aria-hidden="true" />
        <span>刷新失败：{{ error }}</span>
      </div>
      <BaseButton variant="ghost" size="sm" :icon="RefreshCw" @click="retry">重试</BaseButton>
    </div>

    <!-- 骨架屏 -->
    <div v-if="showSkeleton" class="sys-skeleton" aria-busy="true" aria-live="polite">
      <div class="kpi-grid">
        <BaseSkeleton class="kpi--cpu" height="6rem" rounded="var(--radius-lg)" />
        <BaseSkeleton class="kpi--mem" height="6rem" rounded="var(--radius-lg)" />
        <BaseSkeleton class="kpi--disk" height="6rem" rounded="var(--radius-lg)" />
        <BaseSkeleton class="kpi--net" height="6rem" rounded="var(--radius-lg)" />
      </div>
      <div class="trends__grid">
        <BaseSkeleton class="trend--cpu" height="5rem" rounded="var(--radius-md)" />
        <BaseSkeleton class="trend--mem" height="5rem" rounded="var(--radius-md)" />
        <BaseSkeleton class="trend--disk" height="5rem" rounded="var(--radius-md)" />
        <BaseSkeleton class="trend--net" height="5rem" rounded="var(--radius-md)" />
      </div>
    </div>

    <!-- 加载错误且无数据 -->
    <BaseEmpty
      v-else-if="showLoadError"
      :icon="AlertTriangle"
      title="系统监控加载失败"
      :description="error ?? '请检查后端服务后重试。'"
      action-text="重试"
      :action-icon="RefreshCw"
      @action="retry"
    />

    <!-- 无数据空态 -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="Server"
      title="暂无系统监控数据"
      description="尚未读取到任何系统监控数据，请尝试重新加载。"
      action-text="重新加载"
      :action-icon="RefreshCw"
      @action="retry"
    />

    <!-- 数据区 -->
    <template v-else-if="hasCurrent">
      <!-- KPI 分层（非等宽 bento）：CPU primary 大号，内存/磁盘/网络 secondary -->
      <section class="kpi-grid" aria-label="当前资源快照">
        <BaseKpiTile class="kpi--cpu" importance="primary" label="CPU 占用" :value="cpuPct" unit="%" />
        <BaseKpiTile
          class="kpi--mem"
          importance="secondary"
          label="内存"
          :value="memPct"
          :unit="`% · ${memDetail}`"
        />
        <BaseKpiTile
          class="kpi--disk"
          importance="secondary"
          label="磁盘"
          :value="diskPct"
          :unit="`% · ${diskDetail}`"
        />
        <BaseKpiTile
          class="kpi--net"
          importance="secondary"
          label="网络累计"
          :value="netTotal"
          :unit="netDetail"
        />
      </section>

      <!-- 24h 趋势 -->
      <section class="trends">
        <div class="trends__head">
          <h3 class="trends__title font-display">24 小时趋势</h3>
          <span v-if="historyEmpty" class="trends__hint">历史数据积累中，每 60 秒采样一次</span>
        </div>
        <div class="trends__grid">
          <div class="trend-card trend--cpu">
            <Sparkline :data="cpuSeries" label="CPU" unit="%" :min="0" :max="100" />
          </div>
          <div class="trend-card trend--mem">
            <Sparkline :data="memSeries" label="内存占用" unit="%" :min="0" :max="100" />
          </div>
          <div class="trend-card trend--disk">
            <Sparkline :data="diskSeries" label="磁盘占用" unit="%" :min="0" :max="100" />
          </div>
          <div class="trend-card trend--net">
            <Sparkline :data="netRateSeries" label="网络吞吐" unit="KB/s" />
          </div>
        </div>
      </section>
    </template>

    <!-- 容器运维 -->
    <section class="ops card" aria-labelledby="ops-title">
      <h3 id="ops-title" class="card__title font-display">
        <PowerOff class="card__title-icon" :size="16" aria-hidden="true" />
        容器运维
      </h3>
      <p class="ops__hint">
        Dashboard 与 Bot 同容器运行，重启或关机会立即断开当前连接。
        重启约 10-30 秒后自动恢复；关机后需手动执行 docker start Odysseia_Guidance 恢复。
      </p>
      <div class="ops__actions">
        <BaseButton
          variant="danger"
          :loading="restarting"
          :disabled="reconnecting || shuttingDown"
          :icon="RotateCw"
          aria-label="重启 Bot 容器，将断开当前连接"
          @click="restartConfirm = true"
        >
          重启 Bot
        </BaseButton>
        <BaseButton
          variant="danger"
          :loading="shuttingDown"
          :disabled="reconnecting || restarting"
          :icon="PowerOff"
          aria-label="关机 Bot 容器，需手动恢复"
          @click="shutdownConfirm = true"
        >
          关机 Bot
        </BaseButton>
      </div>
    </section>

    <!-- 断连遮罩：restart/shutdown 成功或请求断连后置位 -->
    <div
      v-if="reconnecting"
      class="reconnect-overlay"
      role="alertdialog"
      aria-labelledby="reconnect-title"
      aria-describedby="reconnect-desc"
    >
      <div ref="reconnectCard" class="reconnect-card">
        <div class="reconnect-card__icon-wrap">
          <AlertTriangle class="reconnect-card__icon" aria-hidden="true" />
        </div>
        <h3 id="reconnect-title" class="reconnect-card__title font-display">连接已断开</h3>
        <p id="reconnect-desc" class="reconnect-card__msg">{{ reconnectMessage }}</p>
        <BaseButton variant="primary" :icon="RefreshCw" @click="reloadPage">刷新页面重连</BaseButton>
      </div>
    </div>

    <!-- 确认弹窗（BaseModal 内含 role=dialog aria-modal，焦点陷阱） -->
    <BaseConfirmDialog
      v-model="restartConfirm"
      variant="danger"
      title="确认重启 Bot？"
      message="重启将断开当前连接，Dashboard 与 Bot 同容器，约 10-30 秒后自动恢复。确认重启？"
      confirm-text="重启"
      @confirm="onConfirmRestart"
    />
    <BaseConfirmDialog
      v-model="shutdownConfirm"
      variant="danger"
      title="确认关机 Bot？"
      message="关机后 Bot 停止运行，容器重启策略为 unless-stopped，需手动 docker start 才能恢复。确认关机？"
      confirm-text="关机"
      @confirm="onConfirmShutdown"
    />
  </div>
</template>

<style scoped>
.view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  position: relative;
}

/* ===== 元信息行 ===== */
.meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.meta-row__time {
  font-size: var(--text-sm);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

/* ===== 错误横幅（实心底色，禁 blur） ===== */
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

/* ===== 骨架屏 ===== */
.sys-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

/* ===== KPI bento（非等宽，避免等宽四卡） ===== */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: var(--space-4);
}
.kpi--cpu {
  grid-column: span 7;
}
.kpi--mem {
  grid-column: span 5;
}
.kpi--disk {
  grid-column: span 5;
}
.kpi--net {
  grid-column: span 7;
}

/* ===== 趋势 ===== */
.trends {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.trends__head {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
}
.trends__title {
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}
.trends__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}
.trends__grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: var(--space-3);
}
.trend-card {
  display: flex;
  flex-direction: column;
  padding: var(--space-4);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.trend-card:hover {
  border-color: var(--border-strong);
}
.trend--cpu {
  grid-column: span 6;
}
.trend--mem {
  grid-column: span 3;
}
.trend--disk {
  grid-column: span 3;
}
.trend--net {
  grid-column: span 12;
}

/* ===== 运维卡片 ===== */
.card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.card__title {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}
.card__title-icon {
  flex: none;
  color: var(--accent);
}
.ops__hint {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: var(--lh-relaxed);
}
.ops__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

/* ===== 断连遮罩（实色暖底，禁 blur） ===== */
.reconnect-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-4);
  background: color-mix(in oklch, var(--bg-base) 82%, transparent);
  z-index: 100;
}
.reconnect-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  max-width: 28rem;
  padding: var(--space-6);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  text-align: center;
}
.reconnect-card__icon-wrap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--space-8);
  height: var(--space-8);
  border-radius: var(--radius-md);
  background: color-mix(in oklch, var(--warning) 14%, transparent);
}
.reconnect-card__icon {
  color: var(--warning);
}
.reconnect-card__title {
  font-size: var(--text-xl);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}
.reconnect-card__msg {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: var(--lh-relaxed);
}

/* ===== 移动端单列 ===== */
@media (max-width: 768px) {
  .kpi-grid,
  .trends__grid {
    grid-template-columns: 1fr;
  }
  .kpi--cpu,
  .kpi--mem,
  .kpi--disk,
  .kpi--net,
  .trend--cpu,
  .trend--mem,
  .trend--disk,
  .trend--net {
    grid-column: span 1;
  }
}

/* ===== 降低动效：禁卡片过渡，Sparkline 内部已自禁 ===== */
@media (prefers-reduced-motion: reduce) {
  .trend-card {
    transition: none;
  }
}
</style>
