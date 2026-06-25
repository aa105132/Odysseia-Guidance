<script setup lang="ts">
/* ImageSearchView — 图片搜索配置（OpenAI 兼容接口）。
 * 接 GET/PUT /api/config/image-search、POST /api/config/test-image-search。
 * useConfigForm 统一 load/save/validate/dirty/beforeunload；顶栏手动刷新 = 强制重载。
 * 敏感密钥 api_key GET 不回传明文，load 包装器置空串占位，用户填入才进 dirty payload。
 * extra_body 为 JSON 对象：JsonEditor 编辑文本缓冲，合法时写回 form.extra_body，
 * 非法时不写（避免脏状态），validate 阶段拦截保存。 */
import { computed, inject, onMounted, ref, watch } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import {
  Images,
  Plug,
  RotateCcw,
  Save,
  ServerOff,
  CheckCircle2,
  XCircle,
} from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import PasswordInput from '@/components/ui/PasswordInput.vue';
import JsonEditor from '@/components/ui/JsonEditor.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import { useConfigForm } from '@/composables/useConfigForm';
import { useToastStore } from '@/stores/toast';
import {
  getImageSearchConfig,
  saveImageSearchConfig,
  testImageSearch,
  type ImageSearchTestResult,
} from '@/api/domains/imageSearch';
import type { ImageSearchConfig } from '@/api/models';

const toast = useToastStore();
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh')!;

// load 包装器：清空写入型密钥（GET 不回传明文），form/original 同为空串，不进 dirty
async function load(): Promise<ImageSearchConfig> {
  const data = await getImageSearchConfig();
  return { ...data, api_key: '' };
}

// 客户端早校验，减少 400 往返
function validate(form: ImageSearchConfig): Record<string, string> | null {
  const errs: Record<string, string> = {};
  const mr = form.max_results;
  if (mr != null && (Number.isNaN(mr) || mr < 1 || mr > 50)) {
    errs.max_results = '取值范围 1 - 50';
  }
  const ts = form.timeout_seconds;
  if (ts != null && (Number.isNaN(ts) || ts < 10 || ts > 300)) {
    errs.timeout_seconds = '取值范围 10 - 300 秒';
  }
  // extra_body 文本非法时拦截保存（form.extra_body 未写入非法态，故校验文本缓冲）
  const t = extraBodyText.value.trim();
  if (t) {
    try {
      JSON.parse(t);
    } catch {
      errs.extra_body = '额外请求体不是合法 JSON';
    }
  }
  return Object.keys(errs).length ? errs : null;
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
} = useConfigForm<ImageSearchConfig>({
  load,
  save: saveImageSearchConfig,
  validate,
  successMessage: '图片搜索配置已保存',
});

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

// 顶栏手动刷新 = 强制重载配置 + 重置 dirty
onMounted(() => {
  registerRefresh?.(() => loadForm(true));
});

// ===== extra_body：JSON 文本缓冲 <-> form 对象 =====
const extraBodyText = ref('{}');

// 外部变化（load/save/reset 改变 form.extra_body）同步回文本缓冲；
// 打字中途（用户输入合法 JSON 解析出同对象）不覆盖，保留用户格式。
watch(
  () => form.value.extra_body,
  (obj) => {
    const canonical = JSON.stringify(obj ?? {}, null, 2);
    try {
      if (JSON.stringify(JSON.parse(extraBodyText.value), null, 2) === canonical) return;
    } catch {
      /* 当前文本非法，用 canonical 覆盖 */
    }
    extraBodyText.value = canonical;
  },
);

function onExtraBodyInput(v: string): void {
  extraBodyText.value = v;
  const trimmed = v.trim();
  if (!trimmed) {
    form.value.extra_body = {};
    return;
  }
  try {
    form.value.extra_body = JSON.parse(v);
  } catch {
    // 非法 JSON：不写 form.extra_body，由 JsonEditor 自身显示错误，validate 拦截保存
  }
}

// ===== 测试连接（POST /api/config/test-image-search，JSON 响应）=====
const testing = ref(false);
const testResult = ref<ImageSearchTestResult | null>(null);

async function runTest(): Promise<void> {
  if (testing.value) return;
  testing.value = true;
  testResult.value = null;
  try {
    const r = await testImageSearch();
    testResult.value = r;
    toast.push({
      type: r.success ? 'success' : 'error',
      message: r.status || r.message || (r.success ? '连接成功' : '连接失败'),
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : '测试连接失败';
    toast.push({ type: 'error', message: `图片搜索连接测试失败：${msg}` });
  } finally {
    testing.value = false;
  }
}

// ===== 辅助 =====
function asNumber(v: string): number | undefined {
  if (v === '' || v == null) return undefined;
  const n = Number(v);
  return Number.isNaN(n) ? undefined : n;
}

const hasData = computed(() => Object.keys(form.value ?? {}).length > 0);
const showSkeleton = computed(() => loading.value && !hasData.value);
const showEmpty = computed(() => !loading.value && !error.value && !hasData.value);

const configured = computed(() => !!form.value.configured);

type Tone = 'ok' | 'danger' | 'neutral';
function statusTone(status: string): Tone {
  if (status.startsWith('连接成功')) return 'ok';
  if (status.includes('未配置')) return 'neutral';
  if (status.startsWith('连接失败') || status.startsWith('连接错误')) return 'danger';
  return 'neutral';
}

function retry(): void {
  loadForm(true);
}
</script>

<template>
  <div class="view">
    <BaseSectionTitle
      :icon="Images"
      title="图片搜索"
      subtitle="OpenAI 兼容接口 · 月月联网找图与底图合并参考图的后端"
    />

    <!-- 错误横幅：inline + 重试 -->
    <div v-if="error && !showSkeleton" class="error-banner" role="alert">
      <div class="error-banner__text">
        <ServerOff :size="18" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>
      <BaseButton variant="ghost" size="sm" :icon="RotateCcw" @click="retry">重试</BaseButton>
    </div>

    <!-- 骨架屏：配置加载中 -->
    <div v-if="showSkeleton" class="card" aria-busy="true" aria-live="polite">
      <BaseSkeleton height="1.25rem" width="40%" rounded="var(--radius-md)" />
      <div class="skeleton-grid">
        <BaseSkeleton v-for="i in 4" :key="i" height="2.5rem" rounded="var(--radius-md)" />
      </div>
    </div>

    <!-- 空状态：无数据且无错误（兜底） -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="Images"
      title="暂无图片搜索配置"
      description="配置数据为空，请尝试重新加载。"
      action-text="重新加载"
      :action-icon="RotateCcw"
      @action="retry"
    />

    <!-- 配置表单 -->
    <template v-else>
      <!-- 就绪状态 -->
      <div class="status-row">
        <span class="status-chip" :class="configured ? 'is-ok' : 'is-muted'">
          <component :is="configured ? CheckCircle2 : XCircle" :size="14" aria-hidden="true" />
          图片搜索 {{ configured ? '已配置' : '未配置' }}
        </span>
        <span v-if="dirty" class="dirty-chip" role="status">有未保存的更改</span>
      </div>

      <section class="card">
        <h3 class="card__title font-display">接口配置</h3>
        <div class="field-grid">
          <BaseInput
            :model-value="form.api_url ?? ''"
            label="API URL"
            placeholder="https://api.openai.com/v1"
            hint="OpenAI 兼容端点，留空则回退环境变量 IMAGE_SEARCH_API_URL"
            :disabled="saving"
            @update:model-value="(v) => form.api_url = v"
          />
          <PasswordInput
            :model-value="form.api_key ?? ''"
            label="API Key"
            :placeholder="form.api_key_masked || '未配置'"
            :hint="form.has_api_key ? '已配置密钥，留空保持不变' : '尚未配置密钥'"
            :disabled="saving"
            @update:model-value="(v) => form.api_key = v"
          />
          <BaseInput
            :model-value="form.model ?? ''"
            label="模型"
            placeholder="gpt-4o-mini"
            hint="图片搜索使用的视觉模型 ID"
            :disabled="saving"
            @update:model-value="(v) => form.model = v"
          />
          <BaseInput
            :model-value="form.max_results ?? ''"
            type="number"
            label="最大结果数"
            placeholder="10"
            :error="fieldErrors.max_results"
            hint="单次搜索返回结果上限，1 - 50"
            :disabled="saving"
            @update:model-value="(v) => form.max_results = asNumber(v)"
          />
          <BaseInput
            :model-value="form.timeout_seconds ?? ''"
            type="number"
            label="超时（秒）"
            placeholder="60"
            :error="fieldErrors.timeout_seconds"
            hint="请求超时时间，10 - 300 秒"
            :disabled="saving"
            @update:model-value="(v) => form.timeout_seconds = asNumber(v)"
          />
        </div>

        <!-- 额外请求体：JSON 编辑器 -->
        <div class="extra-body">
          <label class="extra-body__label font-display">额外请求体</label>
          <p class="extra-body__hint">
            附加到请求体的自定义字段（JSON 对象）；留空或 {} 表示不附加。
          </p>
          <p v-if="fieldErrors.extra_body" class="extra-body__error" role="alert">{{ fieldErrors.extra_body }}</p>
          <JsonEditor
            :model-value="extraBodyText"
            @update:model-value="onExtraBodyInput"
          />
        </div>
      </section>

      <!-- 测试连接结果（inline） -->
      <section v-if="testResult" class="test-result" :class="testResult.success ? 'is-ok' : 'is-danger'">
        <div class="test-result__head">
          <component
            :is="testResult.success ? CheckCircle2 : XCircle"
            :size="16"
            aria-hidden="true"
          />
          <span>连接测试结果</span>
        </div>
        <p class="test-result__status" :class="`tone-${statusTone(testResult.status)}`">
          {{ testResult.status || (testResult.success ? '连接成功' : '连接失败') }}
        </p>
        <p v-if="testResult.message && !testResult.success" class="test-result__msg">{{ testResult.message }}</p>
        <pre v-if="testResult.body_preview" class="test-result__preview">{{ testResult.body_preview }}</pre>
      </section>

      <!-- 操作栏：保存 / 重置 / 测试 -->
      <div class="actions">
        <BaseButton
          variant="primary"
          size="md"
          :icon="Save"
          :loading="saving"
          :disabled="!dirty || saving"
          @click="submit"
        >
          保存配置
        </BaseButton>
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
          variant="secondary"
          size="md"
          :icon="Plug"
          :loading="testing"
          :disabled="saving"
          class="actions__test"
          @click="runTest"
        >
          测试连接
        </BaseButton>
      </div>
    </template>

    <!-- 路由离开确认 -->
    <BaseConfirmDialog
      v-model="leaveConfirm"
      title="放弃未保存的更改？"
      message="当前图片搜索配置有未保存的更改，离开将丢弃这些更改。"
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
  gap: var(--space-5);
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
.skeleton-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-3);
  margin-top: var(--space-4);
}

/* ===== 状态行 ===== */
.status-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}
.status-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
}
.status-chip.is-ok { color: var(--success); border-color: color-mix(in oklch, var(--success) 45%, transparent); }
.status-chip.is-muted { color: var(--text-muted); }
.dirty-chip {
  margin-left: auto;
  padding: var(--space-1) var(--space-3);
  background: var(--accent-subtle);
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  color: var(--accent);
  font-size: var(--text-xs);
}

/* ===== 卡片 ===== */
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
.card:hover { border-color: var(--border-strong); }
.card__title {
  font-size: var(--text-lg);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

/* ===== 字段网格：双列，窄屏单列 ===== */
.field-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-4);
}

/* ===== 额外请求体 ===== */
.extra-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-2);
}
.extra-body__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.extra-body__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}
.extra-body__error {
  font-size: var(--text-xs);
  color: var(--danger);
}

/* ===== 测试结果 ===== */
.test-result {
  padding: var(--space-4) var(--space-5);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-surface);
}
.test-result.is-ok { border-color: color-mix(in oklch, var(--success) 45%, transparent); }
.test-result.is-danger { border-color: color-mix(in oklch, var(--danger) 45%, transparent); }
.test-result__head {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  margin-bottom: var(--space-2);
}
.test-result__status { font-size: var(--text-sm); }
.test-result__status.tone-ok { color: var(--success); }
.test-result__status.tone-danger { color: var(--danger); }
.test-result__status.tone-neutral { color: var(--text-muted); }
.test-result__msg {
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin-top: var(--space-1);
}
.test-result__preview {
  margin-top: var(--space-2);
  padding: var(--space-3);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  font-family: var(--font-sans);
  font-size: var(--text-xs);
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 12rem;
  overflow: auto;
}

/* ===== 操作栏 ===== */
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  align-items: center;
}
.actions__test { margin-left: auto; }

/* ===== 窄屏单列 ===== */
@media (max-width: 768px) {
  .field-grid,
  .skeleton-grid { grid-template-columns: 1fr; }
  .actions__test { margin-left: 0; }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .card { transition: none; }
  .actions :deep(.btn__spinner) { animation: none; }
}
</style>
