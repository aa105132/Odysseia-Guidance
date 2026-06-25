<script setup lang="ts">
/* SpringFestivalView — 新春活动配置：活动开关、每日上限、红包金额范围、DM 文案。
 * 接 GET/PUT /api/config/spring-festival。useConfigForm 统一 load/save/validate/dirty/beforeunload。
 * PUT 回 {success, updated}（updated 为部分字典，非完整配置），save 包装器 await 后重新 GET 刷新。
 * min/max_reward 后端联合校验 max≥min；文案字段需非空；PUT 同时写 .env。
 * 8 状态 + dirty 路由离开拦截（onBeforeRouteLeave + BaseConfirmDialog）+ reduced-motion。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { AlertTriangle, Gift, RotateCw, Save } from 'lucide-vue-next';
import { useConfigForm } from '@/composables/useConfigForm';
import {
  getSpringFestivalConfig,
  saveSpringFestivalConfig,
} from '@/api/domains/springFestival';
import type { SpringFestivalConfig } from '@/api/models';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';

// 顶栏手动刷新注入
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');

// ===== 字段级校验（前端早筛，范围取自 api.py L6257-6360 + recon） =====
function validate(f: SpringFestivalConfig): Record<string, string> | null {
  const e: Record<string, string> = {};
  const num = (v: unknown): v is number => typeof v === 'number' && !Number.isNaN(v);

  if (num(f.min_reward) && f.min_reward <= 0) e.min_reward = '最小奖励需大于 0';
  if (num(f.max_reward)) {
    if (f.max_reward <= 0) e.max_reward = '最大奖励需大于 0';
    else if (num(f.min_reward) && f.max_reward < f.min_reward)
      e.max_reward = '最大奖励需 ≥ 最小奖励';
  }

  // 文案字段非空（后端校验 str 非空）
  const textFields: Array<keyof SpringFestivalConfig> = [
    'dm_title',
    'dm_description',
    'button_label',
    'claimed_label',
    'reward_reason',
  ];
  for (const k of textFields) {
    const v = (f as Record<string, unknown>)[k];
    if (typeof v === 'string' && v.trim() === '') {
      e[k as string] = '此字段不能为空';
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
} = useConfigForm<SpringFestivalConfig>({
  load: () => getSpringFestivalConfig(),
  // save 包装器：PUT 仅回 {success, updated}，需重新 GET 刷新 form/original
  save: async (body) => {
    await saveSpringFestivalConfig(body);
    return await getSpringFestivalConfig();
  },
  validate,
  successMessage: '新春活动配置已保存',
});

// ===== 派生状态 =====
const hasData = computed(() => Object.keys(form.value || {}).length > 0);
const showSkeleton = computed(() => loading.value && !hasData.value);
const showEmpty = computed(() => !loading.value && !error.value && !hasData.value);
const showLoadError = computed(() => !loading.value && !!error.value && !hasData.value);
const showForm = computed(() => hasData.value);

// ===== 字段写入助手 =====
function setStr(key: keyof SpringFestivalConfig, v: string): void {
  patch(key, v as unknown as SpringFestivalConfig[keyof SpringFestivalConfig]);
}
function setNum(key: keyof SpringFestivalConfig, v: string): void {
  if (v === '') {
    patch(key, undefined as unknown as SpringFestivalConfig[keyof SpringFestivalConfig]);
    return;
  }
  const n = Number(v);
  if (!Number.isNaN(n)) patch(key, n as unknown as SpringFestivalConfig[keyof SpringFestivalConfig]);
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
      :icon="Gift"
      title="新春活动"
      subtitle="活动开关 · 每日上限 · 红包金额范围 · DM 文案"
    />

    <!-- 保存时错误横幅（表单已存在） -->
    <div v-if="error && showForm" class="error-banner" role="alert">
      <div class="error-banner__text">
        <AlertTriangle :size="18" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>
      <BaseButton variant="ghost" size="sm" :icon="RotateCw" @click="retry">重试</BaseButton>
    </div>

    <!-- 骨架屏：初始加载 -->
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
      :icon="Gift"
      title="暂无新春活动配置"
      description="尚未读取到任何新春活动配置数据，请尝试重新加载。"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 加载错误 -->
    <BaseEmpty
      v-else-if="showLoadError"
      :icon="AlertTriangle"
      title="加载失败"
      :description="error ?? '无法读取新春活动配置。'"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 表单 -->
    <form v-else-if="showForm" class="form" novalidate @submit.prevent="onSubmit">
      <!-- 活动开关 -->
      <section class="card">
        <h3 class="card__title font-display">活动开关</h3>
        <div class="toggle-row">
          <ToggleSwitch
            :model-value="!!form.enabled"
            label="启用新春红包活动"
            :disabled="saving"
            @update:model-value="(v) => patch('enabled', v as SpringFestivalConfig['enabled'])"
          />
          <p class="toggle-row__hint">开启后符合条件的成员将收到新春红包 DM。</p>
        </div>
        <div class="toggle-row">
          <ToggleSwitch
            :model-value="!!form.daily_limit_enabled"
            label="启用每日领取上限"
            :disabled="saving"
            @update:model-value="(v) => patch('daily_limit_enabled', v as SpringFestivalConfig['daily_limit_enabled'])"
          />
          <p class="toggle-row__hint">开启后每位成员每日仅可领取一次红包。</p>
        </div>
      </section>

      <!-- 奖励范围 -->
      <section class="card">
        <h3 class="card__title font-display">奖励金额范围</h3>
        <div class="card__grid">
          <BaseInput
            :model-value="form.min_reward ?? ''"
            type="number"
            label="最小奖励（灵石）"
            placeholder="500"
            :error="fieldErrors.min_reward"
            :disabled="saving"
            @update:model-value="(v) => setNum('min_reward', v)"
          />
          <BaseInput
            :model-value="form.max_reward ?? ''"
            type="number"
            label="最大奖励（灵石）"
            placeholder="1000"
            :error="fieldErrors.max_reward"
            :disabled="saving"
            @update:model-value="(v) => setNum('max_reward', v)"
          />
        </div>
        <p class="card__hint">最小与最大奖励均需大于 0，且最大 ≥ 最小。实际发放金额在此范围内随机。</p>
      </section>

      <!-- DM 文案 -->
      <section class="card">
        <h3 class="card__title font-display">DM 文案</h3>
        <div class="card__grid">
          <BaseInput
            :model-value="form.dm_title ?? ''"
            label="DM 标题"
            placeholder="新春红包"
            :error="fieldErrors.dm_title"
            :disabled="saving"
            @update:model-value="(v) => setStr('dm_title', v)"
          />
          <BaseInput
            :model-value="form.button_label ?? ''"
            label="开启按钮文案"
            placeholder="开启红包"
            :error="fieldErrors.button_label"
            :disabled="saving"
            @update:model-value="(v) => setStr('button_label', v)"
          />
          <BaseInput
            :model-value="form.claimed_label ?? ''"
            label="已领取文案"
            placeholder="已领取"
            :error="fieldErrors.claimed_label"
            :disabled="saving"
            @update:model-value="(v) => setStr('claimed_label', v)"
          />
          <BaseInput
            :model-value="form.reward_reason ?? ''"
            label="奖励事由"
            placeholder="新春红包奖励"
            :error="fieldErrors.reward_reason"
            :disabled="saving"
            @update:model-value="(v) => setStr('reward_reason', v)"
          />
        </div>
        <div class="field">
          <label class="field__label font-display">DM 描述</label>
          <textarea
            :value="form.dm_description ?? ''"
            class="field__textarea"
            rows="3"
            aria-label="DM 描述"
            placeholder="你收到了一份新春祝福…"
            :disabled="saving"
            :aria-invalid="!!fieldErrors.dm_description"
            @input="(e) => setStr('dm_description', (e.target as HTMLTextAreaElement).value)"
          />
          <p v-if="fieldErrors.dm_description" class="field__error" role="alert">{{ fieldErrors.dm_description }}</p>
          <p v-else class="field__hint">红包 DM 正文，展示给领取成员。</p>
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

    <!-- 路由离开确认 -->
    <BaseConfirmDialog
      v-model="leaveConfirm"
      title="离开将丢弃未保存的修改"
      message="当前新春活动配置有未保存的更改，确定离开吗？"
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
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
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

/* ===== 文本域（对齐 BaseInput 视觉） ===== */
.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.field__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.field__textarea {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: var(--text-base);
  line-height: var(--lh-normal);
  resize: vertical;
  outline: none;
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.field__textarea:hover {
  border-color: var(--border-strong);
}
.field__textarea:focus-visible {
  border-color: var(--accent);
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.field__textarea:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.field__textarea::placeholder {
  color: var(--text-placeholder);
}
.field__error {
  font-size: var(--text-xs);
  color: var(--danger);
}
.field__hint {
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
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .card,
  .field__textarea {
    transition: none;
  }
}
</style>
