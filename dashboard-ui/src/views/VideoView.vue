<script setup lang="ts">
/* VideoView — 视频生成配置。
 * 接 GET/PUT /api/config/video。
 * useConfigForm 统一 load/save/validate/dirty/beforeunload；敏感字段 api_key 不回填，
 * 保存时只提交脏字段，避免空值误覆盖现有密钥。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { AlertTriangle, Clock3, Film, KeyRound, RotateCw, Save, Server, Video, Zap } from 'lucide-vue-next';
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
import { getVideoConfig, saveVideoConfig } from '@/api/domains/video';
import type { VideoConfig } from '@/api/models';

const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');

function sanitize(cfg: VideoConfig): VideoConfig {
  return {
    ...cfg,
    api_key: '',
  };
}

function validate(f: VideoConfig): Record<string, string> | null {
  const e: Record<string, string> = {};
  const apiUrl = (f.api_url ?? '').trim();
  const model = (f.model ?? '').trim();

  if (apiUrl && !apiUrl.startsWith('http://') && !apiUrl.startsWith('https://')) {
    e.api_url = 'API URL 必须以 http:// 或 https:// 开头';
  }
  if (!model) e.model = '文生视频模型不能为空';
  if (f.video_format && !['url', 'html'].includes(f.video_format)) {
    e.video_format = '视频格式必须是 url 或 html';
  }
  if (typeof f.generation_cost === 'number' && f.generation_cost < 0) {
    e.generation_cost = '生成成本不能为负数';
  }
  if (typeof f.max_duration === 'number' && (f.max_duration < 6 || f.max_duration > 30)) {
    e.max_duration = '最大时长需在 6–30 秒之间';
  }
  if (typeof f.default_videos === 'number' && (f.default_videos < 1 || f.default_videos > 8)) {
    e.default_videos = '默认生成数量需在 1–8 之间';
  }
  if (typeof f.max_concurrent_tasks === 'number' && (f.max_concurrent_tasks < 1 || f.max_concurrent_tasks > 8)) {
    e.max_concurrent_tasks = '并发上限需在 1–8 之间';
  }
  if (typeof f.empty_result_max_retries === 'number' && (f.empty_result_max_retries < 0 || f.empty_result_max_retries > 10)) {
    e.empty_result_max_retries = '空回重试需在 0–10 次之间';
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
} = useConfigForm<VideoConfig>({
  load: async () => sanitize(await getVideoConfig()),
  save: async (body) => {
    const payload = { ...body };
    if (payload.api_key === '') delete payload.api_key;
    return sanitize(await saveVideoConfig(payload));
  },
  validate,
  successMessage: '视频配置已保存',
});

const hasData = computed(() => Object.keys(form.value || {}).length > 0);
const showSkeleton = computed(() => loading.value && !hasData.value);
const showEmpty = computed(() => !loading.value && !error.value && !hasData.value);
const showLoadError = computed(() => !loading.value && !!error.value && !hasData.value);
const showForm = computed(() => hasData.value);

const formatOptions = [
  { value: 'url', label: 'URL' },
  { value: 'html', label: 'HTML' },
];

const keyPlaceholder = computed(() =>
  form.value.api_key_masked || (form.value.has_api_key ? '已配置，输入新值覆盖' : '未配置'),
);

const statusText = computed(() => {
  if (!form.value.enabled) return '已停用';
  if (!form.value.has_api_key) return '缺少密钥';
  if (!form.value.service_available) return '未就绪';
  return '运行中';
});

const statusClass = computed(() => {
  if (!form.value.enabled) return 'is-muted';
  if (!form.value.has_api_key || !form.value.service_available) return 'is-warn';
  return 'is-ok';
});

const modelSummary = computed(() => {
  const model = form.value.model || '未设置模型';
  const i2v = form.value.i2v_model || '图生视频沿用默认';
  return `${model} · ${i2v}`;
});

function setStr(key: keyof VideoConfig, v: string): void {
  patch(key, v as unknown as VideoConfig[keyof VideoConfig]);
}

function setNum(key: keyof VideoConfig, v: string): void {
  if (v === '') {
    patch(key, undefined as unknown as VideoConfig[keyof VideoConfig]);
    return;
  }
  const n = Number(v);
  if (!Number.isNaN(n)) patch(key, n as unknown as VideoConfig[keyof VideoConfig]);
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
      :icon="Video"
      title="视频设置"
      subtitle="文生视频 · 图生视频 · 并发与重试策略"
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
      <BaseSkeleton height="10rem" rounded="var(--radius-lg)" />
    </div>

    <BaseEmpty
      v-else-if="showEmpty"
      :icon="Video"
      title="暂无视频配置"
      description="尚未读取到视频生成配置，请确认后端服务正常后重试。"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <BaseEmpty
      v-else-if="showLoadError"
      :icon="AlertTriangle"
      title="配置加载失败"
      :description="error ?? '无法读取视频配置。'"
      action-text="重试"
      :action-icon="RotateCw"
      @action="retry"
    />

    <form v-else-if="showForm" class="video-form" novalidate @submit.prevent="onSubmit">
      <section class="status-strip" :class="statusClass" aria-live="polite">
        <span class="status-strip__dot" aria-hidden="true" />
        <div class="status-strip__main">
          <span class="status-strip__label">视频服务{{ statusText }}</span>
          <span class="status-strip__meta">
            {{ modelSummary }} · {{ form.video_format ?? 'url' }} · 最长 {{ form.max_duration ?? '—' }} 秒
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
            label="启用视频生成"
            :disabled="saving || loading"
            @update:model-value="(v) => patch('enabled', v)"
          />
          <p class="toggle-row__hint">开启后对话工具与相关命令可调用视频生成服务。</p>
        </div>
        <div class="readonly-field">
          <span class="field-label font-display">API 格式</span>
          <span class="readonly-field__value">{{ form.api_format ?? 'openai' }}</span>
          <p class="readonly-field__hint">当前后端以环境配置读取该值，本页不写入 API 格式。</p>
        </div>
        <BaseInput
          :model-value="form.api_url ?? ''"
          label="API URL"
          type="url"
          placeholder="https://api.x.ai/v1"
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
            <KeyRound :size="12" aria-hidden="true" />
            {{ form.has_api_key ? '密钥已配置' : '密钥未配置' }}
          </span>
        </div>
      </section>

      <section class="block">
        <h3 class="block__title font-display">
          <Film class="block__title-icon" :size="16" aria-hidden="true" />
          模型与输出
        </h3>
        <div class="grid">
          <BaseInput
            :model-value="form.model ?? ''"
            label="文生视频模型"
            placeholder="grok-imagine-1.0-video"
            :error="fieldErrors.model"
            :disabled="saving || loading"
            @update:model-value="(v) => setStr('model', v)"
          />
          <BaseInput
            :model-value="form.i2v_model ?? ''"
            label="图生视频模型"
            placeholder="留空则沿用后端默认"
            :error="fieldErrors.i2v_model"
            :disabled="saving || loading"
            @update:model-value="(v) => setStr('i2v_model', v)"
          />
        </div>
        <div class="choice-field">
          <span class="field-label font-display">返回格式</span>
          <ChoiceChip
            :model-value="form.video_format ?? 'url'"
            :options="formatOptions"
            label="视频返回格式"
            @update:model-value="(v) => setStr('video_format', String(v))"
          />
          <p v-if="fieldErrors.video_format" class="field-error" role="alert">{{ fieldErrors.video_format }}</p>
        </div>
      </section>

      <section class="block">
        <h3 class="block__title font-display">
          <Clock3 class="block__title-icon" :size="16" aria-hidden="true" />
          生成限制
        </h3>
        <div class="grid">
          <BaseInput
            :model-value="form.generation_cost ?? ''"
            label="生成消耗"
            type="number"
            hint="不能为负数"
            :error="fieldErrors.generation_cost"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('generation_cost', v)"
          />
          <BaseInput
            :model-value="form.max_duration ?? ''"
            label="最大时长（秒）"
            type="number"
            hint="6–30"
            :error="fieldErrors.max_duration"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('max_duration', v)"
          />
          <BaseInput
            :model-value="form.default_videos ?? ''"
            label="默认生成数量"
            type="number"
            hint="1–8"
            :error="fieldErrors.default_videos"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('default_videos', v)"
          />
          <BaseInput
            :model-value="form.max_concurrent_tasks ?? ''"
            label="并发上限"
            type="number"
            hint="1–8"
            :error="fieldErrors.max_concurrent_tasks"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('max_concurrent_tasks', v)"
          />
          <BaseInput
            :model-value="form.empty_result_max_retries ?? ''"
            label="空回重试"
            type="number"
            hint="0–10，生成服务共用"
            :error="fieldErrors.empty_result_max_retries"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('empty_result_max_retries', v)"
          />
        </div>
      </section>

      <section class="block block--notes">
        <h3 class="block__title font-display">
          <Zap class="block__title-icon" :size="16" aria-hidden="true" />
          调用策略
        </h3>
        <div class="note-grid">
          <div class="note">
            <span class="note__label">并发任务</span>
            <span class="note__value">{{ form.max_concurrent_tasks ?? '—' }} 个</span>
          </div>
          <div class="note">
            <span class="note__label">默认产出</span>
            <span class="note__value">{{ form.default_videos ?? '—' }} 个视频</span>
          </div>
          <div class="note">
            <span class="note__label">空回重试</span>
            <span class="note__value">{{ form.empty_result_max_retries ?? '—' }} 次</span>
          </div>
        </div>
        <p class="block__hint">空回重试会同步到 Imagen 与 NovelAI 的生成配置，保持生成链路一致。</p>
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
      message="当前视频配置有未保存的更改，确定离开吗？"
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
.video-form {
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
.readonly-field {
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
.readonly-field__value {
  align-self: flex-start;
  padding: var(--space-1) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-inset);
  color: var(--text-primary);
  font-size: var(--text-sm);
}
.readonly-field__hint {
  margin: 0;
  color: var(--text-muted);
  font-size: var(--text-xs);
}

.badge {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
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
  .block {
    transition: none;
  }
}
</style>
