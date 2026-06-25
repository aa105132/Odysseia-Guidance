<script setup lang="ts">
/* ImagenView — 绘图设置（Imagen 图片生成引擎）。
 * 接 GET/PUT /api/config/imagen + POST /api/config/test-imagen + POST /api/models/list(imagen)。
 * 字段：基础配置 / 分辨率模型 / SFW·NSFW 内容分级模型矩阵 / 运行参数 / NovelAI 引擎。
 * 保存走 useConfigForm（脏字段子集 + dirty 拦截）；PUT 仅回 {success,updated}，保存后重新 GET 刷新 masked。
 * test-imagen 返回 JSON 连通性结果（非图片），在 BaseModal 内呈现成功/失败。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import {
  AlertCircle,
  CheckCircle2,
  Image as ImageIcon,
  Layers,
  Loader2,
  RefreshCw,
  RotateCcw,
  Save,
  ServerOff,
  Settings,
  Shield,
  Sparkles,
  Zap,
} from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseModal from '@/components/ui/BaseModal.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import ChoiceChip from '@/components/ui/ChoiceChip.vue';
import PasswordInput from '@/components/ui/PasswordInput.vue';
import ModelFetcherSelect from '@/components/shared/ModelFetcherSelect.vue';
import { useConfigForm } from '@/composables/useConfigForm';
import { useToastStore } from '@/stores/toast';
import {
  fetchImagenModels,
  getImagenConfig,
  saveImagenConfig,
  testImagen,
  type ImagenTestResult,
} from '@/api/domains/imagen';
import type { ImagenConfig } from '@/api/models';

const toast = useToastStore();

// 顶栏手动刷新注入：注册当前视图的 force 刷新（重新 load + 重置 dirty）
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');

// api_format 三选一（gemini 原生 / gemini 聊天 / OpenAI 兼容）
const apiFormatOptions = [
  { value: 'gemini', label: 'Gemini 原生' },
  { value: 'gemini_chat', label: 'Gemini 聊天' },
  { value: 'openai', label: 'OpenAI 兼容' },
];

// image_response_format 三选一
const responseFormatOptions = [
  { value: 'auto', label: 'auto' },
  { value: 'base64', label: 'base64' },
  { value: 'url', label: 'url' },
];

// useConfigForm：load 直读；save 先 PUT 再重新 GET（PUT 仅回 {success,updated}）
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
} = useConfigForm<ImagenConfig>({
  load: async () => getImagenConfig(),
  save: async (body) => {
    await saveImagenConfig(body);
    // 保存后重新 GET 刷新 masked 字段 + service_available
    return getImagenConfig();
  },
  successMessage: '绘图配置已保存',
  errorMessage: '绘图配置保存失败',
});

// 表单是否已有数据（区分"加载失败空状态"与"已加载"）
const hasForm = computed(() => Object.keys(form.value).length > 0);

// api_url placeholder 随 format 切换
const apiUrlPlaceholder = computed(() => {
  switch (form.value.api_format) {
    case 'openai':
      return 'https://api.openai.com/v1';
    case 'gemini_chat':
    case 'gemini':
    default:
      return 'https://generativelanguage.googleapis.com/v1beta';
  }
});

// gemini 原生格式不支持流式 → 禁用 streaming_enabled
const streamingDisabled = computed(() => form.value.api_format === 'gemini');

// image_response_format hint 随选项切换
const responseFormatHint = computed(() => {
  switch (form.value.image_response_format) {
    case 'base64':
      return '直接返回 base64 编码图片，体积较大但无需额外下载。';
    case 'url':
      return '返回图片 URL，需后续下载，适合大图。';
    case 'auto':
    default:
      return '由服务端自动选择最合适的响应格式。';
  }
});

// 服务状态徽标
const serviceStatus = computed<{ label: string; tone: 'ok' | 'danger' | 'muted' }>(() => {
  if (!form.value.enabled) return { label: '已停用', tone: 'muted' };
  return form.value.service_available
    ? { label: '服务就绪', tone: 'ok' }
    : { label: '服务未就绪', tone: 'danger' };
});

// aspect_ratios 由后端配置派生（不在 ImagenConfigUpdate 中），只读展示键名
const aspectRatioKeys = computed(() => {
  const ar = form.value.aspect_ratios;
  if (!ar || typeof ar !== 'object') return [];
  return Object.keys(ar);
});

// 数字字段安全写入（空串置 undefined 触发 dirty，NaN 忽略）
function setNum<K extends keyof ImagenConfig>(key: K, raw: string): void {
  if (raw === '') {
    patch(key, undefined as ImagenConfig[K]);
    return;
  }
  const n = Number(raw);
  if (!Number.isNaN(n)) patch(key, n as ImagenConfig[K]);
}

// 模型懒拉取：稳定闭包，调用时读取当前 form 的 api_url/api_key/api_format
async function fetchModelsForView(): Promise<string[]> {
  return fetchImagenModels({
    api_url: form.value.api_url || null,
    api_key: (form.value.api_key as string) || null,
    api_format: form.value.api_format,
  });
}

async function onSave(): Promise<void> {
  await submit();
}

function retry(): void {
  loadForm(true).catch(() => {
    /* 错误已在 loadForm 内 toast */
  });
}

// ===== test-imagen 连通性测试（JSON 结果，非图片）=====
const testModalOpen = ref(false);
const testLoading = ref(false);
const testResult = ref<ImagenTestResult | null>(null);

async function runTest(): Promise<void> {
  if (testLoading.value) return;
  if (!form.value.enabled) {
    toast.push({ type: 'warning', message: '请先启用并保存绘图配置后再测试连接' });
    return;
  }
  testLoading.value = true;
  testResult.value = null;
  testModalOpen.value = true;
  try {
    testResult.value = await testImagen();
    if (testResult.value.success) {
      toast.push({ type: 'success', message: 'Imagen 连接测试成功' });
    } else {
      toast.push({ type: 'error', message: testResult.value.error ?? '连接测试失败' });
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : '测试请求失败';
    testResult.value = { success: false, error: msg };
    toast.push({ type: 'error', message: msg });
  } finally {
    testLoading.value = false;
  }
}

function closeTestModal(): void {
  testModalOpen.value = false;
}

// ===== 路由离开拦截：dirty 时弹确认（beforeunload 由 useConfigForm 处理）=====
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
  // 注册顶栏手动刷新 = 强制重新 load + 重置 dirty
  registerRefresh?.(() => loadForm(true));
});
</script>

<template>
  <div class="view">
    <BaseSectionTitle
      :icon="ImageIcon"
      title="绘图设置"
      subtitle="Imagen 图片生成引擎 · SFW/NSFW 模型矩阵"
    />

    <!-- 加载骨架 -->
    <div v-if="loading" class="config-card" aria-busy="true" aria-live="polite">
      <BaseSkeleton width="40%" height="1.5rem" rounded="var(--radius-md)" />
      <div class="skeleton-grid">
        <BaseSkeleton v-for="i in 6" :key="i" height="2.5rem" rounded="var(--radius-md)" />
      </div>
      <BaseSkeleton width="70%" height="1rem" rounded="var(--radius-sm)" />
    </div>

    <!-- 加载失败空状态 -->
    <BaseEmpty
      v-else-if="error && !hasForm"
      :icon="ServerOff"
      title="绘图配置加载失败"
      :description="error"
      action-text="重新加载"
      :action-icon="RefreshCw"
      @action="retry"
    />

    <!-- 表单主体 -->
    <template v-else>
      <!-- 错误横幅（已加载后的保存/运行错误） -->
      <div v-if="error" class="error-banner" role="alert">
        <div class="error-banner__text">
          <ServerOff :size="18" aria-hidden="true" />
          <span>{{ error }}</span>
        </div>
        <BaseButton variant="ghost" size="sm" :icon="RefreshCw" @click="retry">重试</BaseButton>
      </div>

      <!-- 操作栏：dirty 指示 + 重置 + 保存 -->
      <div class="action-bar" :class="{ 'is-dirty': dirty }">
        <span class="action-bar__state" :class="dirty ? 'is-dirty' : 'is-clean'">
          {{ dirty ? '有未保存更改' : '所有更改已保存' }}
        </span>
        <div class="action-bar__btns">
          <BaseButton
            variant="ghost"
            size="md"
            :icon="RotateCcw"
            :disabled="!dirty || saving"
            @click="reset"
          >
            重置
          </BaseButton>
          <BaseButton
            variant="primary"
            size="md"
            :icon="Save"
            :loading="saving"
            :disabled="!dirty"
            @click="onSave"
          >
            保存配置
          </BaseButton>
        </div>
      </div>

      <!-- ===== 基础配置 ===== -->
      <section class="config-card">
        <BaseSectionTitle :icon="Sparkles" title="基础配置" subtitle="引擎开关与 API 接入" />

        <div class="field-row field-row--inline">
          <div class="enable-cell">
            <ToggleSwitch
              :model-value="!!form.enabled"
              :disabled="saving"
              label="启用 Imagen 绘图"
              @update:model-value="patch('enabled', $event)"
            />
          </div>
          <span class="badge" :class="`is-${serviceStatus.tone}`">{{ serviceStatus.label }}</span>
        </div>

        <div class="field-row">
          <div class="field-cell">
            <label class="field-cell__label font-display">API 格式</label>
            <ChoiceChip
              :model-value="form.api_format ?? 'gemini'"
              :options="apiFormatOptions"
              @update:model-value="patch('api_format', String($event))"
            />
          </div>
        </div>

        <div class="field-grid field-grid--2">
          <BaseInput
            :model-value="form.api_url ?? ''"
            label="API 地址"
            type="text"
            :placeholder="apiUrlPlaceholder"
            :error="fieldErrors['api_url']"
            hint="留空则使用默认端点"
            :disabled="saving"
            @update:model-value="patch('api_url', $event)"
          />
          <PasswordInput
            :model-value="(form.api_key as string) ?? ''"
            label="API Key"
            :placeholder="form.api_key_masked || '未配置'"
            :error="fieldErrors['api_key']"
            :disabled="saving"
            @update:model-value="patch('api_key', $event)"
          >
          </PasswordInput>
        </div>
        <div class="badge-row">
          <span class="badge" :class="form.has_api_key ? 'is-ok' : 'is-warn'">
            {{ form.has_api_key ? 'API Key 已配置' : 'API Key 未配置' }}
          </span>
        </div>

        <div class="field-grid field-grid--2">
          <ModelFetcherSelect
            :model-value="form.model ?? ''"
            :fetch-models="fetchModelsForView"
            label="文生图模型"
            hint="留空使用默认模型；可点击加载远端可用模型列表"
            :disabled="saving"
            :empty-description="'未获取到 imagen 可用模型，请检查 API 配置后重试。'"
            @update:model-value="patch('model', $event)"
          />
          <BaseInput
            :model-value="form.edit_model ?? ''"
            label="图生图模型"
            type="text"
            placeholder="如 imagen-3.0-generate-002"
            :error="fieldErrors['edit_model']"
            :disabled="saving"
            @update:model-value="patch('edit_model', $event)"
          />
        </div>

        <div class="field-grid field-grid--4">
          <BaseInput
            :model-value="form.default_images ?? ''"
            label="默认生成数量"
            type="number"
            placeholder="1"
            :error="fieldErrors['default_images']"
            hint="1-4"
            :disabled="saving"
            @update:model-value="setNum('default_images', $event)"
          />
          <BaseInput
            :model-value="form.max_images ?? ''"
            label="单次最大图片数"
            type="number"
            placeholder="20"
            :error="fieldErrors['max_images']"
            hint="1-50"
            :disabled="saving"
            @update:model-value="setNum('max_images', $event)"
          />
          <BaseInput
            :model-value="form.generation_cost ?? ''"
            label="文生图成本（灵石）"
            type="number"
            placeholder="1"
            :error="fieldErrors['generation_cost']"
            :disabled="saving"
            @update:model-value="setNum('generation_cost', $event)"
          />
          <BaseInput
            :model-value="form.edit_cost ?? ''"
            label="图生图成本（灵石）"
            type="number"
            placeholder="1"
            :error="fieldErrors['edit_cost']"
            :disabled="saving"
            @update:model-value="setNum('edit_cost', $event)"
          />
        </div>

        <div class="field-row">
          <div class="field-cell">
            <label class="field-cell__label font-display">图片响应格式</label>
            <ChoiceChip
              :model-value="form.image_response_format ?? 'auto'"
              :options="responseFormatOptions"
              @update:model-value="patch('image_response_format', String($event))"
            />
            <p class="field-cell__hint">{{ responseFormatHint }}</p>
          </div>
        </div>

        <div class="field-row field-row--inline">
          <ToggleSwitch
            :model-value="!!form.streaming_enabled"
            :disabled="streamingDisabled || saving"
            label="启用流式请求"
            @update:model-value="patch('streaming_enabled', $event)"
          />
          <span v-if="streamingDisabled" class="warn-hint">Gemini 原生格式不支持流式</span>
        </div>

        <div class="test-row">
          <BaseButton
            variant="secondary"
            size="md"
            :icon="Zap"
            :loading="testLoading"
            :disabled="saving || !form.enabled"
            @click="runTest"
          >
            测试连接
          </BaseButton>
          <span class="test-row__hint">需先启用并保存配置；后端用固定提示词生成一张图校验连通性</span>
        </div>
      </section>

      <!-- ===== 分辨率模型 ===== -->
      <section class="config-card">
        <BaseSectionTitle :icon="Layers" title="分辨率模型" subtitle="2K / 4K 文生图与图生图模型覆盖" />
        <div class="field-grid field-grid--2">
          <BaseInput
            :model-value="form.model_2k ?? ''"
            label="2K 文生图模型"
            type="text"
            :error="fieldErrors['model_2k']"
            :disabled="saving"
            @update:model-value="patch('model_2k', $event)"
          />
          <BaseInput
            :model-value="form.model_4k ?? ''"
            label="4K 文生图模型"
            type="text"
            :error="fieldErrors['model_4k']"
            :disabled="saving"
            @update:model-value="patch('model_4k', $event)"
          />
          <BaseInput
            :model-value="form.edit_model_2k ?? ''"
            label="2K 图生图模型"
            type="text"
            :error="fieldErrors['edit_model_2k']"
            :disabled="saving"
            @update:model-value="patch('edit_model_2k', $event)"
          />
          <BaseInput
            :model-value="form.edit_model_4k ?? ''"
            label="4K 图生图模型"
            type="text"
            :error="fieldErrors['edit_model_4k']"
            :disabled="saving"
            @update:model-value="patch('edit_model_4k', $event)"
          />
        </div>
      </section>

      <!-- ===== SFW 内容分级模型矩阵（2 行 × 3 列）===== -->
      <section class="config-card">
        <BaseSectionTitle
          :icon="Shield"
          title="SFW 内容分级模型"
          subtitle="文生图 / 图生图 × 默认 / 2K / 4K"
        />
        <div class="matrix-grid">
          <BaseInput
            :model-value="form.sfw_model ?? ''"
            label="默认 · 文生图"
            type="text"
            :error="fieldErrors['sfw_model']"
            :disabled="saving"
            @update:model-value="patch('sfw_model', $event)"
          />
          <BaseInput
            :model-value="form.sfw_model_2k ?? ''"
            label="2K · 文生图"
            type="text"
            :error="fieldErrors['sfw_model_2k']"
            :disabled="saving"
            @update:model-value="patch('sfw_model_2k', $event)"
          />
          <BaseInput
            :model-value="form.sfw_model_4k ?? ''"
            label="4K · 文生图"
            type="text"
            :error="fieldErrors['sfw_model_4k']"
            :disabled="saving"
            @update:model-value="patch('sfw_model_4k', $event)"
          />
          <BaseInput
            :model-value="form.sfw_edit_model ?? ''"
            label="默认 · 图生图"
            type="text"
            :error="fieldErrors['sfw_edit_model']"
            :disabled="saving"
            @update:model-value="patch('sfw_edit_model', $event)"
          />
          <BaseInput
            :model-value="form.sfw_edit_model_2k ?? ''"
            label="2K · 图生图"
            type="text"
            :error="fieldErrors['sfw_edit_model_2k']"
            :disabled="saving"
            @update:model-value="patch('sfw_edit_model_2k', $event)"
          />
          <BaseInput
            :model-value="form.sfw_edit_model_4k ?? ''"
            label="4K · 图生图"
            type="text"
            :error="fieldErrors['sfw_edit_model_4k']"
            :disabled="saving"
            @update:model-value="patch('sfw_edit_model_4k', $event)"
          />
        </div>
      </section>

      <!-- ===== NSFW 内容分级模型矩阵（2 行 × 3 列）===== -->
      <section class="config-card">
        <BaseSectionTitle
          :icon="Shield"
          title="NSFW 内容分级模型"
          subtitle="文生图 / 图生图 × 默认 / 2K / 4K"
        />
        <div class="matrix-grid">
          <BaseInput
            :model-value="form.nsfw_model ?? ''"
            label="默认 · 文生图"
            type="text"
            :error="fieldErrors['nsfw_model']"
            :disabled="saving"
            @update:model-value="patch('nsfw_model', $event)"
          />
          <BaseInput
            :model-value="form.nsfw_model_2k ?? ''"
            label="2K · 文生图"
            type="text"
            :error="fieldErrors['nsfw_model_2k']"
            :disabled="saving"
            @update:model-value="patch('nsfw_model_2k', $event)"
          />
          <BaseInput
            :model-value="form.nsfw_model_4k ?? ''"
            label="4K · 文生图"
            type="text"
            :error="fieldErrors['nsfw_model_4k']"
            :disabled="saving"
            @update:model-value="patch('nsfw_model_4k', $event)"
          />
          <BaseInput
            :model-value="form.nsfw_edit_model ?? ''"
            label="默认 · 图生图"
            type="text"
            :error="fieldErrors['nsfw_edit_model']"
            :disabled="saving"
            @update:model-value="patch('nsfw_edit_model', $event)"
          />
          <BaseInput
            :model-value="form.nsfw_edit_model_2k ?? ''"
            label="2K · 图生图"
            type="text"
            :error="fieldErrors['nsfw_edit_model_2k']"
            :disabled="saving"
            @update:model-value="patch('nsfw_edit_model_2k', $event)"
          />
          <BaseInput
            :model-value="form.nsfw_edit_model_4k ?? ''"
            label="4K · 图生图"
            type="text"
            :error="fieldErrors['nsfw_edit_model_4k']"
            :disabled="saving"
            @update:model-value="patch('nsfw_edit_model_4k', $event)"
          />
        </div>
      </section>

      <!-- ===== 运行参数 ===== -->
      <section class="config-card">
        <BaseSectionTitle :icon="Settings" title="运行参数" subtitle="超时与重试（后端兜底 clamp）" />
        <div class="field-grid field-grid--auto">
          <BaseInput
            :model-value="form.empty_result_max_retries ?? ''"
            label="空结果最大重试"
            type="number"
            placeholder="3"
            :error="fieldErrors['empty_result_max_retries']"
            hint="1-10"
            :disabled="saving"
            @update:model-value="setNum('empty_result_max_retries', $event)"
          />
          <BaseInput
            :model-value="form.request_timeout ?? ''"
            label="请求超时（秒）"
            type="number"
            placeholder="120"
            :error="fieldErrors['request_timeout']"
            hint="10-600"
            :disabled="saving"
            @update:model-value="setNum('request_timeout', $event)"
          />
          <BaseInput
            :model-value="form.streaming_timeout ?? ''"
            label="流式超时（秒）"
            type="number"
            placeholder="180"
            :error="fieldErrors['streaming_timeout']"
            hint="10-600"
            :disabled="saving"
            @update:model-value="setNum('streaming_timeout', $event)"
          />
          <BaseInput
            :model-value="form.connect_timeout ?? ''"
            label="连接超时（秒）"
            type="number"
            placeholder="15"
            :error="fieldErrors['connect_timeout']"
            hint="5-60"
            :disabled="saving"
            @update:model-value="setNum('connect_timeout', $event)"
          />
          <BaseInput
            :model-value="form.transient_max_retries ?? ''"
            label="瞬态错误重试"
            type="number"
            placeholder="2"
            :error="fieldErrors['transient_max_retries']"
            hint="0-10"
            :disabled="saving"
            @update:model-value="setNum('transient_max_retries', $event)"
          />
        </div>
      </section>

      <!-- ===== NovelAI 引擎 ===== -->
      <section class="config-card">
        <BaseSectionTitle :icon="Sparkles" title="NovelAI 引擎" subtitle="作为 Imagen 的备用绘图引擎" />
        <ToggleSwitch
          :model-value="!!form.novelai_enabled"
          :disabled="saving"
          label="启用 NovelAI 绘图引擎"
          @update:model-value="patch('novelai_enabled', $event)"
        />
      </section>

      <!-- ===== 宽高比（只读，后端配置派生）===== -->
      <section v-if="aspectRatioKeys.length" class="config-card">
        <BaseSectionTitle title="支持宽高比" subtitle="由后端配置派生，不可在此编辑" />
        <div class="chip-readonly">
          <span v-for="key in aspectRatioKeys" :key="key" class="chip-readonly__item">{{ key }}</span>
        </div>
      </section>
    </template>

    <!-- ===== test-imagen 结果 Modal（JSON 连通性结果，非图片）===== -->
    <BaseModal
      :model-value="testModalOpen"
      title="Imagen 连接测试"
      size="sm"
      @update:model-value="(v: boolean) => (testModalOpen = v)"
    >
      <div class="test-modal">
        <!-- 测试中 -->
        <div v-if="testLoading" class="test-modal__loading" aria-live="polite">
          <Loader2 class="test-modal__spinner" aria-hidden="true" />
          <span>正在测试连接，后端生成一张校验图…</span>
        </div>

        <!-- 成功 -->
        <div v-else-if="testResult?.success" class="test-modal__result is-ok" role="status">
          <CheckCircle2 class="test-modal__icon" aria-hidden="true" />
          <div class="test-modal__text">
            <p class="test-modal__title">连接测试成功</p>
            <p class="test-modal__desc">{{ testResult.message ?? 'Imagen API 可达且可生成图像。' }}</p>
          </div>
        </div>

        <!-- 失败 -->
        <div v-else-if="testResult && !testResult.success" class="test-modal__result is-error" role="alert">
          <AlertCircle class="test-modal__icon" aria-hidden="true" />
          <div class="test-modal__text">
            <p class="test-modal__title">连接测试失败</p>
            <p class="test-modal__desc">{{ testResult.error ?? testResult.message ?? '未知错误' }}</p>
          </div>
        </div>
      </div>

      <template #footer>
        <BaseButton variant="ghost" size="md" :disabled="testLoading" @click="closeTestModal">
          关闭
        </BaseButton>
        <BaseButton
          variant="secondary"
          size="md"
          :icon="RefreshCw"
          :loading="testLoading"
          :disabled="testLoading"
          @click="runTest"
        >
          重新测试
        </BaseButton>
      </template>
    </BaseModal>

    <!-- 路由离开确认 -->
    <BaseConfirmDialog
      v-model="leaveConfirm"
      title="放弃未保存的更改？"
      message="当前绘图配置有未保存的更改，离开将丢弃这些更改。"
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

/* ===== 操作栏 ===== */
.action-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.action-bar.is-dirty {
  border-color: color-mix(in oklch, var(--accent) 45%, transparent);
}
.action-bar__state {
  font-size: var(--text-sm);
  color: var(--text-muted);
}
.action-bar__state.is-dirty { color: var(--accent); font-weight: var(--fw-medium); }
.action-bar__state.is-clean { color: var(--text-muted); }
.action-bar__btns {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

/* ===== 配置卡片 ===== */
.config-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}

/* 骨架卡片 */
.skeleton-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-3);
}

/* ===== 字段布局 ===== */
.field-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.field-row--inline {
  flex-direction: row;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
}
.field-cell {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.field-cell__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.field-cell__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.field-grid {
  display: grid;
  gap: var(--space-3);
}
.field-grid--2 { grid-template-columns: repeat(2, 1fr); }
.field-grid--4 { grid-template-columns: repeat(4, 1fr); }
.field-grid--auto { grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr)); }

/* 矩阵网格：2 行 × 3 列（文生图行 / 图生图行 × 默认·2K·4K） */
.matrix-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3);
}

/* ===== 徽标 ===== */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 0 var(--space-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: var(--text-muted);
  white-space: nowrap;
}
.badge.is-ok {
  color: var(--success);
  border-color: color-mix(in oklch, var(--success) 45%, transparent);
  background: color-mix(in oklch, var(--success) 10%, transparent);
}
.badge.is-warn {
  color: var(--warning);
  border-color: color-mix(in oklch, var(--warning) 45%, transparent);
  background: color-mix(in oklch, var(--warning) 10%, transparent);
}
.badge.is-danger {
  color: var(--danger);
  border-color: color-mix(in oklch, var(--danger) 45%, transparent);
  background: color-mix(in oklch, var(--danger) 10%, transparent);
}
.badge.is-muted { color: var(--text-muted); }
.badge-row { display: flex; gap: var(--space-2); margin-top: calc(var(--space-3) * -1); }

.warn-hint {
  font-size: var(--text-xs);
  color: var(--warning);
}

/* ===== 测试行 ===== */
.test-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border);
}
.test-row__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

/* ===== 只读宽高比芯片 ===== */
.chip-readonly {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.chip-readonly__item {
  padding: var(--space-1) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-inset);
  color: var(--text-secondary);
  font-size: var(--text-sm);
}

/* ===== 测试 Modal ===== */
.test-modal { display: flex; flex-direction: column; gap: var(--space-3); }
.test-modal__loading {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  color: var(--text-secondary);
  font-size: var(--text-sm);
}
.test-modal__spinner {
  width: 1.25rem;
  height: 1.25rem;
  color: var(--accent);
  animation: test-spin 0.8s linear infinite;
}
@keyframes test-spin { to { transform: rotate(360deg); } }

.test-modal__result {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
}
.test-modal__result.is-ok {
  background: color-mix(in oklch, var(--success) 10%, transparent);
  border: 1px solid color-mix(in oklch, var(--success) 40%, transparent);
}
.test-modal__result.is-error {
  background: color-mix(in oklch, var(--danger) 10%, transparent);
  border: 1px solid color-mix(in oklch, var(--danger) 40%, transparent);
}
.test-modal__icon {
  flex: none;
  width: 1.5rem;
  height: 1.5rem;
}
.test-modal__result.is-ok .test-modal__icon { color: var(--success); }
.test-modal__result.is-error .test-modal__icon { color: var(--danger); }
.test-modal__text { display: flex; flex-direction: column; gap: var(--space-1); }
.test-modal__title {
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}
.test-modal__desc {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: var(--lh-relaxed);
  word-break: break-word;
}

/* ===== 移动端堆叠 ===== */
@media (max-width: 768px) {
  .field-grid--2,
  .field-grid--3,
  .matrix-grid,
  .skeleton-grid {
    grid-template-columns: 1fr;
  }
  .field-grid--auto { grid-template-columns: repeat(2, 1fr); }
  .action-bar { flex-direction: column; align-items: stretch; }
  .action-bar__btns { justify-content: flex-end; }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .test-modal__spinner { animation: none; }
  .action-bar { transition: none; }
}
</style>
