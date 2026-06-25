<script setup lang="ts">
/* WebSearchView — 网络搜索配置（Grok + Tavily 双源）。
 * 接 GET/PUT /api/config/web-search、POST /api/config/test-web-search。
 * useConfigForm 统一 load/save/validate/dirty/beforeunload；顶栏手动刷新 = 强制重载。
 * 敏感密钥（grok_api_key/tavily_api_key）GET 不回传明文，load 包装器置空串占位，
 * 用户填入才进 dirty payload；保存后 save 包装器重新 GET 刷新 masked + configured。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import {
  Globe,
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
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import { useConfigForm } from '@/composables/useConfigForm';
import { useToastStore } from '@/stores/toast';
import {
  getWebSearchConfig,
  saveWebSearchConfig,
  testWebSearch,
  type WebSearchTestResult,
} from '@/api/domains/webSearch';
import type { WebSearchConfig } from '@/api/models';

const toast = useToastStore();
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh')!;

// load 包装器：清空写入型密钥（GET 不回传明文），form/original 同为空串，不进 dirty
async function load(): Promise<WebSearchConfig> {
  const data = await getWebSearchConfig();
  return { ...data, grok_api_key: '', tavily_api_key: '' };
}

// 客户端早校验，减少 400 往返（后端对范围兜底）
function validate(form: WebSearchConfig): Record<string, string> | null {
  const errs: Record<string, string> = {};
  const limit = form.search_history_fallback_fetch_limit;
  if (limit != null && (Number.isNaN(limit) || limit < 0 || limit > 50000)) {
    errs.search_history_fallback_fetch_limit = '取值范围 0 - 50000';
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
} = useConfigForm<WebSearchConfig>({
  load,
  save: saveWebSearchConfig,
  validate,
  successMessage: '网络搜索配置已保存',
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

// ===== 测试连接（POST /api/config/test-web-search，JSON 响应）=====
const testing = ref(false);
const testResult = ref<WebSearchTestResult | null>(null);

async function runTest(): Promise<void> {
  if (testing.value) return;
  testing.value = true;
  testResult.value = null;
  try {
    const r = await testWebSearch();
    testResult.value = r;
    const grokStatus = r.results?.grok?.status ?? '未知';
    const tavilyStatus = r.results?.tavily?.status ?? '未知';
    const grokModels = r.results?.grok?.models_count;
    const grokText = grokModels != null ? `${grokStatus}（${grokModels} 个模型）` : grokStatus;
    toast.push({
      type: r.success ? 'success' : 'error',
      message: `Grok: ${grokText} | Tavily: ${tavilyStatus}`,
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : '测试连接失败';
    toast.push({ type: 'error', message: `网络搜索连接测试失败：${msg}` });
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

const grokConfigured = computed(() => !!form.value.grok_configured);
const tavilyConfigured = computed(() => !!form.value.tavily_configured);

const grokTestStatus = computed(() => testResult.value?.results?.grok?.status ?? '');
const tavilyTestStatus = computed(() => testResult.value?.results?.tavily?.status ?? '');
const grokTestModels = computed(() => testResult.value?.results?.grok?.models_count ?? null);

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
      :icon="Globe"
      title="网络搜索"
      subtitle="Grok 与 Tavily 双源配置 · 月月联网检索的后端"
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
        <BaseSkeleton v-for="i in 6" :key="i" height="2.5rem" rounded="var(--radius-md)" />
      </div>
    </div>

    <!-- 空状态：无数据且无错误（兜底） -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="Globe"
      title="暂无网络搜索配置"
      description="配置数据为空，请尝试重新加载。"
      action-text="重新加载"
      :action-icon="RotateCcw"
      @action="retry"
    />

    <!-- 配置表单 -->
    <template v-else>
      <!-- 双源就绪状态 -->
      <div class="status-row">
        <span class="status-chip" :class="grokConfigured ? 'is-ok' : 'is-muted'">
          <component :is="grokConfigured ? CheckCircle2 : XCircle" :size="14" aria-hidden="true" />
          Grok 源 {{ grokConfigured ? '已配置' : '未配置' }}
        </span>
        <span class="status-chip" :class="tavilyConfigured ? 'is-ok' : 'is-muted'">
          <component :is="tavilyConfigured ? CheckCircle2 : XCircle" :size="14" aria-hidden="true" />
          Tavily 源 {{ tavilyConfigured ? '已配置' : '未配置' }}
        </span>
        <span v-if="dirty" class="dirty-chip" role="status">有未保存的更改</span>
      </div>

      <!-- Grok 配置 -->
      <section class="card">
        <h3 class="card__title font-display">Grok 搜索源</h3>
        <div class="field-grid">
          <BaseInput
            :model-value="form.grok_api_url ?? ''"
            label="Grok API URL"
            placeholder="https://api.x.ai/v1"
            hint="OpenAI 兼容端点，留空则回退环境变量 GROK_API_URL"
            :disabled="saving"
            @update:model-value="(v) => form.grok_api_url = v"
          />
          <PasswordInput
            :model-value="form.grok_api_key ?? ''"
            label="Grok API Key"
            :placeholder="form.grok_api_key_masked || '未配置'"
            :hint="form.has_grok_api_key ? '已配置密钥，留空保持不变' : '尚未配置密钥'"
            :disabled="saving"
            @update:model-value="(v) => form.grok_api_key = v"
          />
          <BaseInput
            :model-value="form.grok_model ?? ''"
            label="Grok 模型"
            placeholder="grok-3-mini"
            hint="联网检索使用的模型 ID"
            :disabled="saving"
            @update:model-value="(v) => form.grok_model = v"
          />
        </div>
      </section>

      <!-- Tavily 配置 -->
      <section class="card">
        <h3 class="card__title font-display">Tavily 搜索源</h3>
        <div class="field-grid">
          <BaseInput
            :model-value="form.tavily_api_url ?? ''"
            label="Tavily API URL"
            placeholder="https://api.tavily.com"
            hint="勿填 OpenAI/Grok 兼容端点，否则后端将拒绝"
            :disabled="saving"
            @update:model-value="(v) => form.tavily_api_url = v"
          />
          <PasswordInput
            :model-value="form.tavily_api_key ?? ''"
            label="Tavily API Key"
            :placeholder="form.tavily_api_key_masked || '未配置'"
            :hint="form.has_tavily_api_key ? '已配置密钥，留空保持不变' : '尚未配置密钥'"
            :disabled="saving"
            @update:model-value="(v) => form.tavily_api_key = v"
          />
        </div>
      </section>

      <!-- 通用设置 -->
      <section class="card">
        <h3 class="card__title font-display">通用设置</h3>
        <div class="field-grid">
          <BaseInput
            :model-value="form.search_history_fallback_fetch_limit ?? ''"
            type="number"
            label="历史回退抓取上限"
            placeholder="500"
            :error="fieldErrors.search_history_fallback_fetch_limit"
            hint="检索失败时回退抓取历史消息的条数上限，0 - 50000"
            :disabled="saving"
            @update:model-value="(v) => form.search_history_fallback_fetch_limit = asNumber(v)"
          />
        </div>
        <div class="toggle-row">
          <ToggleSwitch
            :model-value="!!form.show_sources"
            label="回复中展示检索来源"
            :disabled="saving"
            @update:model-value="(v) => form.show_sources = v"
          />
          <p class="toggle-row__hint">开启后月月在引用联网结果时附带来源链接</p>
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
        <ul class="test-result__list">
          <li>
            <span class="test-result__label">Grok</span>
            <span class="test-result__status" :class="`tone-${statusTone(grokTestStatus)}`">
              {{ grokTestStatus || '未测试' }}
              <span v-if="grokTestModels != null" class="test-result__meta">{{ grokTestModels }} 个模型</span>
            </span>
          </li>
          <li>
            <span class="test-result__label">Tavily</span>
            <span class="test-result__status" :class="`tone-${statusTone(tavilyTestStatus)}`">
              {{ tavilyTestStatus || '未测试' }}
            </span>
          </li>
        </ul>
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
      message="当前网络搜索配置有未保存的更改，离开将丢弃这些更改。"
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

/* ===== 双源状态行 ===== */
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

.toggle-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.toggle-row__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
  margin-left: calc(2.5rem + var(--space-2));
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
.test-result__list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--text-sm);
}
.test-result__list li {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
}
.test-result__label {
  flex: 0 0 4rem;
  color: var(--text-muted);
}
.test-result__status { color: var(--text-secondary); }
.test-result__status.tone-ok { color: var(--success); }
.test-result__status.tone-danger { color: var(--danger); }
.test-result__status.tone-neutral { color: var(--text-muted); }
.test-result__meta { color: var(--text-muted); margin-left: var(--space-2); font-size: var(--text-xs); }

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
