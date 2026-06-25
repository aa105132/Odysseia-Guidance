<script setup lang="ts">
/* NovelAIView — NovelAI 设置。
 * 接 GET/PUT /api/config/novelai + POST /api/config/test-novelai + admin/user 预设端点。
 * 字段：基础配置（开关/API Token/模型/成本）、生成参数（尺寸/步数/缩放/采样器/质量/UC/CFG/噪声/SMEA）、
 * 提示词与画师串、重试参数、提示词模型路由（专用 LLM）。
 * 保存走 useConfigForm（脏字段子集 + dirty 拦截）；PUT 仅回 {success,updated}，保存后重新 GET 刷新 masked。
 * test-novelai 返回连通性 + 订阅等级/Anlas，在 BaseModal 呈现。
 * 预设管理嵌入 AdminPresetsPanel（CRUD）与 UserPresetsPanel（只读+删除）。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Palette,
  RefreshCw,
  RotateCcw,
  Save,
  ServerOff,
  Settings,
  Sparkles,
  Zap,
} from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseSelect from '@/components/ui/BaseSelect.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseModal from '@/components/ui/BaseModal.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import PasswordInput from '@/components/ui/PasswordInput.vue';
import AdminPresetsPanel from '@/components/novelai/AdminPresetsPanel.vue';
import UserPresetsPanel from '@/components/novelai/UserPresetsPanel.vue';
import { useConfigForm } from '@/composables/useConfigForm';
import { useToastStore } from '@/stores/toast';
import { getNovelAIConfig, saveNovelAIConfig, testNovelAI } from '@/api/domains/novelai';
import type { NovelAIConfig, NovelAITestResponse } from '@/api/models';

const toast = useToastStore();

// 顶栏手动刷新注入：注册当前视图的 force 刷新（重新 load + 重置 dirty）
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');

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
} = useConfigForm<NovelAIConfig>({
  load: async () => getNovelAIConfig(),
  save: async (body) => {
    await saveNovelAIConfig(body);
    // 保存后重新 GET 刷新 masked 字段 + service_available
    return getNovelAIConfig();
  },
  successMessage: 'NovelAI 配置已保存',
  errorMessage: 'NovelAI 配置保存失败',
});

const hasForm = computed(() => Object.keys(form.value).length > 0);

// 下拉选项由 GET 返回的 available_* 派生
const modelOptions = computed(() =>
  (form.value.available_models ?? []).map((m) => ({ value: m, label: m })),
);
const samplerOptions = computed(() =>
  (form.value.available_samplers ?? []).map((s) => ({ value: s, label: s })),
);
const noiseScheduleOptions = computed(() =>
  (form.value.available_noise_schedules ?? []).map((n) => ({ value: n, label: n })),
);

// 服务状态徽标
const serviceStatus = computed<{ label: string; tone: 'ok' | 'danger' | 'muted' }>(() => {
  if (!form.value.enabled) return { label: '已停用', tone: 'muted' };
  return form.value.service_available
    ? { label: '服务就绪', tone: 'ok' }
    : { label: '服务未就绪', tone: 'danger' };
});

// 数字字段安全写入（空串置 undefined 触发 dirty，NaN 忽略）
function setNum<K extends keyof NovelAIConfig>(key: K, raw: string): void {
  if (raw === '') {
    patch(key, undefined as NovelAIConfig[K]);
    return;
  }
  const n = Number(raw);
  if (!Number.isNaN(n)) patch(key, n as NovelAIConfig[K]);
}

async function onSave(): Promise<void> {
  await submit();
}

function retry(): void {
  loadForm(true).catch(() => {
    /* 错误已在 loadForm 内 toast */
  });
}

// ===== test-novelai 连通性测试 =====
const testModalOpen = ref(false);
const testLoading = ref(false);
const testResult = ref<NovelAITestResponse | null>(null);

async function runTest(): Promise<void> {
  if (testLoading.value) return;
  // 测试需用已保存配置（后端用持久化 token 调 NovelAI），未保存的脏字段不会生效
  if (dirty.value) {
    toast.push({ type: 'warning', message: '请先保存配置后再测试连接', duration: 3500 });
    return;
  }
  if (!form.value.enabled) {
    toast.push({ type: 'warning', message: '请先启用 NovelAI 并保存配置后再测试' });
    return;
  }
  testLoading.value = true;
  testResult.value = null;
  testModalOpen.value = true;
  try {
    testResult.value = await testNovelAI();
    if (testResult.value.success) {
      toast.push({ type: 'success', message: 'NovelAI 连接测试成功' });
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
      :icon="Palette"
      title="NovelAI 设置"
      subtitle="NovelAI 图像生成引擎 · 画师串预设管理"
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
      title="NovelAI 配置加载失败"
      :description="error"
      action-text="重新加载"
      :action-icon="RefreshCw"
      @action="retry"
    />

    <!-- 表单主体 -->
    <template v-else>
      <!-- 错误横幅 -->
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
          <BaseButton variant="ghost" size="md" :icon="RotateCcw" :disabled="!dirty || saving" @click="reset">
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
        <BaseSectionTitle :icon="Sparkles" title="基础配置" subtitle="引擎开关与 API Token" />

        <div class="field-row field-row--inline">
          <div class="enable-cell">
            <ToggleSwitch
              :model-value="!!form.enabled"
              :disabled="saving"
              label="启用 NovelAI 绘图"
              @update:model-value="patch('enabled', $event)"
            />
          </div>
          <span class="badge" :class="`is-${serviceStatus.tone}`">{{ serviceStatus.label }}</span>
        </div>

        <div class="field-grid field-grid--2">
          <PasswordInput
            :model-value="(form.api_token as string) ?? ''"
            label="API Token"
            :placeholder="form.api_token_masked || '未配置'"
            :error="fieldErrors['api_token']"
            hint="NovelAI 账户 API Token，留空保持原值"
            :disabled="saving"
            @update:model-value="patch('api_token', $event)"
          />
          <BaseSelect
            :model-value="form.model ?? ''"
            :options="modelOptions"
            label="生成模型"
            placeholder="选择模型"
            :error="fieldErrors['model']"
            :disabled="saving"
            @update:model-value="patch('model', String($event))"
          />
        </div>
        <div class="badge-row">
          <span class="badge" :class="form.has_api_token ? 'is-ok' : 'is-warn'">
            {{ form.has_api_token ? 'API Token 已配置' : 'API Token 未配置' }}
          </span>
        </div>

        <div class="field-grid field-grid--2">
          <BaseInput
            :model-value="form.generation_cost ?? ''"
            label="生成成本（灵石）"
            type="number"
            placeholder="5"
            :error="fieldErrors['generation_cost']"
            hint="≥0"
            :disabled="saving"
            @update:model-value="setNum('generation_cost', $event)"
          />
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
          <span class="test-row__hint">需先启用并保存配置；后端校验连通性并返回订阅等级与 Anlas 余额</span>
        </div>
      </section>

      <!-- ===== 生成参数 ===== -->
      <section class="config-card">
        <BaseSectionTitle :icon="Settings" title="生成参数" subtitle="尺寸 / 步数 / 采样器 / 噪声调度" />

        <div class="field-grid field-grid--4">
          <BaseInput
            :model-value="form.default_width ?? ''"
            label="默认宽度"
            type="number"
            placeholder="832"
            :error="fieldErrors['default_width']"
            :disabled="saving"
            @update:model-value="setNum('default_width', $event)"
          />
          <BaseInput
            :model-value="form.default_height ?? ''"
            label="默认高度"
            type="number"
            placeholder="1216"
            :error="fieldErrors['default_height']"
            :disabled="saving"
            @update:model-value="setNum('default_height', $event)"
          />
          <BaseInput
            :model-value="form.default_steps ?? ''"
            label="默认步数"
            type="number"
            placeholder="28"
            :error="fieldErrors['default_steps']"
            hint="1-50"
            :disabled="saving"
            @update:model-value="setNum('default_steps', $event)"
          />
          <BaseInput
            :model-value="form.default_scale ?? ''"
            label="引导缩放 (scale)"
            type="number"
            placeholder="5"
            :error="fieldErrors['default_scale']"
            :disabled="saving"
            @update:model-value="setNum('default_scale', $event)"
          />
        </div>

        <div class="field-grid field-grid--3">
          <BaseSelect
            :model-value="form.default_sampler ?? ''"
            :options="samplerOptions"
            label="采样器"
            placeholder="选择采样器"
            :error="fieldErrors['default_sampler']"
            :disabled="saving"
            @update:model-value="patch('default_sampler', String($event))"
          />
          <BaseSelect
            :model-value="form.noise_schedule ?? ''"
            :options="noiseScheduleOptions"
            label="噪声调度"
            placeholder="选择噪声调度"
            :error="fieldErrors['noise_schedule']"
            :disabled="saving"
            @update:model-value="patch('noise_schedule', String($event))"
          />
          <BaseInput
            :model-value="form.uc_preset ?? ''"
            label="UC 预设"
            type="number"
            placeholder="0"
            :error="fieldErrors['uc_preset']"
            hint="0-3"
            :disabled="saving"
            @update:model-value="setNum('uc_preset', $event)"
          />
        </div>

        <div class="field-grid field-grid--3">
          <BaseInput
            :model-value="form.cfg_rescale ?? ''"
            label="CFG Rescale"
            type="number"
            placeholder="0"
            :error="fieldErrors['cfg_rescale']"
            :disabled="saving"
            @update:model-value="setNum('cfg_rescale', $event)"
          />
          <div class="toggle-cell">
            <ToggleSwitch
              :model-value="!!form.quality_toggle"
              :disabled="saving"
              label="Quality Toggle"
              @update:model-value="patch('quality_toggle', $event)"
            />
          </div>
          <div class="toggle-cell">
            <ToggleSwitch
              :model-value="!!form.smea"
              :disabled="saving"
              label="SMEA"
              @update:model-value="patch('smea', $event)"
            />
          </div>
        </div>

        <div class="field-row field-row--inline">
          <ToggleSwitch
            :model-value="!!form.smea_dyn"
            :disabled="saving"
            label="SMEA Dyn（需先开 SMEA）"
            @update:model-value="patch('smea_dyn', $event)"
          />
        </div>
      </section>

      <!-- ===== 提示词与画师串 ===== -->
      <section class="config-card">
        <BaseSectionTitle title="提示词与画师串" subtitle="默认负面提示词与画师串模板" />
        <div class="field">
          <label class="field__label font-display">默认画师串</label>
          <textarea
            :value="form.default_artist_string ?? ''"
            class="field__textarea"
            rows="3"
            placeholder="artist:xxx, ..."
            :disabled="saving"
            @input="patch('default_artist_string', ($event.target as HTMLTextAreaElement).value)"
          />
          <p v-if="fieldErrors['default_artist_string']" class="field__error" role="alert">{{ fieldErrors['default_artist_string'] }}</p>
          <p v-else class="field__hint">追加到生成请求的正面提示词，可作为全局画师风格</p>
        </div>
        <div class="field">
          <label class="field__label font-display">默认负面提示词</label>
          <textarea
            :value="form.default_negative_prompt ?? ''"
            class="field__textarea"
            rows="4"
            placeholder="lowres, bad anatomy, ..."
            :disabled="saving"
            @input="patch('default_negative_prompt', ($event.target as HTMLTextAreaElement).value)"
          />
          <p v-if="fieldErrors['default_negative_prompt']" class="field__error" role="alert">{{ fieldErrors['default_negative_prompt'] }}</p>
          <p v-else class="field__hint">未单独指定负面时使用的全局负面提示词</p>
        </div>
      </section>

      <!-- ===== 重试参数 ===== -->
      <section class="config-card">
        <BaseSectionTitle :icon="RefreshCw" title="重试参数" subtitle="请求失败与空回重试次数" />
        <div class="field-grid field-grid--2">
          <BaseInput
            :model-value="form.max_retries ?? ''"
            label="最大重试次数"
            type="number"
            placeholder="3"
            :error="fieldErrors['max_retries']"
            hint="0-10"
            :disabled="saving"
            @update:model-value="setNum('max_retries', $event)"
          />
          <BaseInput
            :model-value="form.empty_result_max_retries ?? ''"
            label="空回最大重试"
            type="number"
            placeholder="3"
            :error="fieldErrors['empty_result_max_retries']"
            hint="0-10（全局，同时影响 Imagen/视频）"
            :disabled="saving"
            @update:model-value="setNum('empty_result_max_retries', $event)"
          />
        </div>
      </section>

      <!-- ===== 提示词模型路由 ===== -->
      <section class="config-card">
        <BaseSectionTitle :icon="Sparkles" title="提示词模型路由" subtitle="AI 描述/重写专用的 LLM 接入" />
        <div class="field-grid field-grid--2">
          <BaseInput
            :model-value="form.prompt_model ?? ''"
            label="提示词模型"
            type="text"
            placeholder="如 gemini-2.5-flash"
            :error="fieldErrors['prompt_model']"
            :hint="`留空则回退全局默认模型（当前：${form.effective_prompt_model ?? '全局默认'}）`"
            :disabled="saving"
            @update:model-value="patch('prompt_model', $event)"
          />
          <BaseInput
            :model-value="form.prompt_api_url ?? ''"
            label="提示词 API 地址"
            type="text"
            placeholder="留空使用默认端点"
            :error="fieldErrors['prompt_api_url']"
            :disabled="saving"
            @update:model-value="patch('prompt_api_url', $event)"
          />
        </div>
        <PasswordInput
          :model-value="(form.prompt_api_key as string) ?? ''"
          label="提示词 API Key"
          :placeholder="form.prompt_api_key_masked || '未配置'"
          :error="fieldErrors['prompt_api_key']"
          hint="提示词生成专用密钥，留空保持原值"
          :disabled="saving"
          @update:model-value="patch('prompt_api_key', $event)"
        />
        <div class="badge-row">
          <span class="badge" :class="form.has_prompt_api_key ? 'is-ok' : 'is-warn'">
            {{ form.has_prompt_api_key ? '提示词 Key 已配置' : '提示词 Key 未配置' }}
          </span>
        </div>
        <div class="field-row field-row--inline">
          <ToggleSwitch
            :model-value="!!form.use_prompt_model_in_chat_tool"
            :disabled="saving"
            label="对话工具启用提示词模型"
            @update:model-value="patch('use_prompt_model_in_chat_tool', $event)"
          />
        </div>
      </section>

      <!-- ===== 预设管理 ===== -->
      <AdminPresetsPanel />
      <UserPresetsPanel />
    </template>

    <!-- ===== test-novelai 结果 Modal ===== -->
    <BaseModal
      :model-value="testModalOpen"
      title="NovelAI 连接测试"
      size="sm"
      @update:model-value="(v: boolean) => (testModalOpen = v)"
    >
      <div class="test-modal">
        <div v-if="testLoading" class="test-modal__loading" aria-live="polite">
          <Loader2 class="test-modal__spinner" aria-hidden="true" />
          <span>正在测试连接，后端校验 NovelAI API…</span>
        </div>

        <div v-else-if="testResult?.success" class="test-modal__result is-ok" role="status">
          <CheckCircle2 class="test-modal__icon" aria-hidden="true" />
          <div class="test-modal__text">
            <p class="test-modal__title">连接测试成功</p>
            <p class="test-modal__desc">{{ testResult.message ?? 'NovelAI API 可达。' }}</p>
          </div>
        </div>

        <div v-else-if="testResult && !testResult.success" class="test-modal__result is-error" role="alert">
          <AlertCircle class="test-modal__icon" aria-hidden="true" />
          <div class="test-modal__text">
            <p class="test-modal__title">连接测试失败</p>
            <p class="test-modal__desc">{{ testResult.error ?? testResult.message ?? '未知错误' }}</p>
          </div>
        </div>
      </div>

      <template #footer>
        <BaseButton variant="ghost" size="md" :disabled="testLoading" @click="closeTestModal">关闭</BaseButton>
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
      message="当前 NovelAI 配置有未保存的更改，离开将丢弃这些更改。"
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
.action-bar.is-dirty { border-color: color-mix(in oklch, var(--accent) 45%, transparent); }
.action-bar__state { font-size: var(--text-sm); color: var(--text-muted); }
.action-bar__state.is-dirty { color: var(--accent); font-weight: var(--fw-medium); }
.action-bar__state.is-clean { color: var(--text-muted); }
.action-bar__btns { display: flex; align-items: center; gap: var(--space-2); }

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
.enable-cell { display: inline-flex; }
.toggle-cell {
  display: flex;
  align-items: center;
  min-height: 2.5rem;
}

.field-grid { display: grid; gap: var(--space-3); }
.field-grid--2 { grid-template-columns: repeat(2, 1fr); }
.field-grid--3 { grid-template-columns: repeat(3, 1fr); }
.field-grid--4 { grid-template-columns: repeat(4, 1fr); }

/* textarea 字段（复用 BaseInput 字段样式）*/
.field { display: flex; flex-direction: column; gap: var(--space-2); }
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
  line-height: var(--lh-relaxed);
  resize: vertical;
  outline: none;
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.field__textarea:hover { border-color: var(--border-strong); }
.field__textarea:focus-visible { border-color: var(--accent); outline: 2px solid var(--accent); outline-offset: 2px; }
.field__textarea:disabled { cursor: not-allowed; opacity: 0.55; }
.field__textarea::placeholder { color: var(--text-placeholder); }
.field__error { font-size: var(--text-xs); color: var(--danger); }
.field__hint { font-size: var(--text-xs); color: var(--text-muted); }

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

/* ===== 测试行 ===== */
.test-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border);
}
.test-row__hint { font-size: var(--text-xs); color: var(--text-muted); }

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
.test-modal__icon { flex: none; width: 1.5rem; height: 1.5rem; }
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

/* ===== 移动端 ===== */
@media (max-width: 768px) {
  .field-grid--2,
  .field-grid--3,
  .field-grid--4,
  .skeleton-grid {
    grid-template-columns: 1fr;
  }
  .action-bar { flex-direction: column; align-items: stretch; }
  .action-bar__btns { justify-content: flex-end; }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .test-modal__spinner { animation: none; }
  .action-bar,
  .field__textarea { transition: none; }
}
</style>
