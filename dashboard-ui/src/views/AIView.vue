<script setup lang="ts">
/* AIView — AI 对话核心配置。
 * 接 GET/PUT /api/config/ai + POST /api/config/reload-api-keys + POST /api/models/list。
 * useConfigForm 统一 load/save/validate/dirty/beforeunload；保存只送脏字段（PATCH 语义）。
 * 敏感字段 api_key 永不回填：load 包装器清空，使其在 form/original 同为空，不误入 dirty。
 * PUT 仅回 {success, updated}，save 包装器 await 后重新 GET 刷新掩码字段。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { AlertTriangle, Brain, KeyRound, RotateCw, Save, Sparkles, SlidersHorizontal } from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import ChoiceChip from '@/components/ui/ChoiceChip.vue';
import PasswordInput from '@/components/ui/PasswordInput.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import ModelFetcherSelect from '@/components/shared/ModelFetcherSelect.vue';
import { useConfigForm } from '@/composables/useConfigForm';
import { useToastStore } from '@/stores/toast';
import {
  getAIConfig,
  saveAIConfig,
  listModels,
  reloadApiKeys,
  type ModelListRequest,
} from '@/api/domains/ai';
import type { AIConfig } from '@/api/models';

const toast = useToastStore();

// 顶栏手动刷新注入：注册当前视图的 force 刷新 = 强制重拉配置 + 重置 dirty
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');

// ===== 字段级校验（前端早筛，减少 400 往返；范围取自 AIConfigUpdate） =====
function validate(f: AIConfig): Record<string, string> | null {
  const e: Record<string, string> = {};
  const num = (v: unknown): v is number => typeof v === 'number' && !Number.isNaN(v);
  if (num(f.temperature) && (f.temperature < 0 || f.temperature > 2))
    e.temperature = 'temperature 需在 0–2 之间';
  if (num(f.max_tokens) && (f.max_tokens < 1 || f.max_tokens > 65536))
    e.max_tokens = 'max_tokens 需在 1–65536 之间';
  if (num(f.channel_history_limit) && (f.channel_history_limit < 5 || f.channel_history_limit > 500))
    e.channel_history_limit = '频道历史条数需在 5–500 之间';
  if (num(f.newspaper_brief_threshold) && (f.newspaper_brief_threshold < 50 || f.newspaper_brief_threshold > 5000))
    e.newspaper_brief_threshold = '报纸摘要阈值需在 50–5000 之间';
  if (num(f.max_attempts_per_key) && (f.max_attempts_per_key < 1 || f.max_attempts_per_key > 10))
    e.max_attempts_per_key = '单密钥重试需在 1–10 之间';
  if (num(f.retry_delay_seconds) && (f.retry_delay_seconds < 0 || f.retry_delay_seconds > 30))
    e.retry_delay_seconds = '重试延迟需在 0–30 秒';
  if (num(f.max_key_rotation_retries) && (f.max_key_rotation_retries < 1 || f.max_key_rotation_retries > 20))
    e.max_key_rotation_retries = '密钥轮换重试需在 1–20 之间';
  if (f.api_format && !['gemini', 'openai', 'interactions'].includes(f.api_format))
    e.api_format = 'API 格式仅支持 gemini、openai 或 interactions';
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
} = useConfigForm<AIConfig>({
  // load 包装器：清空敏感字段，使其在 form 与 original 同为空，避免误入 dirty 集合
  load: async () => {
    const data = await getAIConfig();
    data.api_key = '';
    return data;
  },
  // save 包装器：PUT 仅回 {success, updated}，需重新 GET 刷新掩码字段并重置 dirty
  save: async (body) => {
    const res = await saveAIConfig(body);
    const u = res?.updated ?? {};
    // 密钥热更新副作用反馈（api.py 行 1412-1426）
    const err = u.api_keys_reload_error;
    if (typeof err === 'string' && err) {
      toast.push({ type: 'warning', message: `API 密钥热更新失败：${err}` });
    } else if (u.api_keys_pending_restart) {
      toast.push({ type: 'info', message: '密钥将在下次重启后生效（GeminiService 未初始化）', duration: 4000 });
    } else if (u.api_keys_reloaded) {
      toast.push({ type: 'success', message: 'API 密钥已热更新到 GeminiService', duration: 2500 });
    }
    const fresh = await getAIConfig();
    fresh.api_key = '';
    return fresh;
  },
  validate,
  successMessage: 'AI 配置已保存',
});

// ===== 派生状态 =====
const hasData = computed(() => !!form.value.model || Object.keys(form.value || {}).length > 0);
const showSkeleton = computed(() => loading.value && !hasData.value);
const showEmpty = computed(() => !loading.value && !error.value && !hasData.value);
const showLoadError = computed(() => !loading.value && !!error.value && !hasData.value);
const showForm = computed(() => hasData.value);

const hasApiKey = computed(() => !!form.value.has_api_key);
const apiKeyPlaceholder = computed(
  () => form.value.api_key_masked || (hasApiKey.value ? '已配置，输入新值覆盖' : '未配置'),
);
const apiUrlPlaceholder = computed(
  () => (form.value.api_format === 'openai' ? 'https://api.openai.com/v1' : 'https://generativelanguage.googleapis.com/v1beta'),
);

const apiFormatOptions = [
  { value: 'gemini', label: 'Gemini' },
  { value: 'interactions', label: 'Interactions' },
  { value: 'openai', label: 'OpenAI' },
];

// ===== 字段写入助手（BaseInput 始终 emit string，数值字段需转换） =====
function setStr(key: keyof AIConfig, v: string): void {
  patch(key, v as unknown as AIConfig[keyof AIConfig]);
}
function setNum(key: keyof AIConfig, v: string): void {
  if (v === '') {
    patch(key, undefined as unknown as AIConfig[keyof AIConfig]);
    return;
  }
  const n = Number(v);
  if (!Number.isNaN(n)) patch(key, n as unknown as AIConfig[keyof AIConfig]);
}

// ===== 模型懒拉取：闭包捕获当前 api_url/api_key/api_format，未输入密钥传 null 走环境变量 =====
async function fetchAIModels(): Promise<string[]> {
  const req: ModelListRequest = {
    api_url: form.value.api_url || null,
    api_key: form.value.api_key || null,
    api_format: form.value.api_format || 'gemini',
    model_type: 'chat',
  };
  const r = await listModels(req);
  return r.models;
}

// ===== 密钥热重载（独立按钮，不影响表单 dirty） =====
const reloading = ref(false);
async function doReloadApiKeys(): Promise<void> {
  if (reloading.value) return;
  reloading.value = true;
  try {
    const r = await reloadApiKeys();
    toast.push({
      type: 'success',
      message: r.message || `API 密钥已热重载（${r.key_count ?? 0} 个）`,
      duration: 3000,
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : '热重载失败';
    toast.push({ type: 'error', message: `密钥热重载失败：${msg}` });
  } finally {
    reloading.value = false;
  }
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
  // 顶栏 @refresh → 强制重拉 + 重置 dirty（配置静态，无需轮询）
  registerRefresh?.(() => loadForm(true));
});
</script>

<template>
  <div class="view">
    <BaseSectionTitle
      :icon="Brain"
      title="AI 设置"
      subtitle="月月对话核心模型、接入与重试参数"
    />

    <!-- 保存时错误横幅（表单已存在，inline + 字段级错误） -->
    <div v-if="error && showForm" class="error-banner" role="alert">
      <div class="error-banner__text">
        <AlertTriangle :size="18" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>
      <BaseButton variant="ghost" size="sm" :icon="RotateCw" @click="retry">重试</BaseButton>
    </div>

    <!-- 骨架屏：初始加载 -->
    <div v-if="showSkeleton" class="ai-skeleton" aria-busy="true" aria-live="polite">
      <BaseSkeleton height="1.25rem" width="8rem" />
      <div class="ai-skeleton__card">
        <BaseSkeleton height="1.5rem" width="40%" />
        <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
        <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
      </div>
      <div class="ai-skeleton__card">
        <BaseSkeleton height="1.5rem" width="40%" />
        <div class="ai-skeleton__grid">
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
        </div>
      </div>
    </div>

    <!-- 空状态：已加载但无配置（AI 域后端总有默认值，兜底） -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="Brain"
      title="暂无 AI 配置"
      description="尚未读取到任何 AI 配置数据，请尝试重新加载。"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 加载错误：初始拉取失败，提供重试 -->
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
    <form v-else-if="showForm" class="ai-form" novalidate @submit.prevent="onSubmit">
      <!-- 模型 -->
      <section class="card">
        <h3 class="card__title font-display">
          <Sparkles class="card__title-icon" :size="16" aria-hidden="true" />
          模型
        </h3>
        <ModelFetcherSelect
          :model-value="form.model ?? ''"
          :fetch-models="fetchAIModels"
          label="主模型"
          :disabled="saving || loading"
          hint="点击加载可用模型，当前模型不在列表时保留为兜底选项。"
          :empty-description="'未获取到可用模型，请检查 API URL/Key 与格式后重试。'"
          @update:model-value="(v) => setStr('model', v)"
        />
        <div class="card__grid">
          <BaseInput
            :model-value="form.summary_model ?? ''"
            label="摘要模型"
            placeholder="留空则与主模型一致"
            :error="fieldErrors.summary_model"
            :disabled="saving || loading"
            @update:model-value="(v) => setStr('summary_model', v)"
          />
          <BaseInput
            :model-value="form.query_model ?? ''"
            label="查询重写模型"
            placeholder="留空则与主模型一致"
            :error="fieldErrors.query_model"
            :disabled="saving || loading"
            @update:model-value="(v) => setStr('query_model', v)"
          />
          <BaseInput
            :model-value="form.persona_name ?? ''"
            label="人设名称"
            hint="只读，由后端固定"
            disabled
          />
        </div>
      </section>

      <!-- API 接入 -->
      <section class="card">
        <h3 class="card__title font-display">
          <KeyRound class="card__title-icon" :size="16" aria-hidden="true" />
          API 接入
        </h3>
        <div class="choice-field">
          <span class="field-label font-display">API 格式</span>
          <ChoiceChip
            :model-value="form.api_format ?? 'gemini'"
            :options="apiFormatOptions"
            @update:model-value="(v) => setStr('api_format', String(v))"
          />
          <p v-if="fieldErrors.api_format" class="field-error" role="alert">{{ fieldErrors.api_format }}</p>
        </div>
        <BaseInput
          :model-value="form.api_url ?? ''"
          label="API URL"
          :placeholder="apiUrlPlaceholder"
          :error="fieldErrors.api_url"
          :disabled="saving || loading"
          @update:model-value="(v) => setStr('api_url', v)"
        />
        <div class="key-field">
          <PasswordInput
            :model-value="form.api_key ?? ''"
            label="API Key"
            :placeholder="apiKeyPlaceholder"
            :error="fieldErrors.api_key"
            :disabled="saving || loading"
          />
          <span class="badge" :class="hasApiKey ? 'badge--ok' : 'badge--warn'">
            {{ hasApiKey ? '密钥已配置' : '密钥未配置' }}
          </span>
        </div>
        <div class="reload-row">
          <BaseButton
            variant="secondary"
            size="md"
            :loading="reloading"
            :disabled="saving || loading"
            :icon="KeyRound"
            @click="doReloadApiKeys"
          >
            热重载密钥
          </BaseButton>
          <p class="reload-row__hint">从环境变量重新加载 GeminiService 密钥，无需重启 Bot。</p>
        </div>
      </section>

      <!-- 生成参数 -->
      <section class="card">
        <h3 class="card__title font-display">
          <SlidersHorizontal class="card__title-icon" :size="16" aria-hidden="true" />
          生成参数
        </h3>
        <div class="card__grid">
          <BaseInput
            :model-value="form.temperature ?? ''"
            type="number"
            label="Temperature"
            hint="0–2，步进 0.1"
            :error="fieldErrors.temperature"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('temperature', v)"
          />
          <BaseInput
            :model-value="form.max_tokens ?? ''"
            type="number"
            label="Max Tokens"
            hint="1–65536"
            :error="fieldErrors.max_tokens"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('max_tokens', v)"
          />
          <BaseInput
            :model-value="form.channel_history_limit ?? ''"
            type="number"
            label="频道历史条数"
            hint="5–500"
            :error="fieldErrors.channel_history_limit"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('channel_history_limit', v)"
          />
          <BaseInput
            :model-value="form.newspaper_brief_threshold ?? ''"
            type="number"
            label="报纸摘要阈值"
            hint="50–5000 字符"
            :error="fieldErrors.newspaper_brief_threshold"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('newspaper_brief_threshold', v)"
          />
        </div>
        <div class="toggle-row">
          <ToggleSwitch
            :model-value="!!form.long_reply_in_dm_enabled"
            label="超长回复改发私信"
            :disabled="saving || loading"
            @update:model-value="(v) => patch('long_reply_in_dm_enabled', v)"
          />
          <p class="toggle-row__hint">开启后超长回复将以私信发送，避免刷屏频道。</p>
        </div>
      </section>

      <!-- 重试与密钥轮换 -->
      <section class="card">
        <h3 class="card__title font-display">
          <RotateCw class="card__title-icon" :size="16" aria-hidden="true" />
          重试与密钥轮换
        </h3>
        <div class="card__grid">
          <BaseInput
            :model-value="form.max_attempts_per_key ?? ''"
            type="number"
            label="单密钥最大重试"
            hint="1–10 次"
            :error="fieldErrors.max_attempts_per_key"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('max_attempts_per_key', v)"
          />
          <BaseInput
            :model-value="form.retry_delay_seconds ?? ''"
            type="number"
            label="重试延迟"
            hint="0–30 秒"
            :error="fieldErrors.retry_delay_seconds"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('retry_delay_seconds', v)"
          />
          <BaseInput
            :model-value="form.max_key_rotation_retries ?? ''"
            type="number"
            label="密钥轮换重试"
            hint="1–20 次"
            :error="fieldErrors.max_key_rotation_retries"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('max_key_rotation_retries', v)"
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
      message="当前 AI 配置有未保存的更改，离开将丢弃这些更改。"
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
.ai-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.ai-skeleton__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.ai-skeleton__grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3);
}

/* ===== 表单卡片 ===== */
.ai-form {
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

/* ChoiceChip 无 label prop，外裹 field-label 保持与 BaseInput label 一致 */
.choice-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.field-label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.field-error {
  font-size: var(--text-xs);
  color: var(--danger);
}

/* API Key 字段 + 状态徽标 */
.key-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.badge {
  align-self: flex-start;
  padding: 0 var(--space-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  line-height: var(--lh-tight);
}
.badge--ok {
  color: var(--success);
  border-color: color-mix(in oklch, var(--success) 40%, transparent);
  background: color-mix(in oklch, var(--success) 10%, transparent);
}
.badge--warn {
  color: var(--warning);
  border-color: color-mix(in oklch, var(--warning) 40%, transparent);
  background: color-mix(in oklch, var(--warning) 10%, transparent);
}

/* 热重载按钮行 */
.reload-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.reload-row__hint {
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
  .ai-skeleton__grid {
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
