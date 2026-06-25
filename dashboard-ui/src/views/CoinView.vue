<script setup lang="ts">
/* CoinView — 货币设置：灵石奖励、贷款、投喂/告白图片、抽鬼牌图片、总结配图。
 * 接 GET（走 /api/config/all 的 coin 子集，绕开 /api/config/coin 的 NameError）
 * + PUT /api/config/coin。useConfigForm 统一 load/save/validate/dirty/拦截。
 * 9 个 ghost card URL 为动态键，按 GHOST_CARD_URL_KEYS 渲染独立 BaseInput，
 * 每行一个 URL，dirty 与字段级错误自然生效（优于 JsonEditor 的中间态问题）。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { AlertTriangle, Coins, Image as ImageIcon, RotateCw, Save, SlidersHorizontal, Sparkles } from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseSelect from '@/components/ui/BaseSelect.vue';
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import { useConfigForm } from '@/composables/useConfigForm';
import {
  getCoinConfig,
  saveCoinConfig,
  GHOST_CARD_URL_KEYS,
  GHOST_CARD_LABELS,
} from '@/api/domains/coin';
import type { CoinConfig } from '@/api/models';

// 顶栏手动刷新注入：注册当前视图的 force 刷新
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');

// ===== 字段级校验（前端早筛，范围取自后端 PUT 校验 api.py 行 4520-4624） =====
const URL_FIELDS = ['feeding_response_image_url', 'confession_response_image_url', 'loan_thumbnail_url'];
function isUrlOrEmpty(v: unknown): boolean {
  return typeof v === 'string' && (v === '' || v.startsWith('http://') || v.startsWith('https://'));
}
function validate(f: CoinConfig): Record<string, string> | null {
  const e: Record<string, string> = {};
  const num = (v: unknown): v is number => typeof v === 'number' && !Number.isNaN(v);
  const intPos = (v: unknown): boolean => num(v) && Number.isInteger(v) && v >= 0;

  if (num(f.daily_reward) && !intPos(f.daily_reward)) e.daily_reward = '每日奖励需为非负整数';
  if (num(f.chat_reward) && !intPos(f.chat_reward)) e.chat_reward = '聊天奖励需为非负整数';
  if (num(f.max_loan) && !intPos(f.max_loan)) e.max_loan = '最大贷款额需为非负整数';
  if (num(f.feeding_cooldown_seconds) && !intPos(f.feeding_cooldown_seconds))
    e.feeding_cooldown_seconds = '投喂冷却需为非负整数';
  if (num(f.feeding_daily_limit) && !intPos(f.feeding_daily_limit))
    e.feeding_daily_limit = '投喂每日上限需为非负整数';

  if (f.summary_imagen_resolution && !['default', '2k', '4k'].includes(f.summary_imagen_resolution))
    e.summary_imagen_resolution = '分辨率仅支持 default / 2k / 4k';

  for (const k of URL_FIELDS) {
    if (!isUrlOrEmpty((f as Record<string, unknown>)[k]))
      e[k] = 'URL 需以 http:// 或 https:// 开头';
  }
  for (const k of GHOST_CARD_URL_KEYS) {
    if (!isUrlOrEmpty((f as Record<string, unknown>)[k]))
      e[k] = 'URL 需以 http:// 或 https:// 开头';
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
} = useConfigForm<CoinConfig>({
  load: getCoinConfig,
  // save 包装器：PUT 仅回 {success, updated}，需重新 GET 刷新表单与 dirty
  save: async (body) => {
    await saveCoinConfig(body);
    return await getCoinConfig();
  },
  validate,
  successMessage: '货币配置已保存',
});

// ===== 派生状态 =====
const hasData = computed(() => Object.keys(form.value || {}).length > 0);
const showSkeleton = computed(() => loading.value && !hasData.value);
const showEmpty = computed(() => !loading.value && !error.value && !hasData.value);
const showLoadError = computed(() => !loading.value && !!error.value && !hasData.value);
const showForm = computed(() => hasData.value);

const currencyName = computed(() => (form.value.currency_name as string) || '灵石');

const resolutionOptions = [
  { value: 'default', label: '默认' },
  { value: '2k', label: '2K' },
  { value: '4k', label: '4K' },
];

// ===== 字段写入助手 =====
function setStr(key: keyof CoinConfig, v: string): void {
  patch(key, v as unknown as CoinConfig[keyof CoinConfig]);
}
function setNum(key: keyof CoinConfig, v: string): void {
  if (v === '') {
    patch(key, undefined as unknown as CoinConfig[keyof CoinConfig]);
    return;
  }
  const n = Number(v);
  if (!Number.isNaN(n)) patch(key, n as unknown as CoinConfig[keyof CoinConfig]);
}
function setGhostUrl(key: string, v: string): void {
  patch(key as unknown as keyof CoinConfig, v as unknown as CoinConfig[keyof CoinConfig]);
}
function ghostUrl(key: string): string {
  return String((form.value as Record<string, unknown>)[key] ?? '');
}
function ghostError(key: string): string | undefined {
  return (fieldErrors.value as Record<string, string>)[key];
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
      :icon="Coins"
      title="货币设置"
      subtitle="灵石奖励、贷款额度、投喂与抽鬼牌图片、总结配图"
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
    <div v-if="showSkeleton" class="coin-skeleton" aria-busy="true" aria-live="polite">
      <BaseSkeleton height="1.25rem" width="8rem" />
      <div class="coin-skeleton__card">
        <BaseSkeleton height="1.5rem" width="40%" />
        <div class="coin-skeleton__grid">
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
        </div>
      </div>
      <div class="coin-skeleton__card">
        <BaseSkeleton height="1.5rem" width="40%" />
        <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
      </div>
    </div>

    <!-- 空状态 -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="Coins"
      title="暂无货币配置"
      description="尚未读取到任何货币配置数据，请尝试重新加载。"
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
    <form v-else-if="showForm" class="coin-form" novalidate @submit.prevent="onSubmit">
      <!-- 灵石基础 -->
      <section class="card">
        <h3 class="card__title font-display">
          <Coins class="card__title-icon" :size="16" aria-hidden="true" />
          灵石基础
        </h3>
        <div class="card__grid">
          <BaseInput :model-value="currencyName" label="货币名称" hint="只读，由后端固定" disabled />
          <BaseInput
            :model-value="form.daily_reward ?? ''"
            type="number"
            label="每日签到奖励"
            hint="非负整数"
            :error="fieldErrors.daily_reward"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('daily_reward', v)"
          />
          <BaseInput
            :model-value="form.chat_reward ?? ''"
            type="number"
            label="每日聊天奖励"
            hint="非负整数"
            :error="fieldErrors.chat_reward"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('chat_reward', v)"
          />
          <BaseInput
            :model-value="form.max_loan ?? ''"
            type="number"
            label="最大贷款额"
            hint="非负整数"
            :error="fieldErrors.max_loan"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('max_loan', v)"
          />
        </div>
      </section>

      <!-- 投喂与告白图片 -->
      <section class="card">
        <h3 class="card__title font-display">
          <ImageIcon class="card__title-icon" :size="16" aria-hidden="true" />
          投喂与告白图片
        </h3>
        <BaseInput
          :model-value="form.feeding_response_image_url ?? ''"
          label="投喂回应图片 URL"
          placeholder="https://..."
          :error="fieldErrors.feeding_response_image_url"
          :disabled="saving || loading"
          @update:model-value="(v) => setStr('feeding_response_image_url', v)"
        />
        <BaseInput
          :model-value="form.confession_response_image_url ?? ''"
          label="忏悔回应图片 URL"
          placeholder="https://..."
          :error="fieldErrors.confession_response_image_url"
          :disabled="saving || loading"
          @update:model-value="(v) => setStr('confession_response_image_url', v)"
        />
        <BaseInput
          :model-value="form.loan_thumbnail_url ?? ''"
          label="借贷中心缩略图 URL"
          placeholder="https://..."
          :error="fieldErrors.loan_thumbnail_url"
          :disabled="saving || loading"
          @update:model-value="(v) => setStr('loan_thumbnail_url', v)"
        />
      </section>

      <!-- 抽鬼牌图片（9 个动态键） -->
      <section class="card">
        <h3 class="card__title font-display">
          <Sparkles class="card__title-icon" :size="16" aria-hidden="true" />
          抽鬼牌图片
        </h3>
        <p class="card__hint">每张图片 URL 需以 http:// 或 https:// 开头，留空则使用默认值。</p>
        <div class="card__grid">
          <BaseInput
            v-for="key in GHOST_CARD_URL_KEYS"
            :key="key"
            :model-value="ghostUrl(key)"
            :label="GHOST_CARD_LABELS[key]"
            placeholder="https://..."
            :error="ghostError(key)"
            :disabled="saving || loading"
            @update:model-value="(v) => setGhostUrl(key, v)"
          />
        </div>
      </section>

      <!-- 投喂 Imagen 与总结配图 -->
      <section class="card">
        <h3 class="card__title font-display">
          <SlidersHorizontal class="card__title-icon" :size="16" aria-hidden="true" />
          投喂 Imagen 与总结配图
        </h3>
        <div class="toggle-row">
          <ToggleSwitch
            :model-value="!!form.feeding_imagen_enabled"
            label="投喂回应启用 Imagen 配图"
            :disabled="saving || loading"
            @update:model-value="(v) => patch('feeding_imagen_enabled', v)"
          />
          <p class="toggle-row__hint">开启后投喂回应将自动生成配图。</p>
        </div>
        <div class="toggle-row">
          <ToggleSwitch
            :model-value="!!form.summary_imagen_enabled"
            label="年度总结启用 Imagen 配图"
            :disabled="saving || loading"
            @update:model-value="(v) => patch('summary_imagen_enabled', v)"
          />
          <p class="toggle-row__hint">开启后年度总结将自动生成配图。</p>
        </div>
        <div class="card__grid">
          <BaseSelect
            :model-value="form.summary_imagen_resolution ?? 'default'"
            label="总结配图分辨率"
            :options="resolutionOptions"
            :error="fieldErrors.summary_imagen_resolution"
            :disabled="saving || loading"
            @update:model-value="(v) => setStr('summary_imagen_resolution', String(v))"
          />
          <BaseInput
            :model-value="form.summary_imagen_model ?? ''"
            label="总结配图模型"
            placeholder="留空则用默认 Imagen 模型"
            :error="fieldErrors.summary_imagen_model"
            :disabled="saving || loading"
            @update:model-value="(v) => setStr('summary_imagen_model', v)"
          />
          <BaseInput
            :model-value="form.feeding_cooldown_seconds ?? ''"
            type="number"
            label="投喂冷却（秒）"
            hint="非负整数"
            :error="fieldErrors.feeding_cooldown_seconds"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('feeding_cooldown_seconds', v)"
          />
          <BaseInput
            :model-value="form.feeding_daily_limit ?? ''"
            type="number"
            label="投喂每日上限"
            hint="非负整数"
            :error="fieldErrors.feeding_daily_limit"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('feeding_daily_limit', v)"
          />
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
      message="当前货币配置有未保存的更改，离开将丢弃这些更改。"
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
.coin-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.coin-skeleton__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.coin-skeleton__grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3);
}

/* ===== 表单卡片 ===== */
.coin-form {
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
.card__hint {
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
  .coin-skeleton__grid {
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
