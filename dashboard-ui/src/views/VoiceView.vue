<script setup lang="ts">
/* VoiceView — 语音设置（doubao/siliconflow/custom/xiaomi）
 * 接 GET/PUT /api/config/voice + POST /api/config/test-voice。
 * useConfigForm 统一 load/save/validate/dirty/beforeunload；保存后 PUT 仅回 {success,updated}，
 * 故 save 包装器内 re-GET 刷新 masked 字段。
 * JSON 字段（app_pool/extra_body 等）用 *_text 字符串镜像绑定 JsonEditor（对齐旧 SPA app_pool_text 模式），
 * load 时 stringify、save 时 parse 回对象，dirty 跟踪比较字符串精确可靠。
 * 敏感字段 api_key/access_token：load 时清空（form 与 original 同为空，不进 dirty 集合），
 * 用 masked placeholder + has_* 徽标展示；用户填入才随 PATCH 送出，避免覆盖回后端原值。
 * 试听走 VoiceTester（二进制 blob + objectURL 播放 + 卸载释放）。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { Mic, Save, RotateCcw, AlertTriangle, ServerOff } from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseSelect from '@/components/ui/BaseSelect.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import ChoiceChip from '@/components/ui/ChoiceChip.vue';
import PasswordInput from '@/components/ui/PasswordInput.vue';
import JsonEditor from '@/components/ui/JsonEditor.vue';
import VoiceTester from '@/components/shared/VoiceTester.vue';
import { useConfigForm } from '@/composables/useConfigForm';
import { getVoiceConfig, saveVoiceConfig } from '@/api/domains/voice';
import type { VoiceConfig } from '@/api/models';

// JSON 字段：text 镜像键 ↔ 后端对象字段 + 空集合字面量
const JSON_FIELDS = [
  { text: 'app_pool_text', out: 'app_pool', empty: '[]' },
  { text: 'app_default_voice_types_text', out: 'app_default_voice_types', empty: '{}' },
  { text: 'clone_voice_app_bindings_text', out: 'clone_voice_app_bindings', empty: '{}' },
  { text: 'available_voice_types_text', out: 'available_voice_types', empty: '[]' },
  { text: 'voice_type_hints_text', out: 'voice_type_hints', empty: '{}' },
  { text: 'extra_body_text', out: 'extra_body', empty: '{}' },
  { text: 'siliconflow_references_text', out: 'siliconflow_references', empty: '[]' },
] as const;

type JsonTextKey = (typeof JSON_FIELDS)[number]['text'];

/** 表单类型：VoiceConfig 去掉对象型 JSON 字段，换成 *_text 字符串镜像 + 写入专用 api_key */
interface VoiceForm extends Omit<
  VoiceConfig,
  'app_pool' | 'app_default_voice_types' | 'clone_voice_app_bindings' | 'available_voice_types' | 'voice_type_hints' | 'extra_body' | 'siliconflow_references'
> {
  app_pool_text: string;
  app_default_voice_types_text: string;
  clone_voice_app_bindings_text: string;
  available_voice_types_text: string;
  voice_type_hints_text: string;
  extra_body_text: string;
  siliconflow_references_text: string;
}

/** GET 配置 → 表单：清空敏感字段 + 对象 stringify 为 *_text */
function sanitize(cfg: VoiceConfig): VoiceForm {
  const {
    app_pool, app_default_voice_types, clone_voice_app_bindings,
    available_voice_types, voice_type_hints, extra_body, siliconflow_references,
    ...rest
  } = cfg;
  return {
    ...rest,
    api_key: '',          // GET 不返回，置空；用户填写才送
    access_token: '',     // 明文不回填表单，用 masked placeholder 展示
    app_pool_text: JSON.stringify(app_pool ?? [], null, 2),
    app_default_voice_types_text: JSON.stringify(app_default_voice_types ?? {}, null, 2),
    clone_voice_app_bindings_text: JSON.stringify(clone_voice_app_bindings ?? {}, null, 2),
    available_voice_types_text: JSON.stringify(available_voice_types ?? [], null, 2),
    voice_type_hints_text: JSON.stringify(voice_type_hints ?? {}, null, 2),
    extra_body_text: JSON.stringify(extra_body ?? {}, null, 2),
    siliconflow_references_text: JSON.stringify(siliconflow_references ?? [], null, 2),
  };
}

/** 字符串数值清理：空/NaN → undefined（不进 dirty，不送） */
function numVal(v: string): number | undefined {
  if (v === '' || v == null) return undefined;
  const n = Number(v);
  return Number.isNaN(n) ? undefined : n;
}

const {
  form, loading, saving, error, fieldErrors, dirty, dirtyFields,
  loadForm, submit, reset, patch,
} = useConfigForm<VoiceForm>({
  load: async () => sanitize(await getVoiceConfig()),
  save: async (body) => {
    // *_text → 对象；其余直传；空敏感字段不送
    const payload: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(body)) {
      const jf = JSON_FIELDS.find((f) => f.text === k);
      if (jf) {
        const txt = String(v ?? '').trim();
        payload[jf.out] = txt ? JSON.parse(txt) : JSON.parse(jf.empty);
      } else {
        payload[k] = v;
      }
    }
    if (payload.api_key === '') delete payload.api_key;
    if (payload.access_token === '') delete payload.access_token;
    return sanitize(await saveVoiceConfig(payload as Partial<VoiceConfig>));
  },
  validate: (f) => {
    const errs: Record<string, string> = {};
    for (const jf of JSON_FIELDS) {
      const txt = (f[jf.text as JsonTextKey] ?? '').trim();
      if (!txt) continue;
      try { JSON.parse(txt); } catch (e: unknown) {
        errs[jf.text] = e instanceof Error ? e.message : 'JSON 解析失败';
      }
    }
    if (f.speed_ratio != null && (f.speed_ratio < 0.2 || f.speed_ratio > 3.0)) errs.speed_ratio = '范围 0.2-3.0';
    if (f.volume_ratio != null && (f.volume_ratio < 0.2 || f.volume_ratio > 3.0)) errs.volume_ratio = '范围 0.2-3.0';
    if (f.pitch_ratio != null && (f.pitch_ratio < 0.1 || f.pitch_ratio > 3.0)) errs.pitch_ratio = '范围 0.1-3.0';
    if (f.emotion_scale != null && (f.emotion_scale < 1.0 || f.emotion_scale > 5.0)) errs.emotion_scale = '范围 1.0-5.0';
    if (f.max_text_length != null && (f.max_text_length < 20 || f.max_text_length > 8000)) errs.max_text_length = '范围 20-8000';
    if (f.request_timeout_seconds != null && (f.request_timeout_seconds < 5 || f.request_timeout_seconds > 300)) errs.request_timeout_seconds = '范围 5-300';
    if (f.provider === 'doubao' && f.clone_resource_id && !f.clone_resource_id.startsWith('seed-icl-')) {
      errs.clone_resource_id = '需以 seed-icl- 开头';
    }
    return Object.keys(errs).length ? errs : null;
  },
  successMessage: '语音配置已保存',
});

// 顶栏手动刷新 = 强制重拉 + 重置 dirty
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh')!;

onMounted(() => {
  registerRefresh?.(() => loadForm(true));
});

// ===== 派生选项 =====
const providerOptions = computed(() => {
  const list = form.value.available_providers;
  if (list && list.length) {
    return list.map((p) => ({ value: p.id ?? '', label: p.name ?? p.id ?? '' }));
  }
  return [
    { value: 'doubao', label: '火山引擎（豆包）' },
    { value: 'siliconflow', label: '硅基流动（OpenAI 兼容）' },
    { value: 'custom', label: '自定义 OpenAI 兼容' },
    { value: 'xiaomi', label: '小米 MiMo TTS' },
  ];
});

const audioFormatOptions = ['mp3', 'wav', 'ogg', 'opus', 'flac', 'aac', 'pcm'].map((v) => ({
  value: v, label: v.toUpperCase(),
}));

// 音色下拉：从 available_voice_types_text 解析，label 取 voice_type_hints 映射
const voiceTypeHints = computed<Record<string, string>>(() => {
  try { return JSON.parse(form.value.voice_type_hints_text || '{}') as Record<string, string>; }
  catch { return {}; }
});
const voiceTypeOptions = computed<{ value: string; label: string }[]>(() => {
  try {
    const arr = JSON.parse(form.value.available_voice_types_text || '[]') as string[];
    return arr.map((v) => ({ value: v, label: voiceTypeHints.value[v] ?? v }));
  } catch { return []; }
});

const isDoubao = computed(() => form.value.provider === 'doubao');
const isSiliconFlow = computed(() => form.value.provider === 'siliconflow');

const baseUrlPlaceholder = computed(() => {
  switch (form.value.provider) {
    case 'doubao': return '留空使用火山引擎默认端点';
    case 'siliconflow': return '留空使用硅基流动默认端点';
    case 'xiaomi': return '留空使用小米默认端点';
    default: return 'https://api.example.com/v1';
  }
});

// ===== 8 状态派生 =====
// 加载错误：仅当无数据时整屏替换为错误/空；保存错误靠 toast + fieldErrors，不打断表单
const loadError = computed(() => (error.value && !form.value.provider ? error.value : null));
const showSkeleton = computed(() => loading.value && !form.value.provider);
const showEmpty = computed(() => !loading.value && !form.value.provider && !loadError.value);
const serviceOk = computed(() => !!form.value.service_available);

// 试听禁用：未启用 / 配置加载中 / 保存中
const testerDisabled = computed(() => !form.value.enabled || loading.value || saving.value);

// ===== 未保存拦截：路由离开确认（beforeunload 由 useConfigForm 处理） =====
const showLeaveConfirm = ref(false);
const leaveNext = ref<((v?: boolean | undefined) => void) | null>(null);
onBeforeRouteLeave((_to, _from, next) => {
  if (dirty.value) {
    leaveNext.value = next;
    showLeaveConfirm.value = true;
  } else {
    next();
  }
});
function confirmLeave(): void {
  showLeaveConfirm.value = false;
  leaveNext.value?.();
  leaveNext.value = null;
}
function cancelLeave(): void {
  showLeaveConfirm.value = false;
  leaveNext.value?.(false);
  leaveNext.value = null;
}

// 放弃编辑确认
const showResetConfirm = ref(false);
function askReset(): void {
  if (!dirty.value) return;
  showResetConfirm.value = true;
}
function confirmReset(): void {
  showResetConfirm.value = false;
  reset();
}

function retryLoad(): void {
  loadForm(true).catch(() => { /* useConfigForm 已 toast */ });
}

async function onSubmit(): Promise<void> {
  await submit();
}
</script>

<template>
  <div class="view">
    <BaseSectionTitle :icon="Mic" title="语音设置" subtitle="月月的语音合成供应商与音色配置" />

    <!-- 加载骨架 -->
    <div v-if="showSkeleton" class="skeleton-stack" aria-busy="true" aria-live="polite">
      <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
      <BaseSkeleton height="6rem" rounded="var(--radius-lg)" />
      <BaseSkeleton height="10rem" rounded="var(--radius-lg)" />
      <BaseSkeleton height="8rem" rounded="var(--radius-lg)" />
    </div>

    <!-- 加载错误整屏 -->
    <div v-else-if="loadError" class="error-banner" role="alert">
      <div class="error-banner__text">
        <ServerOff :size="18" aria-hidden="true" />
        <span>{{ loadError }}</span>
      </div>
      <BaseButton variant="ghost" size="sm" :icon="RotateCcw" @click="retryLoad">重试</BaseButton>
    </div>

    <!-- 空状态 -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="Mic"
      title="暂无语音配置"
      description="未能读取语音配置数据，请确认后端服务正常后重试。"
      action-text="重新加载"
      :action-icon="RotateCcw"
      @action="retryLoad"
    />

    <!-- 表单 -->
    <template v-else>
      <form class="voice-form" novalidate @submit.prevent="onSubmit">
        <!-- 服务状态卡 -->
        <div class="status-card" :class="serviceOk ? 'is-ok' : 'is-down'">
          <span class="status-card__dot" aria-hidden="true" />
          <span class="status-card__label">
            语音服务{{ form.enabled ? (serviceOk ? '运行中' : '已启用但未就绪') : '未启用' }}
          </span>
        </div>

        <!-- 基础设置 -->
        <section class="block">
          <h3 class="block__title font-display">基础设置</h3>
          <div class="grid">
            <div class="field-row">
              <ToggleSwitch
                :model-value="!!form.enabled"
                label="启用语音合成"
                :disabled="loading || saving"
                @update:model-value="(v) => patch('enabled', v)"
              />
            </div>
            <div class="field-row field-row--full">
              <label class="field__label font-display">供应商</label>
              <ChoiceChip
                :model-value="form.provider ?? 'doubao'"
                :options="providerOptions"
                :disabled="loading || saving"
                @update:model-value="(v) => patch('provider', String(v))"
              />
            </div>
            <BaseInput
              :model-value="form.base_url ?? ''"
              label="Base URL"
              type="url"
              :placeholder="baseUrlPlaceholder"
              :disabled="loading || saving"
              :error="fieldErrors.base_url"
              @update:model-value="(v) => patch('base_url', v)"
            />
            <div class="field-row">
              <PasswordInput
                :model-value="form.api_key ?? ''"
                label="API Key"
                :placeholder="form.api_key_masked || '留空保持原值'"
                :disabled="loading || saving"
                :error="fieldErrors.api_key"
                @update:model-value="(v) => patch('api_key', v)"
              />
              <span class="badge" :class="form.has_api_key ? 'badge--ok' : 'badge--warn'">
                {{ form.has_api_key ? '已配置' : '未配置' }}
              </span>
            </div>
            <BaseInput
              :model-value="form.model_name ?? ''"
              label="模型名"
              :placeholder="'如 tts-a1.nano / CosyVoice-v1'"
              :disabled="loading || saving"
              :error="fieldErrors.model_name"
              @update:model-value="(v) => patch('model_name', v)"
            />
          </div>
        </section>

        <!-- 音色 -->
        <section class="block">
          <h3 class="block__title font-display">音色</h3>
          <div class="grid">
            <BaseSelect
              :model-value="form.voice_type ?? ''"
              :options="voiceTypeOptions"
              label="当前音色"
              placeholder="选择音色"
              :disabled="loading || saving"
              :error="fieldErrors.voice_type"
              @update:model-value="(v) => patch('voice_type', String(v))"
            />
            <div class="field-row field-row--full">
              <label class="field__label font-display">可用音色列表（JSON 数组）</label>
              <JsonEditor
                :model-value="form.available_voice_types_text"
                @update:model-value="(v) => patch('available_voice_types_text', v)"
              />
              <p v-if="fieldErrors.available_voice_types_text" class="field-err" role="alert">
                {{ fieldErrors.available_voice_types_text }}
              </p>
            </div>
            <div class="field-row field-row--full">
              <label class="field__label font-display">音色说明映射（JSON 对象，voice_id → 场景说明）</label>
              <JsonEditor
                :model-value="form.voice_type_hints_text"
                @update:model-value="(v) => patch('voice_type_hints_text', v)"
              />
              <p v-if="fieldErrors.voice_type_hints_text" class="field-err" role="alert">
                {{ fieldErrors.voice_type_hints_text }}
              </p>
            </div>
          </div>
        </section>

        <!-- 豆包账号与复刻 -->
        <section v-show="isDoubao" class="block">
          <h3 class="block__title font-display">豆包账号与复刻</h3>
          <div class="grid">
            <BaseInput
              :model-value="form.app_id ?? ''"
              label="App ID"
              :disabled="loading || saving"
              :error="fieldErrors.app_id"
              @update:model-value="(v) => patch('app_id', v)"
            />
            <div class="field-row">
              <PasswordInput
                :model-value="form.access_token ?? ''"
                label="Access Token"
                :placeholder="form.access_token_masked || '留空保持原值'"
                :disabled="loading || saving"
                :error="fieldErrors.access_token"
                @update:model-value="(v) => patch('access_token', v)"
              />
              <span class="badge" :class="form.has_access_token ? 'badge--ok' : 'badge--warn'">
                {{ form.has_access_token ? '已配置' : '未配置' }}
              </span>
            </div>
            <BaseInput
              :model-value="form.cluster ?? ''"
              label="Cluster"
              :placeholder="'如 volcano_tts'"
              :disabled="loading || saving"
              :error="fieldErrors.cluster"
              @update:model-value="(v) => patch('cluster', v)"
            />
            <BaseInput
              :model-value="form.clone_cluster ?? ''"
              label="复刻 Cluster"
              :placeholder="'如 volcano_icl'"
              :disabled="loading || saving"
              :error="fieldErrors.clone_cluster"
              @update:model-value="(v) => patch('clone_cluster', v)"
            />
            <BaseInput
              :model-value="form.clone_resource_id ?? ''"
              label="复刻资源 ID"
              :placeholder="'需以 seed-icl- 开头'"
              :disabled="loading || saving"
              :error="fieldErrors.clone_resource_id"
              @update:model-value="(v) => patch('clone_resource_id', v)"
            />
            <BaseInput
              :model-value="form.emotion ?? ''"
              label="情感"
              :placeholder="'如 happy / sad'"
              :disabled="loading || saving"
              :error="fieldErrors.emotion"
              @update:model-value="(v) => patch('emotion', v)"
            />
            <div class="field-row">
              <ToggleSwitch
                :model-value="!!form.enable_emotion"
                label="启用情感"
                :disabled="loading || saving"
                @update:model-value="(v) => patch('enable_emotion', v)"
              />
            </div>
            <BaseInput
              :model-value="form.emotion_scale ?? ''"
              label="情感强度"
              type="number"
              step="0.1"
              :placeholder="'1.0 - 5.0'"
              :disabled="loading || saving"
              :error="fieldErrors.emotion_scale"
              @update:model-value="(v) => patch('emotion_scale', numVal(v))"
            />
            <div class="field-row field-row--full">
              <label class="field__label font-display">账号池（JSON 数组，[{app_id, access_token}]）</label>
              <JsonEditor
                :model-value="form.app_pool_text"
                @update:model-value="(v) => patch('app_pool_text', v)"
              />
              <p v-if="fieldErrors.app_pool_text" class="field-err" role="alert">{{ fieldErrors.app_pool_text }}</p>
            </div>
            <div class="field-row field-row--full">
              <label class="field__label font-display">App 默认音色映射（JSON 对象，app_id → voice_type）</label>
              <JsonEditor
                :model-value="form.app_default_voice_types_text"
                @update:model-value="(v) => patch('app_default_voice_types_text', v)"
              />
              <p v-if="fieldErrors.app_default_voice_types_text" class="field-err" role="alert">
                {{ fieldErrors.app_default_voice_types_text }}
              </p>
            </div>
            <div class="field-row field-row--full">
              <label class="field__label font-display">复刻音色绑定（JSON 对象，voice_type → app_id）</label>
              <JsonEditor
                :model-value="form.clone_voice_app_bindings_text"
                @update:model-value="(v) => patch('clone_voice_app_bindings_text', v)"
              />
              <p v-if="fieldErrors.clone_voice_app_bindings_text" class="field-err" role="alert">
                {{ fieldErrors.clone_voice_app_bindings_text }}
              </p>
            </div>
          </div>
        </section>

        <!-- 硅基流动参考音色 -->
        <section v-show="isSiliconFlow" class="block">
          <h3 class="block__title font-display">硅基流动参考音色</h3>
          <div class="grid">
            <div class="field-row field-row--full">
              <label class="field__label font-display">参考音色列表（JSON 数组，[{audio, text}]）</label>
              <JsonEditor
                :model-value="form.siliconflow_references_text"
                @update:model-value="(v) => patch('siliconflow_references_text', v)"
              />
              <p v-if="fieldErrors.siliconflow_references_text" class="field-err" role="alert">
                {{ fieldErrors.siliconflow_references_text }}
              </p>
            </div>
          </div>
        </section>

        <!-- 生成参数 -->
        <section class="block">
          <h3 class="block__title font-display">生成参数</h3>
          <div class="grid">
            <BaseSelect
              :model-value="form.audio_format ?? 'mp3'"
              :options="audioFormatOptions"
              label="音频格式"
              :disabled="loading || saving"
              :error="fieldErrors.audio_format"
              @update:model-value="(v) => patch('audio_format', String(v))"
            />
            <BaseInput
              :model-value="form.speed_ratio ?? ''"
              label="语速"
              type="number"
              step="0.1"
              :placeholder="'0.2 - 3.0'"
              :disabled="loading || saving"
              :error="fieldErrors.speed_ratio"
              @update:model-value="(v) => patch('speed_ratio', numVal(v))"
            />
            <BaseInput
              :model-value="form.volume_ratio ?? ''"
              label="音量"
              type="number"
              step="0.1"
              :placeholder="'0.2 - 3.0'"
              :disabled="loading || saving"
              :error="fieldErrors.volume_ratio"
              @update:model-value="(v) => patch('volume_ratio', numVal(v))"
            />
            <BaseInput
              :model-value="form.pitch_ratio ?? ''"
              label="音调"
              type="number"
              step="0.1"
              :placeholder="'0.1 - 3.0'"
              :disabled="loading || saving"
              :error="fieldErrors.pitch_ratio"
              @update:model-value="(v) => patch('pitch_ratio', numVal(v))"
            />
            <BaseInput
              :model-value="form.generation_cost ?? ''"
              label="生成消耗（金币）"
              type="number"
              step="1"
              :placeholder="'>= 0'"
              :disabled="loading || saving"
              :error="fieldErrors.generation_cost"
              @update:model-value="(v) => patch('generation_cost', numVal(v))"
            />
            <BaseInput
              :model-value="form.max_text_length ?? ''"
              label="最大文本长度"
              type="number"
              step="1"
              :placeholder="'20 - 8000'"
              :disabled="loading || saving"
              :error="fieldErrors.max_text_length"
              @update:model-value="(v) => patch('max_text_length', numVal(v))"
            />
            <BaseInput
              :model-value="form.request_timeout_seconds ?? ''"
              label="请求超时（秒）"
              type="number"
              step="1"
              :placeholder="'5 - 300'"
              :disabled="loading || saving"
              :error="fieldErrors.request_timeout_seconds"
              @update:model-value="(v) => patch('request_timeout_seconds', numVal(v))"
            />
            <div class="field-row field-row--full">
              <label class="field__label font-display">扩展请求体 extra_body（JSON 对象，OpenAI 兼容透传）</label>
              <JsonEditor
                :model-value="form.extra_body_text"
                @update:model-value="(v) => patch('extra_body_text', v)"
              />
              <p v-if="fieldErrors.extra_body_text" class="field-err" role="alert">{{ fieldErrors.extra_body_text }}</p>
            </div>
          </div>
        </section>

        <!-- 动作栏 -->
        <div class="actions">
          <div class="actions__status">
            <span v-if="dirty" class="actions__dirty" role="status">
              <AlertTriangle :size="14" aria-hidden="true" />
              {{ dirtyFields.length }} 项未保存
            </span>
            <span v-else class="actions__clean">已同步</span>
          </div>
          <div class="actions__btns">
            <BaseButton
              variant="ghost"
              size="md"
              :icon="RotateCcw"
              :disabled="!dirty || saving"
              @click="askReset"
            >
              放弃修改
            </BaseButton>
            <BaseButton
              variant="primary"
              size="md"
              type="submit"
              :icon="Save"
              :loading="saving"
              :disabled="!dirty || loading"
            >
              保存配置
            </BaseButton>
          </div>
        </div>
      </form>

      <!-- 试听 -->
      <VoiceTester
        :voice-type="form.voice_type"
        :emotion="form.emotion"
        :enable-emotion="form.enable_emotion"
        :emotion-scale="form.emotion_scale"
        :provider="form.provider"
        :disabled="testerDisabled"
      />
    </template>

    <!-- 放弃修改确认 -->
    <BaseConfirmDialog
      v-model="showResetConfirm"
      title="放弃修改？"
      message="当前有未保存的更改，放弃后将恢复到上次保存的配置。"
      confirm-text="放弃"
      cancel-text="继续编辑"
      variant="danger"
      @confirm="confirmReset"
    />
    <!-- 路由离开确认 -->
    <BaseConfirmDialog
      v-model="showLeaveConfirm"
      title="离开页面？"
      message="语音设置有未保存的更改，离开后将丢失这些修改。"
      confirm-text="离开"
      cancel-text="留在本页"
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

/* ===== 骨架 ===== */
.skeleton-stack { display: flex; flex-direction: column; gap: var(--space-4); }

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

/* ===== 服务状态卡 ===== */
.status-card {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  background: var(--bg-surface);
}
.status-card.is-ok { border-color: color-mix(in oklch, var(--success) 45%, transparent); }
.status-card.is-ok .status-card__label { color: var(--success); }
.status-card.is-down .status-card__label { color: var(--text-secondary); }
.status-card__dot {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 9999px;
  background: var(--text-muted);
}
.status-card.is-ok .status-card__dot { background: var(--success); }
.status-card.is-down .status-card__dot { background: var(--warning); }

/* ===== 表单区块 ===== */
.voice-form { display: flex; flex-direction: column; gap: var(--space-6); }

.block {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.block__title {
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

/* 两列网格，窄屏单列 */
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4) var(--space-5);
}
.field-row { display: flex; flex-direction: column; gap: var(--space-2); }
.field-row--full { grid-column: span 2; }

.field__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.field-err { font-size: var(--text-xs); color: var(--danger); }

/* 敏感字段徽标 */
.badge {
  display: inline-flex;
  align-items: center;
  margin-right: var(--space-2);
  padding: 0 var(--space-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: var(--text-muted);
  white-space: nowrap;
}
.badge--ok { color: var(--success); border-color: color-mix(in oklch, var(--success) 45%, transparent); }
.badge--warn { color: var(--warning); border-color: color-mix(in oklch, var(--warning) 45%, transparent); }

/* ===== 动作栏 ===== */
.actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.actions__status { font-size: var(--text-sm); }
.actions__dirty {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--warning);
}
.actions__clean { color: var(--text-muted); }
.actions__btns { display: flex; align-items: center; gap: var(--space-2); }

/* ===== 窄屏单列 ===== */
@media (max-width: 768px) {
  .grid { grid-template-columns: 1fr; }
  .field-row--full { grid-column: span 1; }
  .actions { flex-direction: column; align-items: stretch; }
  .actions__btns { justify-content: flex-end; }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .status-card, .block, .actions { transition: none; }
}
</style>
