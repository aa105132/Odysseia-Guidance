<script setup lang="ts">
/* SummaryView — 年度总结配置：开关、年份、生成上限、Tier2 阈值 + 只读统计 + 清日志。
 * 接 GET/PUT /api/config/summary + DELETE /api/config/summary/logs。
 * useConfigForm 统一 load/save/validate/dirty/beforeunload。
 * PUT 回 {success, updated}（非完整配置），save 包装器 await 后重新 GET 刷新（含 stats）。
 * GET 响应含只读 stats: {total_generated, unique_users}（按 year 查 yearly_summary_log）。
 * 清日志：两个按钮（清当前年 / 清所有），BaseConfirmDialog 前置确认 → clearSummaryLogs() →
 *   spinner + toast 成功/失败 → loadForm(true) 刷新 stats。
 * 8 状态 + dirty 路由离开拦截（onBeforeRouteLeave + BaseConfirmDialog）+ reduced-motion。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { AlertTriangle, CalendarCheck, RotateCw, Save, Trash2 } from 'lucide-vue-next';
import { useConfigForm } from '@/composables/useConfigForm';
import { useToastStore } from '@/stores/toast';
import { ApiError } from '@/api/client';
import {
  getSummaryConfig,
  saveSummaryConfig,
  clearSummaryLogs,
} from '@/api/domains/summary';
import type { SummaryConfig } from '@/api/models';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';

const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');
const toast = useToastStore();

// ===== 字段级校验（前端早筛，范围取自 api.py L6408-6448 + recon） =====
function validate(f: SummaryConfig): Record<string, string> | null {
  const e: Record<string, string> = {};
  const num = (v: unknown): v is number => typeof v === 'number' && !Number.isNaN(v);

  if (num(f.year) && (f.year < 2020 || f.year > 2099)) e.year = '年份需在 2020–2099 之间';
  if (num(f.generation_limit) && (f.generation_limit < 1 || f.generation_limit > 100))
    e.generation_limit = '生成上限需在 1–100 之间';
  if (num(f.tier2_threshold) && (f.tier2_threshold < 0 || f.tier2_threshold > 1000))
    e.tier2_threshold = 'Tier2 阈值需在 0–1000 之间';

  return Object.keys(e).length ? e : null;
}

const {
  form,
  loading,
  saving,
  error,
  fieldErrors,
  dirty,
  loadForm,
  submit,
  reset,
  patch,
} = useConfigForm<SummaryConfig>({
  load: () => getSummaryConfig(),
  // save 包装器：PUT 仅回 {success, updated}，需重新 GET 刷新 form/original + stats
  save: async (body) => {
    await saveSummaryConfig(body);
    return await getSummaryConfig();
  },
  validate,
  successMessage: '年度总结配置已保存',
});

// ===== 派生状态 =====
const hasData = computed(() => Object.keys(form.value || {}).length > 0);
const showSkeleton = computed(() => loading.value && !hasData.value);
const showEmpty = computed(() => !loading.value && !error.value && !hasData.value);
const showLoadError = computed(() => !loading.value && !!error.value && !hasData.value);
const showForm = computed(() => hasData.value);

// 只读统计
const statsTotal = computed(() => form.value.stats?.total_generated ?? 0);
const statsUniqueUsers = computed(() => form.value.stats?.unique_users ?? 0);
const hasStats = computed(() => !!form.value.stats);

// ===== 字段写入助手 =====
function setNum(key: keyof SummaryConfig, v: string): void {
  if (v === '') {
    patch(key, undefined as unknown as SummaryConfig[keyof SummaryConfig]);
    return;
  }
  const n = Number(v);
  if (!Number.isNaN(n)) patch(key, n as unknown as SummaryConfig[keyof SummaryConfig]);
}

// ===== 清日志 =====
const clearLogsConfirm = ref(false);
/** 待清除的年份：number=清指定年，null=清所有 */
const clearLogsTarget = ref<number | null>(null);
const clearingLogs = ref(false);

function askClearYear(): void {
  clearLogsTarget.value = form.value.year ?? new Date().getFullYear();
  clearLogsConfirm.value = true;
}
function askClearAll(): void {
  clearLogsTarget.value = null;
  clearLogsConfirm.value = true;
}
const clearLogsMessage = computed(() => {
  if (clearLogsTarget.value === null) return '将清除所有年份的年度总结日志。此操作不可撤销。';
  return `将清除 ${clearLogsTarget.value} 年的年度总结日志。此操作不可撤销。`;
});
const clearLogsTitle = computed(() =>
  clearLogsTarget.value === null ? '清除所有日志' : `清除 ${clearLogsTarget.value} 年日志`,
);

async function confirmClearLogs(): Promise<void> {
  clearingLogs.value = true;
  try {
    const res = await clearSummaryLogs(
      clearLogsTarget.value === null ? undefined : clearLogsTarget.value,
    );
    toast.push({ type: 'success', message: res.message || '日志已清除' });
    // 重新加载刷新 stats
    await loadForm(true);
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : e instanceof Error ? e.message : '清除日志失败';
    toast.push({ type: 'error', message: `清除日志失败：${msg}` });
  } finally {
    clearingLogs.value = false;
    clearLogsConfirm.value = false;
  }
}
function cancelClearLogs(): void {
  clearLogsConfirm.value = false;
  clearLogsTarget.value = null;
}

// ===== 操作 =====
function retry(): void {
  loadForm(true).catch(() => {
    /* 错误已由 useConfigForm 内部 toast 并置 error */
  });
}
function onSubmit(): void {
  void submit();
}
function onReset(): void {
  reset();
}

// ===== 路由离开拦截：dirty 时弹确认框 =====
const leaveConfirm = ref(false);
let leaveNext: ((ok?: boolean) => void) | null = null;
onBeforeRouteLeave((_to, _from, next) => {
  if (!dirty.value) {
    next();
    return;
  }
  leaveNext = next;
  leaveConfirm.value = true;
});
function confirmLeave(): void {
  leaveConfirm.value = false;
  leaveNext?.(true);
  leaveNext = null;
}
function cancelLeave(): void {
  leaveConfirm.value = false;
  leaveNext?.(false);
  leaveNext = null;
}

onMounted(() => {
  registerRefresh?.(() => loadForm(true));
});
</script>

<template>
  <div class="view">
    <BaseSectionTitle
      :icon="CalendarCheck"
      title="年度总结"
      subtitle="年度总结开关 · 生成上限 · Tier2 阈值 · 日志清理"
    />

    <!-- 保存时错误横幅（表单已存在） -->
    <div v-if="error && showForm" class="error-banner" role="alert">
      <div class="error-banner__text">
        <AlertTriangle :size="18" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>
      <BaseButton variant="ghost" size="sm" :icon="RotateCw" @click="retry">重试</BaseButton>
    </div>

    <!-- 骨架屏 -->
    <div v-if="showSkeleton" class="form-skeleton" aria-busy="true" aria-live="polite">
      <BaseSkeleton height="1.25rem" width="8rem" />
      <div class="form-skeleton__card">
        <BaseSkeleton height="1.5rem" width="40%" />
        <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
      </div>
      <div class="form-skeleton__card">
        <BaseSkeleton height="1.5rem" width="40%" />
        <div class="form-skeleton__grid">
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="CalendarCheck"
      title="暂无年度总结配置"
      description="尚未读取到任何年度总结配置数据，请尝试重新加载。"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 加载错误 -->
    <BaseEmpty
      v-else-if="showLoadError"
      :icon="AlertTriangle"
      title="加载失败"
      :description="error ?? '无法读取年度总结配置。'"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 表单 -->
    <form v-else-if="showForm" class="form" novalidate @submit.prevent="onSubmit">
      <!-- 统计概览（只读） -->
      <section v-if="hasStats" class="card card--stats">
        <h3 class="card__title font-display">本年统计</h3>
        <div class="stats">
          <div class="stats__item">
            <span class="stats__value font-display">{{ statsTotal }}</span>
            <span class="stats__label">已生成总结</span>
          </div>
          <div class="stats__divider" aria-hidden="true" />
          <div class="stats__item">
            <span class="stats__value font-display">{{ statsUniqueUsers }}</span>
            <span class="stats__label">独立用户</span>
          </div>
        </div>
        <p class="card__hint">统计基于当前年份（{{ form.year ?? '—' }}）的 yearly_summary_log。</p>
      </section>

      <!-- 配置字段 -->
      <section class="card">
        <h3 class="card__title font-display">总结配置</h3>
        <div class="toggle-row">
          <ToggleSwitch
            :model-value="!!form.enabled"
            label="启用年度总结"
            :disabled="saving"
            @update:model-value="(v) => patch('enabled', v as SummaryConfig['enabled'])"
          />
          <p class="toggle-row__hint">开启后成员可生成本年度总结卡片。</p>
        </div>
        <div class="card__grid">
          <BaseInput
            :model-value="form.year ?? ''"
            type="number"
            label="年份"
            hint="2020–2099"
            placeholder="2025"
            :error="fieldErrors.year"
            :disabled="saving"
            @update:model-value="(v) => setNum('year', v)"
          />
          <BaseInput
            :model-value="form.generation_limit ?? ''"
            type="number"
            label="生成上限"
            hint="每位成员每年 1–100 次"
            placeholder="3"
            :error="fieldErrors.generation_limit"
            :disabled="saving"
            @update:model-value="(v) => setNum('generation_limit', v)"
          />
          <BaseInput
            :model-value="form.tier2_threshold ?? ''"
            type="number"
            label="Tier2 阈值"
            hint="达到此消息数解锁 Tier2 0–1000"
            placeholder="500"
            :error="fieldErrors.tier2_threshold"
            :disabled="saving"
            @update:model-value="(v) => setNum('tier2_threshold', v)"
          />
        </div>
      </section>

      <!-- 日志清理 -->
      <section class="card card--danger">
        <h3 class="card__title font-display">
          <Trash2 class="card__title-icon" :size="16" aria-hidden="true" />
          日志清理
        </h3>
        <p class="card__hint">清除年度总结生成记录，不可撤销。清除后统计将归零。</p>
        <div class="card__actions">
          <BaseButton
            variant="secondary"
            size="md"
            :icon="Trash2"
            :disabled="clearingLogs || saving"
            @click="askClearYear"
          >
            清除 {{ form.year ?? '本年' }} 年日志
          </BaseButton>
          <BaseButton
            variant="danger"
            size="md"
            :icon="Trash2"
            :disabled="clearingLogs || saving"
            @click="askClearAll"
          >
            清除所有日志
          </BaseButton>
        </div>
      </section>

      <!-- 操作栏 -->
      <div class="actions">
        <span v-if="dirty" class="actions__dirty">
          <AlertTriangle :size="14" aria-hidden="true" />
          有未保存更改
        </span>
        <span v-else-if="!saving" class="actions__saved">所有更改已保存</span>
        <BaseButton
          variant="ghost"
          size="md"
          :disabled="!dirty || saving || loading"
          @click="onReset"
        >
          放弃更改
        </BaseButton>
        <BaseButton
          type="submit"
          variant="primary"
          size="md"
          :loading="saving"
          :disabled="!dirty || saving || loading"
          :icon="Save"
        >
          保存
        </BaseButton>
      </div>
    </form>

    <!-- 清日志确认 -->
    <BaseConfirmDialog
      v-model="clearLogsConfirm"
      :title="clearLogsTitle"
      :message="clearLogsMessage"
      confirm-text="清除"
      variant="danger"
      @confirm="confirmClearLogs"
      @cancel="cancelClearLogs"
    />

    <!-- 路由离开确认 -->
    <BaseConfirmDialog
      v-model="leaveConfirm"
      title="离开将丢弃未保存的修改"
      message="当前年度总结配置有未保存的更改，确定离开吗？"
      confirm-text="离开"
      variant="danger"
      @confirm="confirmLeave"
      @cancel="cancelLeave"
    />
  </div>
</template>

<style scoped>
.view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
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

/* ===== 骨架 ===== */
.form-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.form-skeleton__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.form-skeleton__grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-3);
}

/* ===== 表单卡片 ===== */
.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.card:hover {
  border-color: var(--border-strong);
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
.card__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4) var(--space-5);
}
.card__hint {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--text-muted);
}

/* 统计卡 */
.card--stats .stats {
  display: flex;
  align-items: center;
  gap: var(--space-6);
}
.stats__item {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.stats__value {
  font-size: var(--text-2xl);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}
.stats__label {
  font-size: var(--text-xs);
  color: var(--text-muted);
}
.stats__divider {
  width: 1px;
  height: 2rem;
  background: var(--border);
}

/* 危险卡 */
.card--danger .card__title-icon {
  color: var(--danger);
}
.card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

/* 开关行 */
.toggle-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.toggle-row__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

/* ===== 操作栏 ===== */
.actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  position: sticky;
  bottom: var(--space-2);
  z-index: 1;
}
.actions__dirty {
  margin-right: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-sm);
  color: var(--warning);
}
.actions__saved {
  margin-right: auto;
  font-size: var(--text-sm);
  color: var(--text-muted);
}

/* ===== 移动端 ===== */
@media (max-width: 768px) {
  .form-skeleton__grid,
  .card__grid {
    grid-template-columns: 1fr;
  }
  .actions {
    position: static;
    flex-direction: column-reverse;
    align-items: stretch;
  }
  .card--stats .stats {
    gap: var(--space-4);
  }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .card {
    transition: none;
  }
}
</style>
