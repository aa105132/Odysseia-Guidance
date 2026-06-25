<script setup lang="ts">
/* DailyOutfitView — 每日换装配置：调度、设计 API、风格偏好、当前装扮 + 立即换装/恢复默认。
 * 接 GET/PUT /api/config/daily-outfit + POST .../trigger + POST .../revert。
 * useConfigForm 统一 load/save/validate/dirty/beforeunload。
 *
 * ⚠️ 三处非标响应（均 {status, message/outfit}，非 {success, updated}）：
 *  - PUT 回 {status:'ok', message}，不含完整配置 → save 包装器 await 后再 GET 回填。
 *  - trigger 回 {status:'ok', outfit:{name,description,tags,...}} → 本地更新 current_outfit。
 *  - revert 回 {status:'ok', message} → loadForm(true) 重拉刷新 current_outfit。
 *
 * designer_api_key 为写入型敏感字段：GET 仅回 designer_api_key_masked（无明文），
 *  load/save 包装器返回时把 designer_api_key 置空，使其在 form/original 同为空，不进 dirty；
 *  用户填入才送出（useConfigForm 文档 L7-9 模式）。
 * notification_channel_id 留空时送 0（旧 SPA HEAD L6429）。
 * 8 状态 + dirty 路由离开拦截（onBeforeRouteLeave + BaseConfirmDialog）+ reduced-motion。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import {
  AlertTriangle,
  RotateCw,
  RotateCcw,
  Save,
  Shirt,
  Sparkles,
  Wand2,
} from 'lucide-vue-next';
import { useConfigForm } from '@/composables/useConfigForm';
import { useToastStore } from '@/stores/toast';
import { ApiError } from '@/api/client';
import {
  getDailyOutfitConfig,
  saveDailyOutfitConfig,
  triggerDailyOutfit,
  revertDailyOutfit,
} from '@/api/domains/dailyOutfit';
import type { DailyOutfitForm } from '@/api/domains/dailyOutfit';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';

const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');
const toast = useToastStore();

// ===== 字段级校验（前端早筛） =====
function validate(f: DailyOutfitForm): Record<string, string> | null {
  const e: Record<string, string> = {};
  const num = (v: unknown): v is number => typeof v === 'number' && !Number.isNaN(v);

  if (num(f.schedule_hour) && (f.schedule_hour < 0 || f.schedule_hour > 23))
    e.schedule_hour = '小时需在 0–23 之间';
  if (num(f.schedule_minute) && (f.schedule_minute < 0 || f.schedule_minute > 59))
    e.schedule_minute = '分钟需在 0–59 之间';

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
} = useConfigForm<DailyOutfitForm>({
  // load 包装：GET 后把写入型 designer_api_key 置空，使其不进 dirty
  load: async (): Promise<DailyOutfitForm> => {
    const cfg = await getDailyOutfitConfig();
    return { ...cfg, designer_api_key: '' };
  },
  // save 包装：PUT 回 {status, message}（非配置），await 后重新 GET 回填
  save: async (body) => {
    await saveDailyOutfitConfig(body);
    const cfg = await getDailyOutfitConfig();
    return { ...cfg, designer_api_key: '' };
  },
  validate,
  successMessage: '每日换装配置已保存',
});

// ===== 派生状态 =====
const hasData = computed(() => Object.keys(form.value || {}).length > 0);
const showSkeleton = computed(() => loading.value && !hasData.value);
const showEmpty = computed(() => !loading.value && !error.value && !hasData.value);
const showLoadError = computed(() => !loading.value && !!error.value && !hasData.value);
const showForm = computed(() => hasData.value);

// 当前装扮（只读）
const currentOutfit = computed(() => form.value.current_outfit);
const hasOutfit = computed(() => !!currentOutfit.value && !!currentOutfit.value.name);

// 设计 API Key 掩码占位
const apiKeyMasked = computed(() => form.value.designer_api_key_masked || '');
const apiKeyPlaceholder = computed(() =>
  apiKeyMasked.value ? `当前已设置（${apiKeyMasked.value}），留空则不修改` : '未设置，填入 API Key',
);

// 提示词是否为默认值（只读徽标）
const systemPromptIsDefault = computed(() => !!form.value.designer_system_prompt_is_default);
const userTemplateIsDefault = computed(() => !!form.value.designer_user_template_is_default);

// ===== 字段写入助手 =====
function setStr(key: keyof DailyOutfitForm, v: string): void {
  patch(key, v as unknown as DailyOutfitForm[keyof DailyOutfitForm]);
}
function setNum(key: keyof DailyOutfitForm, v: string): void {
  if (v === '') {
    patch(key, undefined as unknown as DailyOutfitForm[keyof DailyOutfitForm]);
    return;
  }
  const n = Number(v);
  if (!Number.isNaN(n)) patch(key, n as unknown as DailyOutfitForm[keyof DailyOutfitForm]);
}
// notification_channel_id 留空送 0（旧 SPA HEAD L6429）
function setChannelId(v: string): void {
  if (v === '') {
    patch('notification_channel_id', 0 as unknown as DailyOutfitForm['notification_channel_id']);
    return;
  }
  const n = Number(v);
  if (!Number.isNaN(n)) patch('notification_channel_id', n as unknown as DailyOutfitForm['notification_channel_id']);
}

// ===== 立即换装（trigger） =====
const triggering = ref(false);
async function onTrigger(): Promise<void> {
  if (triggering.value) return;
  triggering.value = true;
  try {
    const res = await triggerDailyOutfit();
    if (res.status === 'ok' && res.outfit) {
      // 本地更新 current_outfit（last_change_time 取当前时间，旧 SPA HEAD L6445）
      patch('current_outfit', {
        name: res.outfit.name ?? '',
        description: res.outfit.description ?? '',
        tags: res.outfit.tags ?? '',
        last_change_time: new Date().toLocaleString('zh-CN'),
      } as unknown as DailyOutfitForm['current_outfit']);
      toast.push({ type: 'success', message: `换装成功：${res.outfit.name ?? '新装扮'}` });
    } else {
      toast.push({ type: 'error', message: '换装未成功，请重试' });
    }
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : e instanceof Error ? e.message : '换装失败';
    toast.push({ type: 'error', message: `换装失败：${msg}` });
  } finally {
    triggering.value = false;
  }
}

// ===== 恢复默认（revert） =====
const revertConfirm = ref(false);
const reverting = ref(false);
function askRevert(): void {
  revertConfirm.value = true;
}
async function confirmRevert(): Promise<void> {
  reverting.value = true;
  try {
    const res = await revertDailyOutfit();
    if (res.status === 'ok') {
      toast.push({ type: 'success', message: res.message || '已恢复默认服装' });
      // 重新 GET 刷新 current_outfit
      await loadForm(true);
    } else {
      toast.push({ type: 'error', message: '恢复默认未成功，请重试' });
    }
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : e instanceof Error ? e.message : '恢复失败';
    toast.push({ type: 'error', message: `恢复默认失败：${msg}` });
  } finally {
    reverting.value = false;
    revertConfirm.value = false;
  }
}
function cancelRevert(): void {
  revertConfirm.value = false;
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
      :icon="Shirt"
      title="每日换装"
      subtitle="换装调度 · 设计 API · 风格偏好 · 当前装扮"
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
      :icon="Shirt"
      title="暂无换装配置"
      description="尚未读取到任何每日换装配置数据，请尝试重新加载。"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 加载错误 -->
    <BaseEmpty
      v-else-if="showLoadError"
      :icon="AlertTriangle"
      title="加载失败"
      :description="error ?? '无法读取每日换装配置。'"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 表单 -->
    <form v-else-if="showForm" class="form" novalidate @submit.prevent="onSubmit">
      <!-- 当前装扮（只读） + 操作 -->
      <section class="card card--outfit">
        <div class="card__head">
          <h3 class="card__title font-display">
            <Sparkles class="card__title-icon" :size="16" aria-hidden="true" />
            当前装扮
          </h3>
          <div class="card__head-actions">
            <BaseButton
              variant="secondary"
              size="sm"
              :icon="Wand2"
              :loading="triggering"
              :disabled="triggering || reverting || saving"
              @click="onTrigger"
            >
              立即换装
            </BaseButton>
            <BaseButton
              variant="danger"
              size="sm"
              :icon="RotateCcw"
              :loading="reverting"
              :disabled="triggering || reverting || saving"
              @click="askRevert"
            >
              恢复默认
            </BaseButton>
          </div>
        </div>
        <div v-if="hasOutfit" class="outfit">
          <div class="outfit__main">
            <span class="outfit__name font-display">{{ currentOutfit?.name }}</span>
            <p class="outfit__desc">{{ currentOutfit?.description || '暂无描述' }}</p>
          </div>
          <div class="outfit__meta">
            <div v-if="currentOutfit?.tags" class="outfit__row">
              <span class="outfit__key">标签</span>
              <span class="outfit__val">{{ currentOutfit.tags }}</span>
            </div>
            <div v-if="currentOutfit?.last_change_time" class="outfit__row">
              <span class="outfit__key">更换时间</span>
              <span class="outfit__val">{{ currentOutfit.last_change_time }}</span>
            </div>
          </div>
        </div>
        <BaseEmpty
          v-else
          :icon="Shirt"
          title="尚未换装"
          description="点击「立即换装」触发今日装扮设计，或保存配置后等待定时调度。"
        />
      </section>

      <!-- 调度与开关 -->
      <section class="card">
        <h3 class="card__title font-display">调度与开关</h3>
        <div class="toggle-row">
          <ToggleSwitch
            :model-value="!!form.enabled"
            label="启用每日换装"
            :disabled="saving"
            @update:model-value="(v) => patch('enabled', v as DailyOutfitForm['enabled'])"
          />
          <p class="toggle-row__hint">开启后按调度时间自动触发换装设计并推送通知。</p>
        </div>
        <div class="card__grid">
          <BaseInput
            :model-value="form.schedule_hour ?? ''"
            type="number"
            label="调度小时"
            hint="0–23"
            placeholder="8"
            :error="fieldErrors.schedule_hour"
            :disabled="saving"
            @update:model-value="(v) => setNum('schedule_hour', v)"
          />
          <BaseInput
            :model-value="form.schedule_minute ?? ''"
            type="number"
            label="调度分钟"
            hint="0–59"
            placeholder="0"
            :error="fieldErrors.schedule_minute"
            :disabled="saving"
            @update:model-value="(v) => setNum('schedule_minute', v)"
          />
          <BaseInput
            :model-value="form.notification_channel_id ?? ''"
            type="number"
            label="通知频道 ID"
            hint="留空则发送到 0"
            placeholder="0"
            :disabled="saving"
            @update:model-value="(v) => setChannelId(v)"
          />
        </div>
      </section>

      <!-- 设计 API -->
      <section class="card">
        <h3 class="card__title font-display">设计 API</h3>
        <BaseInput
          :model-value="form.designer_api_url ?? ''"
          label="API 地址"
          placeholder="https://..."
          :disabled="saving"
          @update:model-value="(v) => setStr('designer_api_url', v)"
        />
        <BaseInput
          :model-value="form.designer_api_key ?? ''"
          label="API Key"
          :placeholder="apiKeyPlaceholder"
          hint="写入型，留空不修改；GET 仅回掩码"
          :disabled="saving"
          @update:model-value="(v) => setStr('designer_api_key', v)"
        />
        <BaseInput
          :model-value="form.designer_model ?? ''"
          label="模型"
          placeholder="留空则用默认模型"
          :disabled="saving"
          @update:model-value="(v) => setStr('designer_model', v)"
        />
      </section>

      <!-- 风格偏好 -->
      <section class="card">
        <h3 class="card__title font-display">风格偏好</h3>
        <BaseInput
          :model-value="form.style_preference ?? ''"
          label="风格偏好"
          placeholder="如：清新、可爱、酷炫"
          :disabled="saving"
          @update:model-value="(v) => setStr('style_preference', v)"
        />
        <div class="field">
          <label class="field__label font-display">自定义提示词</label>
          <textarea
            :value="form.custom_prompt ?? ''"
            class="field__textarea"
            rows="3"
            aria-label="自定义提示词"
            placeholder="补充换装风格要求…"
            :disabled="saving"
            @input="(e) => setStr('custom_prompt', (e.target as HTMLTextAreaElement).value)"
          />
          <p class="field__hint">附加到换装设计请求的自定义指令。</p>
        </div>
      </section>

      <!-- 设计提示词模板 -->
      <section class="card">
        <h3 class="card__title font-display">设计提示词模板</h3>
        <div class="field">
          <div class="field__head">
            <label class="field__label font-display">系统提示词</label>
            <span v-if="systemPromptIsDefault" class="badge badge--default">默认</span>
            <span v-else class="badge badge--custom">已自定义</span>
          </div>
          <textarea
            :value="form.designer_system_prompt ?? ''"
            class="field__textarea"
            rows="4"
            aria-label="系统提示词"
            placeholder="设计 API 的系统提示词…"
            :disabled="saving"
            @input="(e) => setStr('designer_system_prompt', (e.target as HTMLTextAreaElement).value)"
          />
        </div>
        <div class="field">
          <div class="field__head">
            <label class="field__label font-display">用户模板</label>
            <span v-if="userTemplateIsDefault" class="badge badge--default">默认</span>
            <span v-else class="badge badge--custom">已自定义</span>
          </div>
          <textarea
            :value="form.designer_user_template ?? ''"
            class="field__textarea"
            rows="4"
            aria-label="用户模板"
            placeholder="设计 API 的用户提示词模板…"
            :disabled="saving"
            @input="(e) => setStr('designer_user_template', (e.target as HTMLTextAreaElement).value)"
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

    <!-- 恢复默认确认 -->
    <BaseConfirmDialog
      v-model="revertConfirm"
      title="恢复为默认服装？"
      message="将当前装扮恢复为默认服装，此操作会立即生效。"
      confirm-text="恢复"
      variant="danger"
      @confirm="confirmRevert"
      @cancel="cancelRevert"
    />

    <!-- 路由离开确认 -->
    <BaseConfirmDialog
      v-model="leaveConfirm"
      title="离开将丢弃未保存的修改"
      message="当前每日换装配置有未保存的更改，确定离开吗？"
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
.card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.card__head-actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
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

/* ===== 当前装扮卡 ===== */
.card--outfit {
  border-color: color-mix(in oklch, var(--accent) 25%, var(--border));
}
.outfit {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.outfit__main {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.outfit__name {
  font-size: var(--text-lg);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}
.outfit__desc {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: var(--lh-relaxed);
}
.outfit__meta {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding-top: var(--space-2);
  border-top: 1px solid var(--border);
}
.outfit__row {
  display: flex;
  gap: var(--space-2);
  font-size: var(--text-xs);
}
.outfit__key {
  flex: none;
  color: var(--text-muted);
  min-width: 4rem;
}
.outfit__val {
  color: var(--text-secondary);
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
.field__head {
  display: flex;
  align-items: center;
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
.field__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

/* 徽标 */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 0 var(--space-2);
  height: 1.25rem;
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  font-weight: var(--fw-medium);
}
.badge--default {
  background: var(--bg-surface-2);
  color: var(--text-muted);
  border: 1px solid var(--border);
}
.badge--custom {
  background: color-mix(in oklch, var(--accent) 12%, transparent);
  color: var(--accent);
  border: 1px solid color-mix(in oklch, var(--accent) 30%, transparent);
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
