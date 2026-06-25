<script setup lang="ts">
/* ModerationView — 管理设置：警告阈值、拉黑时长、图片负反馈封禁阶梯。
 * 接 GET/PUT /api/config/moderation。useConfigForm 统一 load/save/validate/dirty/拦截。
 * ban_ladder 为 number[]，用动态行编辑（每行一个分钟数 + 增删按钮），
 * 比 JsonEditor 更直观且支持逐行错误定位。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { AlertTriangle, Plus, RotateCw, Save, ShieldAlert, Trash2 } from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import { useConfigForm } from '@/composables/useConfigForm';
import { getModerationConfig, saveModerationConfig } from '@/api/domains/moderation';
import type { ModerationConfig } from '@/api/models';

// 顶栏手动刷新注入
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');

// ===== 字段级校验（范围取自后端 PUT 校验 api.py 行 4895-4972） =====
function validate(f: ModerationConfig): Record<string, string> | null {
  const e: Record<string, string> = {};
  const num = (v: unknown): v is number => typeof v === 'number' && !Number.isNaN(v);

  if (num(f.warning_threshold) && (f.warning_threshold < 1 || f.warning_threshold > 100))
    e.warning_threshold = '警告阈值需在 1–100 之间';
  if (num(f.ban_duration_min) && (f.ban_duration_min < 1 || f.ban_duration_min > 1440))
    e.ban_duration_min = '拉黑时长最小值需在 1–1440 分钟之间';
  if (num(f.ban_duration_max) && (f.ban_duration_max < 1 || f.ban_duration_max > 1440))
    e.ban_duration_max = '拉黑时长最大值需在 1–1440 分钟之间';
  if (
    num(f.ban_duration_min) && num(f.ban_duration_max) && f.ban_duration_min > f.ban_duration_max
  ) {
    e.ban_duration_min = '拉黑时长最小值不能大于最大值';
  }
  if (num(f.image_feedback_ban_trigger_count) && (f.image_feedback_ban_trigger_count < 1 || f.image_feedback_ban_trigger_count > 20))
    e.image_feedback_ban_trigger_count = '触发数量需在 1–20 之间';
  if (num(f.image_feedback_repeat_window_minutes) && (f.image_feedback_repeat_window_minutes < 1 || f.image_feedback_repeat_window_minutes > 10080))
    e.image_feedback_repeat_window_minutes = '升档窗口需在 1–10080 分钟之间';

  const ladder = f.image_feedback_ban_ladder_minutes;
  if (!Array.isArray(ladder) || ladder.length === 0) {
    e.image_feedback_ban_ladder_minutes = '封禁阶梯不能为空';
  } else {
    for (let i = 0; i < ladder.length; i++) {
      const v = ladder[i];
      if (!Number.isInteger(v) || v <= 0) {
        e.image_feedback_ban_ladder_minutes = `第 ${i + 1} 阶需为正整数`;
        break;
      }
      if (v > 43200) {
        e.image_feedback_ban_ladder_minutes = `第 ${i + 1} 阶不能超过 43200 分钟`;
        break;
      }
    }
  }
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
} = useConfigForm<ModerationConfig>({
  load: getModerationConfig,
  // save 包装器：PUT 仅回 {success, updated}，需重新 GET 刷新表单与 dirty
  save: async (body) => {
    await saveModerationConfig(body);
    return await getModerationConfig();
  },
  validate,
  successMessage: '管理配置已保存',
});

// ===== 派生状态 =====
const hasData = computed(() => Object.keys(form.value || {}).length > 0);
const showSkeleton = computed(() => loading.value && !hasData.value);
const showEmpty = computed(() => !loading.value && !error.value && !hasData.value);
const showLoadError = computed(() => !loading.value && !!error.value && !hasData.value);
const showForm = computed(() => hasData.value);

const banLadder = computed<number[]>(() => {
  const v = form.value.image_feedback_ban_ladder_minutes;
  return Array.isArray(v) ? v : [];
});

// ===== 字段写入助手 =====
function setNum(key: keyof ModerationConfig, v: string): void {
  if (v === '') {
    patch(key, undefined as unknown as ModerationConfig[keyof ModerationConfig]);
    return;
  }
  const n = Number(v);
  if (!Number.isNaN(n)) patch(key, n as unknown as ModerationConfig[keyof ModerationConfig]);
}

// ===== 封禁阶梯动态行编辑 =====
function setLadderItem(index: number, v: string): void {
  const arr = [...banLadder.value];
  if (v === '') {
    arr[index] = 0;
  } else {
    const n = Number(v);
    arr[index] = Number.isNaN(n) ? 0 : n;
  }
  patch('image_feedback_ban_ladder_minutes', arr);
}
function addLadderItem(): void {
  const arr = [...banLadder.value, 60];
  patch('image_feedback_ban_ladder_minutes', arr);
}
function removeLadderItem(index: number): void {
  const arr = [...banLadder.value];
  arr.splice(index, 1);
  patch('image_feedback_ban_ladder_minutes', arr);
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
      :icon="ShieldAlert"
      title="管理设置"
      subtitle="警告阈值、拉黑时长与图片负反馈封禁阶梯"
    />

    <!-- 保存时错误横幅 -->
    <div v-if="error && showForm" class="error-banner" role="alert">
      <div class="error-banner__text">
        <AlertTriangle :size="18" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>
      <BaseButton variant="ghost" size="sm" :icon="RotateCw" @click="retry">重试</BaseButton>
    </div>

    <!-- 骨架屏 -->
    <div v-if="showSkeleton" class="mod-skeleton" aria-busy="true" aria-live="polite">
      <BaseSkeleton height="1.25rem" width="8rem" />
      <div class="mod-skeleton__card">
        <BaseSkeleton height="1.5rem" width="40%" />
        <div class="mod-skeleton__grid">
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="ShieldAlert"
      title="暂无管理配置"
      description="尚未读取到任何管理配置数据，请尝试重新加载。"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 加载错误 -->
    <BaseEmpty
      v-else-if="showLoadError"
      :icon="AlertTriangle"
      title="配置加载失败"
      :description="error ?? '请检查后端服务后重试。'"
      action-text="重试"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 表单 -->
    <form v-else-if="showForm" class="mod-form" novalidate @submit.prevent="onSubmit">
      <!-- 警告与拉黑 -->
      <section class="card">
        <h3 class="card__title font-display">
          <ShieldAlert class="card__title-icon" :size="16" aria-hidden="true" />
          警告与拉黑
        </h3>
        <div class="card__grid">
          <BaseInput
            :model-value="form.warning_threshold ?? ''"
            type="number"
            label="警告次数阈值"
            hint="1–100 次"
            :error="fieldErrors.warning_threshold"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('warning_threshold', v)"
          />
          <BaseInput
            :model-value="form.ban_duration_min ?? ''"
            type="number"
            label="拉黑时长最小值（分钟）"
            hint="1–1440，且 ≤ 最大值"
            :error="fieldErrors.ban_duration_min"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('ban_duration_min', v)"
          />
          <BaseInput
            :model-value="form.ban_duration_max ?? ''"
            type="number"
            label="拉黑时长最大值（分钟）"
            hint="1–1440，且 ≥ 最小值"
            :error="fieldErrors.ban_duration_max"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('ban_duration_max', v)"
          />
        </div>
      </section>

      <!-- 图片负反馈封禁 -->
      <section class="card">
        <h3 class="card__title font-display">
          <AlertTriangle class="card__title-icon" :size="16" aria-hidden="true" />
          图片负反馈封禁
        </h3>
        <div class="toggle-row">
          <ToggleSwitch
            :model-value="!!form.image_feedback_enabled"
            label="启用图片负反馈封禁"
            :disabled="saving || loading"
            @update:model-value="(v) => patch('image_feedback_enabled', v)"
          />
          <p class="toggle-row__hint">开启后累计负反馈达阈值将按阶梯封禁。</p>
        </div>
        <div class="card__grid">
          <BaseInput
            :model-value="form.image_feedback_ban_trigger_count ?? ''"
            type="number"
            label="触发封禁所需反馈数"
            hint="1–20 次"
            :error="fieldErrors.image_feedback_ban_trigger_count"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('image_feedback_ban_trigger_count', v)"
          />
          <BaseInput
            :model-value="form.image_feedback_repeat_window_minutes ?? ''"
            type="number"
            label="升档时间窗口（分钟）"
            hint="1–10080 分钟"
            :error="fieldErrors.image_feedback_repeat_window_minutes"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('image_feedback_repeat_window_minutes', v)"
          />
        </div>

        <!-- 封禁阶梯动态行编辑 -->
        <div class="ladder">
          <div class="ladder__head">
            <span class="ladder__label font-display">封禁阶梯（分钟）</span>
            <BaseButton
              variant="secondary"
              size="sm"
              :icon="Plus"
              :disabled="saving || loading"
              @click="addLadderItem"
            >
              添加阶梯
            </BaseButton>
          </div>
          <p class="ladder__hint">每阶需为正整数且 ≤ 43200，至少一阶；按顺序对应第 1、2… 次封禁时长。</p>
          <div v-if="banLadder.length === 0" class="ladder__empty">
            暂无阶梯，点击「添加阶梯」新建。
          </div>
          <div v-for="(item, idx) in banLadder" :key="idx" class="ladder__row">
            <span class="ladder__index">{{ idx + 1 }}</span>
            <BaseInput
              :model-value="item"
              type="number"
              :placeholder="'第 ' + (idx + 1) + ' 阶分钟数'"
              :disabled="saving || loading"
              @update:model-value="(v) => setLadderItem(idx, v)"
            />
            <BaseButton
              variant="ghost"
              size="sm"
              :icon="Trash2"
              :disabled="saving || loading"
              aria-label="删除该阶梯"
              @click="removeLadderItem(idx)"
            />
          </div>
          <p v-if="fieldErrors.image_feedback_ban_ladder_minutes" class="ladder__error" role="alert">
            {{ fieldErrors.image_feedback_ban_ladder_minutes }}
          </p>
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
          @click="reset"
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

    <!-- 离开确认 -->
    <BaseConfirmDialog
      v-model="leaveConfirm"
      title="放弃未保存的更改？"
      message="当前管理配置有未保存的更改，离开将丢弃这些更改。"
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

/* ===== 骨架屏 ===== */
.mod-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.mod-skeleton__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.mod-skeleton__grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3);
}

/* ===== 表单卡片 ===== */
.mod-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
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

/* ===== 封禁阶梯动态行 ===== */
.ladder {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.ladder__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.ladder__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.ladder__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}
.ladder__empty {
  font-size: var(--text-sm);
  color: var(--text-muted);
  padding: var(--space-2) 0;
}
.ladder__row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.ladder__index {
  flex: none;
  width: 1.5rem;
  text-align: center;
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
}
.ladder__row :deep(.field) {
  flex: 1 1 auto;
}
.ladder__error {
  font-size: var(--text-xs);
  color: var(--danger);
}

/* ===== 操作栏（sticky 底部，实心底色，禁 blur） ===== */
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

/* ===== 移动端单列 ===== */
@media (max-width: 768px) {
  .card__grid,
  .mod-skeleton__grid {
    grid-template-columns: 1fr;
  }
  .actions {
    position: static;
  }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .card {
    transition: none;
  }
}
</style>
