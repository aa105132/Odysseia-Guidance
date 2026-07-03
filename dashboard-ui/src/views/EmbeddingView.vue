<script setup lang="ts">
/* EmbeddingView — 向量嵌入配置。
 * 接 GET/PUT /api/config/embedding。
 * useConfigForm 统一 load/save/validate/dirty/beforeunload；敏感字段 api_key 不回填，
 * 保存时只提交脏字段，避免空值误覆盖现有密钥。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { AlertTriangle, Database, RotateCw, Ruler, Save, Search, Server } from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import ChoiceChip from '@/components/ui/ChoiceChip.vue';
import PasswordInput from '@/components/ui/PasswordInput.vue';
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import { useConfigForm } from '@/composables/useConfigForm';
import { getEmbeddingConfig, saveEmbeddingConfig } from '@/api/domains/embedding';
import type { EmbeddingConfig } from '@/api/models';

const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');

const PROVIDERS = ['gemini', 'openai', 'siliconflow'];

function sanitize(cfg: EmbeddingConfig): EmbeddingConfig {
  return {
    ...cfg,
    api_key: '',
  };
}

function validate(f: EmbeddingConfig): Record<string, string> | null {
  const e: Record<string, string> = {};
  const provider = f.provider ?? '';
  const apiUrl = (f.api_url ?? '').trim();
  const model = (f.model ?? '').trim();

  if (provider && !PROVIDERS.includes(provider)) e.provider = '供应商必须是 gemini、openai 或 siliconflow';
  if (apiUrl && !apiUrl.startsWith('http://') && !apiUrl.startsWith('https://')) {
    e.api_url = 'API URL 必须以 http:// 或 https:// 开头';
  }
  if (!model) e.model = '模型名不能为空';
  if (typeof f.dimensions === 'number' && (f.dimensions < 1 || f.dimensions > 4096)) {
    e.dimensions = '向量维度需在 1–4096 之间';
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
  dirtyFields,
  loadForm,
  submit,
  reset,
  patch,
} = useConfigForm<EmbeddingConfig>({
  load: async () => sanitize(await getEmbeddingConfig()),
  save: async (body) => {
    const payload = { ...body };
    if (payload.api_key === '') delete payload.api_key;
    return sanitize(await saveEmbeddingConfig(payload));
  },
  validate,
  successMessage: '向量嵌入配置已保存',
});

const hasData = computed(() => Object.keys(form.value || {}).length > 0);
const showSkeleton = computed(() => loading.value && !hasData.value);
const showEmpty = computed(() => !loading.value && !error.value && !hasData.value);
const showLoadError = computed(() => !loading.value && !!error.value && !hasData.value);
const showForm = computed(() => hasData.value);

const providerOptions = computed(() => {
  const list = form.value.available_providers;
  if (list && list.length) {
    return list.map((p) => ({ value: p.id ?? '', label: p.name ?? p.id ?? '' }));
  }
  return [
    { value: 'gemini', label: 'Google Gemini' },
    { value: 'openai', label: 'OpenAI 兼容' },
    { value: 'siliconflow', label: '硅基流动' },
  ];
});

const modelOptions = computed(() => {
  const provider = form.value.provider ?? 'gemini';
  return form.value.available_models?.[provider] ?? [];
});

const apiUrlPlaceholder = computed(() => {
  switch (form.value.provider) {
    case 'openai':
      return 'https://api.openai.com/v1';
    case 'siliconflow':
      return 'https://api.siliconflow.cn/v1';
    default:
      return 'https://generativelanguage.googleapis.com/v1beta';
  }
});

const keyPlaceholder = computed(() =>
  form.value.api_key_masked || (form.value.has_api_key ? '已配置，输入新值覆盖' : '未配置'),
);

const statusText = computed(() => {
  if (!form.value.enabled) return '已停用';
  if (!form.value.has_api_key) return '缺少密钥';
  return '可用';
});

const statusClass = computed(() => {
  if (!form.value.enabled) return 'is-muted';
  if (!form.value.has_api_key) return 'is-warn';
  return 'is-ok';
});

function setStr(key: keyof EmbeddingConfig, v: string): void {
  patch(key, v as unknown as EmbeddingConfig[keyof EmbeddingConfig]);
}

function setNum(key: keyof EmbeddingConfig, v: string): void {
  if (v === '') {
    patch(key, undefined as unknown as EmbeddingConfig[keyof EmbeddingConfig]);
    return;
  }
  const n = Number(v);
  if (!Number.isNaN(n)) patch(key, n as unknown as EmbeddingConfig[keyof EmbeddingConfig]);
}

function chooseProvider(v: string | number | (string | number)[]): void {
  const provider = String(Array.isArray(v) ? v[0] : v);
  patch('provider', provider);
  const nextDefault = form.value.available_providers?.find((p) => p.id === provider)?.default_model;
  if (nextDefault) patch('model', nextDefault);
}

function chooseModel(model: string): void {
  patch('model', model);
}

function retry(): void {
  loadForm(true).catch(() => {
    /* 错误已由 useConfigForm 内部 toast 并置 error */
  });
}

function onSubmit(): void {
  void submit();
}

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
      :icon="Search"
      title="向量嵌入"
      subtitle="嵌入模型接入 · 向量维度 · 向量库检索底座"
    />

    <div v-if="error && showForm" class="error-banner" role="alert">
      <div class="error-banner__text">
        <AlertTriangle :size="18" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>
      <BaseButton variant="ghost" size="sm" :icon="RotateCw" @click="retry">重试</BaseButton>
    </div>

    <div v-if="showSkeleton" class="skeleton-stack" aria-busy="true" aria-live="polite">
      <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
      <BaseSkeleton height="7rem" rounded="var(--radius-lg)" />
      <BaseSkeleton height="12rem" rounded="var(--radius-lg)" />
      <BaseSkeleton height="8rem" rounded="var(--radius-lg)" />
    </div>

    <BaseEmpty
      v-else-if="showEmpty"
      :icon="Database"
      title="暂无向量嵌入配置"
      description="尚未读取到嵌入配置，请确认后端服务正常后重试。"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <BaseEmpty
      v-else-if="showLoadError"
      :icon="AlertTriangle"
      title="配置加载失败"
      :description="error ?? '无法读取向量嵌入配置。'"
      action-text="重试"
      :action-icon="RotateCw"
      @action="retry"
    />

    <form v-else-if="showForm" class="embedding-form" novalidate @submit.prevent="onSubmit">
      <section class="status-strip" :class="statusClass" aria-live="polite">
        <span class="status-strip__dot" aria-hidden="true" />
        <div class="status-strip__main">
          <span class="status-strip__label">嵌入服务{{ statusText }}</span>
          <span class="status-strip__meta">
            {{ form.provider ?? 'gemini' }} · {{ form.model ?? '未设置模型' }} · {{ form.dimensions ?? '—' }} 维
          </span>
        </div>
      </section>

      <section class="block">
        <h3 class="block__title font-display">
          <Server class="block__title-icon" :size="16" aria-hidden="true" />
          服务接入
        </h3>
        <div class="toggle-row">
          <ToggleSwitch
            :model-value="!!form.enabled"
            label="启用向量嵌入"
            :disabled="saving || loading"
            @update:model-value="(v) => patch('enabled', v)"
          />
          <p class="toggle-row__hint">关闭后依赖嵌入向量的检索链路将无法更新向量。</p>
        </div>
        <div class="choice-field">
          <span class="field-label font-display">供应商</span>
          <ChoiceChip
            :model-value="form.provider ?? 'gemini'"
            :options="providerOptions"
            label="向量嵌入供应商"
            @update:model-value="chooseProvider"
          />
          <p v-if="fieldErrors.provider" class="field-error" role="alert">{{ fieldErrors.provider }}</p>
        </div>
        <BaseInput
          :model-value="form.api_url ?? ''"
          label="API URL"
          type="url"
          :placeholder="apiUrlPlaceholder"
          :error="fieldErrors.api_url"
          :disabled="saving || loading"
          @update:model-value="(v) => setStr('api_url', v)"
        />
        <div class="key-field">
          <PasswordInput
            :model-value="form.api_key ?? ''"
            label="API Key"
            :placeholder="keyPlaceholder"
            :error="fieldErrors.api_key"
            :disabled="saving || loading"
            @update:model-value="(v) => setStr('api_key', v)"
          />
          <span class="badge" :class="form.has_api_key ? 'badge--ok' : 'badge--warn'">
            {{ form.has_api_key ? '密钥已配置' : '密钥未配置' }}
          </span>
        </div>
      </section>

      <section class="block">
        <h3 class="block__title font-display">
          <Database class="block__title-icon" :size="16" aria-hidden="true" />
          模型与向量
        </h3>
        <div class="grid">
          <BaseInput
            :model-value="form.model ?? ''"
            label="嵌入模型"
            placeholder="gemini-embedding-001"
            :error="fieldErrors.model"
            :disabled="saving || loading"
            @update:model-value="(v) => setStr('model', v)"
          />
          <BaseInput
            :model-value="form.dimensions ?? ''"
            label="向量维度"
            type="number"
            hint="1–4096"
            :error="fieldErrors.dimensions"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('dimensions', v)"
          />
        </div>
        <div v-if="modelOptions.length" class="model-presets">
          <span class="field-label font-display">常用模型</span>
          <div class="model-presets__list" aria-label="常用嵌入模型">
            <button
              v-for="model in modelOptions"
              :key="model"
              type="button"
              :class="['preset-chip', { 'is-selected': model === form.model }]"
              :disabled="saving || loading"
              @click="chooseModel(model)"
            >
              {{ model }}
            </button>
          </div>
        </div>
      </section>

      <section class="block block--notes">
        <h3 class="block__title font-display">
          <Ruler class="block__title-icon" :size="16" aria-hidden="true" />
          向量库说明
        </h3>
        <div class="note-grid">
          <div class="note">
            <span class="note__label">存储后端</span>
            <span class="note__value">PostgreSQL pgvector</span>
          </div>
          <div class="note">
            <span class="note__label">当前索引</span>
            <span class="note__value">HNSW cosine</span>
          </div>
          <div class="note">
            <span class="note__label">重建入口</span>
            <span class="note__value">scripts/re_embed_knowledge.py</span>
          </div>
        </div>
        <p class="block__hint">本页负责模型接入配置；知识库重嵌入仍由脚本执行，后端暂无独立重建端点。</p>
      </section>

      <div class="actions">
        <span v-if="dirty" class="actions__dirty" role="status">
          <AlertTriangle :size="14" aria-hidden="true" />
          {{ dirtyFields.length }} 项未保存
        </span>
        <span v-else-if="!saving" class="actions__saved">已同步</span>
        <BaseButton
          variant="ghost"
          size="md"
          :disabled="!dirty || saving || loading"
          @click="reset"
        >
          放弃修改
        </BaseButton>
        <BaseButton
          type="submit"
          variant="primary"
          size="md"
          :loading="saving"
          :disabled="!dirty || saving || loading"
          :icon="Save"
        >
          保存配置
        </BaseButton>
      </div>
    </form>

    <BaseConfirmDialog
      v-model="leaveConfirm"
      title="离开将丢弃未保存的修改"
      message="当前向量嵌入配置有未保存的更改，确定离开吗？"
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

.skeleton-stack,
.embedding-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.status-strip {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.status-strip__dot {
  flex: none;
  width: 0.65rem;
  height: 0.65rem;
  border-radius: 9999px;
  background: var(--text-muted);
}
.status-strip__main {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}
.status-strip__label {
  color: var(--text-primary);
  font-weight: var(--fw-semibold);
}
.status-strip__meta {
  color: var(--text-muted);
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}
.status-strip.is-ok {
  border-color: color-mix(in oklch, var(--success) 45%, transparent);
}
.status-strip.is-ok .status-strip__dot {
  background: var(--success);
}
.status-strip.is-warn {
  border-color: color-mix(in oklch, var(--warning) 45%, transparent);
}
.status-strip.is-warn .status-strip__dot {
  background: var(--warning);
}
.status-strip.is-muted .status-strip__dot {
  background: var(--text-muted);
}

.block {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.block:hover {
  border-color: var(--border-strong);
}
.block__title {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}
.block__title-icon {
  flex: none;
  color: var(--accent);
}
.block__hint {
  margin: 0;
  color: var(--text-muted);
  font-size: var(--text-xs);
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4) var(--space-5);
}

.toggle-row,
.choice-field,
.key-field,
.model-presets {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.toggle-row__hint {
  margin: 0;
  color: var(--text-muted);
  font-size: var(--text-xs);
}
.field-label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.field-error {
  margin: 0;
  color: var(--danger);
  font-size: var(--text-xs);
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

.model-presets__list {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.preset-chip {
  display: inline-flex;
  align-items: center;
  padding: var(--space-1) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-inset);
  color: var(--text-secondary);
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  cursor: pointer;
  white-space: nowrap;
  transition: background-color var(--dur-micro) var(--ease-out-quart),
    border-color var(--dur-micro) var(--ease-out-quart),
    color var(--dur-micro) var(--ease-out-quart);
}
.preset-chip:hover {
  background: var(--bg-surface-2);
  border-color: var(--border-strong);
  color: var(--text-primary);
}
.preset-chip.is-selected {
  background: var(--accent-subtle);
  border-color: var(--accent);
  color: var(--text-primary);
  font-weight: var(--fw-medium);
}
.preset-chip:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.preset-chip:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.note-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
}
.note {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-4);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.note__label {
  color: var(--text-muted);
  font-size: var(--text-xs);
}
.note__value {
  color: var(--text-primary);
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}

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
  color: var(--warning);
  font-size: var(--text-sm);
}
.actions__saved {
  margin-right: auto;
  color: var(--text-muted);
  font-size: var(--text-sm);
}

@media (max-width: 768px) {
  .grid,
  .note-grid {
    grid-template-columns: 1fr;
  }
  .actions {
    position: static;
    flex-direction: column-reverse;
    align-items: stretch;
  }
}

@media (prefers-reduced-motion: reduce) {
  .block,
  .preset-chip {
    transition: none;
  }
}
</style>
