<script setup lang="ts">
/* ThreadAutoSpeakerView — 帖子自动发言（自动暖贴）配置。
 * 接 GET/PUT /api/config/thread-auto-speaker。useConfigForm 统一 load/save/validate/dirty/beforeunload。
 * PUT 回 {success, updated, message}（updated 为部分字典，非完整配置），save 包装器 await 后重新 GET 刷新。
 * thread_ids 为 string[]（后端 _serialize_thread_ids 回传字符串避免精度丢失，_normalize_thread_ids 解析 int 去重 >0 ≤100）：
 *   视图用 chip 输入，逐条添加/删除，提交前转 string[]，后端再 int 化。
 * 8 状态 + dirty 路由离开拦截（onBeforeRouteLeave + BaseConfirmDialog）+ reduced-motion。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import {
  AlertTriangle,
  MessageCircle,
  Plus,
  RotateCw,
  Save,
  X as XIcon,
} from 'lucide-vue-next';
import { useConfigForm } from '@/composables/useConfigForm';
import {
  getThreadAutoSpeakerConfig,
  saveThreadAutoSpeakerConfig,
} from '@/api/domains/threadAutoSpeaker';
import type { ThreadAutoSpeakerConfig } from '@/api/models';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseSelect from '@/components/ui/BaseSelect.vue';
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';

// 顶栏手动刷新注入
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');

// ===== 字段级校验（前端早筛，范围取自 api.py L5688-5840 + recon） =====
function validate(f: ThreadAutoSpeakerConfig): Record<string, string> | null {
  const e: Record<string, string> = {};
  const num = (v: unknown): v is number => typeof v === 'number' && !Number.isNaN(v);

  // thread_ids：每项可解析为正整数，总数 ≤100
  const ids = f.thread_ids ?? [];
  if (ids.length > 100) e.thread_ids = '目标 ID 数量不能超过 100 个';
  else {
    const bad = ids.find((id) => !/^\d+$/.test(String(id).trim()) || Number(id) <= 0);
    if (bad) e.thread_ids = `无效的帖子 ID：${bad}（需为正整数）`;
  }

  if (num(f.check_interval_seconds) && (f.check_interval_seconds < 30 || f.check_interval_seconds > 3600))
    e.check_interval_seconds = '轮询间隔需在 30–3600 秒';
  if (num(f.message_interval_seconds) && (f.message_interval_seconds < 60 || f.message_interval_seconds > 86400))
    e.message_interval_seconds = '发言间隔需在 60–86400 秒';
  if (num(f.idle_trigger_seconds) && (f.idle_trigger_seconds < 300 || f.idle_trigger_seconds > 604800))
    e.idle_trigger_seconds = '冷场阈值需在 300–604800 秒';
  if (num(f.idle_reminder_seconds)) {
    if (f.idle_reminder_seconds < 300 || f.idle_reminder_seconds > 604800)
      e.idle_reminder_seconds = '冷场提醒间隔需在 300–604800 秒';
    else if (num(f.idle_trigger_seconds) && f.idle_reminder_seconds > f.idle_trigger_seconds)
      e.idle_reminder_seconds = '冷场提醒间隔需 ≤ 冷场阈值';
  }
  if (num(f.context_message_limit) && (f.context_message_limit < 5 || f.context_message_limit > 80))
    e.context_message_limit = '上下文消息条数需在 5–80 之间';
  if (num(f.new_thread_comment_delay_seconds) && (f.new_thread_comment_delay_seconds < 0 || f.new_thread_comment_delay_seconds > 7200))
    e.new_thread_comment_delay_seconds = '新帖评价延迟需在 0–7200 秒';
  if (num(f.new_thread_reply_max_chars) && (f.new_thread_reply_max_chars < 50 || f.new_thread_reply_max_chars > 1000))
    e.new_thread_reply_max_chars = '新帖回复最大字数需在 50–1000 之间';
  if (num(f.new_thread_rag_n_results) && (f.new_thread_rag_n_results < 1 || f.new_thread_rag_n_results > 20))
    e.new_thread_rag_n_results = '新帖 RAG 条数需在 1–20 之间';
  if (f.new_thread_reply_mode && f.new_thread_reply_mode !== 'analysis' && f.new_thread_reply_mode !== 'light')
    e.new_thread_reply_mode = '新帖回复模式仅支持 analysis 或 light';
  if (
    f.new_thread_style_focus &&
    f.new_thread_style_focus !== 'praise_and_answer' &&
    f.new_thread_style_focus !== 'praise_only' &&
    f.new_thread_style_focus !== 'answer_only'
  )
    e.new_thread_style_focus = '风格侧重点仅支持 praise_and_answer / praise_only / answer_only';

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
} = useConfigForm<ThreadAutoSpeakerConfig>({
  load: () => getThreadAutoSpeakerConfig(),
  // save 包装器：PUT 仅回 {success, updated, message}，需重新 GET 刷新 form/original
  save: async (body) => {
    await saveThreadAutoSpeakerConfig(body);
    return await getThreadAutoSpeakerConfig();
  },
  validate,
  successMessage: '自动暖贴配置已保存',
});

// ===== 派生状态 =====
const hasData = computed(() => Object.keys(form.value || {}).length > 0);
const showSkeleton = computed(() => loading.value && !hasData.value);
const showEmpty = computed(() => !loading.value && !error.value && !hasData.value);
const showLoadError = computed(() => !loading.value && !!error.value && !hasData.value);
const showForm = computed(() => hasData.value);

// ===== 字段写入助手（BaseInput 始终 emit string，数值字段需转换） =====
function setNum(key: keyof ThreadAutoSpeakerConfig, v: string): void {
  if (v === '') {
    patch(key, undefined as unknown as ThreadAutoSpeakerConfig[keyof ThreadAutoSpeakerConfig]);
    return;
  }
  const n = Number(v);
  if (!Number.isNaN(n)) patch(key, n as unknown as ThreadAutoSpeakerConfig[keyof ThreadAutoSpeakerConfig]);
}

function setStr(key: keyof ThreadAutoSpeakerConfig, v: string): void {
  patch(key, v as unknown as ThreadAutoSpeakerConfig[keyof ThreadAutoSpeakerConfig]);
}

// ===== thread_ids chip 编辑器 =====
const threadIdInput = ref('');
const threadIdError = ref('');

function addThreadId(): void {
  const raw = threadIdInput.value.trim();
  if (!raw) return;
  if (!/^\d+$/.test(raw) || Number(raw) <= 0) {
    threadIdError.value = '帖子 ID 需为正整数';
    return;
  }
  const ids = (form.value.thread_ids ?? []).slice();
  // 去重（字符串比较，后端再 int 去重）
  if (ids.includes(raw)) {
    threadIdError.value = '该帖子 ID 已存在';
    return;
  }
  if (ids.length >= 100) {
    threadIdError.value = '目标 ID 数量不能超过 100 个';
    return;
  }
  ids.push(raw);
  patch('thread_ids', ids as unknown as ThreadAutoSpeakerConfig['thread_ids']);
  threadIdInput.value = '';
  threadIdError.value = '';
}

function removeThreadId(id: string): void {
  const ids = (form.value.thread_ids ?? []).filter((x) => x !== id);
  patch('thread_ids', ids as unknown as ThreadAutoSpeakerConfig['thread_ids']);
}

function onThreadIdKeydown(ev: KeyboardEvent): void {
  if (ev.key === 'Enter') {
    ev.preventDefault();
    addThreadId();
  }
}

function clearThreadIdError(): void {
  if (threadIdError.value) threadIdError.value = '';
}

// ===== 下拉选项 =====
const replyModeOptions = [
  { value: 'analysis', label: '深度分析（analysis）' },
  { value: 'light', label: '轻量回复（light）' },
];
const styleFocusOptions = [
  { value: 'praise_and_answer', label: '夸赞 + 回答' },
  { value: 'praise_only', label: '仅夸赞' },
  { value: 'answer_only', label: '仅回答' },
];

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
  // 顶栏 @refresh → 强制重拉（配置静态，无需轮询）
  registerRefresh?.(() => loadForm(true));
});
</script>

<template>
  <div class="view">
    <BaseSectionTitle
      :icon="MessageCircle"
      title="自动暖贴"
      subtitle="帖子自动发言规则 · 冷场检测与新帖评价"
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
    <div v-if="showSkeleton" class="form-skeleton" aria-busy="true" aria-live="polite">
      <BaseSkeleton height="1.25rem" width="8rem" />
      <div class="form-skeleton__card">
        <BaseSkeleton height="1.5rem" width="40%" />
        <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
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

    <!-- 空状态：已加载但无配置（兜底） -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="MessageCircle"
      title="暂无自动暖贴配置"
      description="尚未读取到任何自动发言配置数据，请尝试重新加载。"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 加载错误：初始拉取失败 -->
    <BaseEmpty
      v-else-if="showLoadError"
      :icon="AlertTriangle"
      title="加载失败"
      :description="error ?? '无法读取自动暖贴配置。'"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 表单 -->
    <form v-else-if="showForm" class="form" @submit.prevent="onSubmit">
      <!-- 主开关 -->
      <section class="card">
        <div class="card__head">
          <h3 class="card__title font-display">总开关</h3>
        </div>
        <ToggleSwitch
          :model-value="!!form.enabled"
          label="启用自动暖贴"
          :disabled="saving"
          @update:model-value="(v) => patch('enabled', v as ThreadAutoSpeakerConfig['enabled'])"
        />
        <p class="card__hint">开启后月月会按规则在指定帖子自动发言，避免冷场。</p>
      </section>

      <!-- 目标帖子 -->
      <section class="card">
        <div class="card__head">
          <h3 class="card__title font-display">目标帖子</h3>
        </div>
        <div class="field">
          <label class="field__label font-display">帖子 ID 列表</label>
          <div class="chips">
            <span
              v-for="id in (form.thread_ids ?? [])"
              :key="id"
              class="chip"
            >
              <span class="chip__text">{{ id }}</span>
              <button
                type="button"
                class="chip__remove"
                :aria-label="`移除帖子 ${id}`"
                :disabled="saving"
                @click="removeThreadId(id)"
              >
                <XIcon :size="12" aria-hidden="true" />
              </button>
            </span>
          </div>
          <div class="chips-input">
            <input
              v-model="threadIdInput"
              class="chips-input__field"
              type="text"
              inputmode="numeric"
              aria-label="帖子 ID"
              placeholder="输入帖子 ID 后回车添加"
              :disabled="saving || (form.thread_ids ?? []).length >= 100"
              :aria-invalid="!!threadIdError || !!fieldErrors['thread_ids']"
              @keydown="onThreadIdKeydown"
              @input="clearThreadIdError"
            />
            <BaseButton
              variant="secondary"
              size="sm"
              type="button"
              :icon="Plus"
              :disabled="saving || !threadIdInput.trim() || (form.thread_ids ?? []).length >= 100"
              @click="addThreadId"
            >
              添加
            </BaseButton>
          </div>
          <p v-if="threadIdError" class="field__error" role="alert">{{ threadIdError }}</p>
          <p v-else-if="fieldErrors['thread_ids']" class="field__error" role="alert">{{ fieldErrors['thread_ids'] }}</p>
          <p v-else class="field__hint">
            最多 100 个；每项为正整数，回车或点击添加。当前 {{ (form.thread_ids ?? []).length }} 个。
          </p>
        </div>
      </section>

      <!-- 轮询与间隔 -->
      <section class="card">
        <div class="card__head">
          <h3 class="card__title font-display">轮询与间隔</h3>
        </div>
        <div class="card__grid">
          <BaseInput
            :model-value="form.check_interval_seconds ?? ''"
            label="轮询间隔（秒）"
            type="number"
            placeholder="300"
            :error="fieldErrors['check_interval_seconds']"
            :disabled="saving"
            @update:model-value="(v) => setNum('check_interval_seconds', v)"
          />
          <BaseInput
            :model-value="form.message_interval_seconds ?? ''"
            label="发言间隔（秒）"
            type="number"
            placeholder="1800"
            :error="fieldErrors['message_interval_seconds']"
            :disabled="saving"
            @update:model-value="(v) => setNum('message_interval_seconds', v)"
          />
          <BaseInput
            :model-value="form.idle_trigger_seconds ?? ''"
            label="冷场阈值（秒）"
            type="number"
            placeholder="7200"
            :error="fieldErrors['idle_trigger_seconds']"
            :disabled="saving"
            @update:model-value="(v) => setNum('idle_trigger_seconds', v)"
          />
          <BaseInput
            :model-value="form.idle_reminder_seconds ?? ''"
            label="冷场提醒间隔（秒）"
            type="number"
            placeholder="3600"
            :error="fieldErrors['idle_reminder_seconds']"
            :disabled="saving"
            @update:model-value="(v) => setNum('idle_reminder_seconds', v)"
          />
          <BaseInput
            :model-value="form.context_message_limit ?? ''"
            label="上下文消息条数"
            type="number"
            placeholder="20"
            :error="fieldErrors['context_message_limit']"
            :disabled="saving"
            @update:model-value="(v) => setNum('context_message_limit', v)"
          />
        </div>
      </section>

      <!-- 新帖评价 -->
      <section class="card">
        <div class="card__head">
          <h3 class="card__title font-display">新帖评价</h3>
        </div>
        <ToggleSwitch
          :model-value="!!form.new_thread_comment_enabled"
          label="启用新帖评价"
          :disabled="saving"
          @update:model-value="(v) => patch('new_thread_comment_enabled', v as ThreadAutoSpeakerConfig['new_thread_comment_enabled'])"
        />
        <div class="card__grid">
          <BaseInput
            :model-value="form.new_thread_comment_delay_seconds ?? ''"
            label="评价延迟（秒）"
            type="number"
            placeholder="600"
            :error="fieldErrors['new_thread_comment_delay_seconds']"
            :disabled="saving"
            @update:model-value="(v) => setNum('new_thread_comment_delay_seconds', v)"
          />
          <BaseSelect
            :model-value="form.new_thread_reply_mode ?? ''"
            label="回复模式"
            :options="replyModeOptions"
            :error="fieldErrors['new_thread_reply_mode']"
            :disabled="saving"
            @update:model-value="(v) => setStr('new_thread_reply_mode', String(v))"
          />
          <BaseSelect
            :model-value="form.new_thread_style_focus ?? ''"
            label="风格侧重点"
            :options="styleFocusOptions"
            :error="fieldErrors['new_thread_style_focus']"
            :disabled="saving"
            @update:model-value="(v) => setStr('new_thread_style_focus', String(v))"
          />
          <BaseInput
            :model-value="form.new_thread_reply_max_chars ?? ''"
            label="回复最大字数"
            type="number"
            placeholder="200"
            :error="fieldErrors['new_thread_reply_max_chars']"
            :disabled="saving"
            @update:model-value="(v) => setNum('new_thread_reply_max_chars', v)"
          />
        </div>
        <ToggleSwitch
          :model-value="!!form.new_thread_include_question_answer"
          label="回复中包含问题回答"
          :disabled="saving"
          @update:model-value="(v) => patch('new_thread_include_question_answer', v as ThreadAutoSpeakerConfig['new_thread_include_question_answer'])"
        />
      </section>

      <!-- RAG 检索 -->
      <section class="card">
        <div class="card__head">
          <h3 class="card__title font-display">知识库检索（RAG）</h3>
        </div>
        <ToggleSwitch
          :model-value="!!form.new_thread_rag_enabled"
          label="新帖评价启用 RAG"
          :disabled="saving"
          @update:model-value="(v) => patch('new_thread_rag_enabled', v as ThreadAutoSpeakerConfig['new_thread_rag_enabled'])"
        />
        <div class="card__grid card__grid--single">
          <BaseInput
            :model-value="form.new_thread_rag_n_results ?? ''"
            label="RAG 检索条数"
            type="number"
            placeholder="5"
            :error="fieldErrors['new_thread_rag_n_results']"
            :disabled="saving"
            @update:model-value="(v) => setNum('new_thread_rag_n_results', v)"
          />
        </div>
      </section>

      <!-- 操作栏 -->
      <div class="actions">
        <BaseButton
          variant="ghost"
          size="md"
          :disabled="saving || !dirty"
          @click="onReset"
        >
          放弃修改
        </BaseButton>
        <BaseButton
          variant="primary"
          size="md"
          :icon="Save"
          :loading="saving"
          :disabled="saving"
          @click="onSubmit"
        >
          保存配置
        </BaseButton>
      </div>
    </form>

    <!-- 路由离开确认 -->
    <BaseConfirmDialog
      v-model="leaveConfirm"
      title="离开将丢弃未保存的修改"
      message="当前自动暖贴配置有未保存的更改，确定离开吗？"
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
  gap: var(--space-3);
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
}
.card__title {
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}
.card__hint {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--text-muted);
}
.card__grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-3);
}
.card__grid--single {
  grid-template-columns: 1fr;
  max-width: 24rem;
}

/* ===== 字段（chip 编辑器） ===== */
.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.field__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.field__error {
  font-size: var(--text-xs);
  color: var(--danger);
}
.field__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

/* ===== thread_ids chips ===== */
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  min-height: 2rem;
  padding: var(--space-2);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  align-content: flex-start;
}
.chips:empty {
  align-items: center;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 0 var(--space-2);
  height: 1.5rem;
  background: var(--bg-surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}
.chip__text {
  font-family: var(--font-sans);
}
.chip__remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1rem;
  height: 1rem;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: background-color var(--dur-micro) var(--ease-out-quart),
    color var(--dur-micro) var(--ease-out-quart);
}
.chip__remove:hover {
  background: color-mix(in oklch, var(--danger) 18%, transparent);
  color: var(--danger);
}
.chip__remove:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
.chip__remove:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.chips-input {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.chips-input__field {
  flex: 1 1 auto;
  width: 100%;
  padding: var(--space-2) var(--space-3);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: var(--text-base);
  outline: none;
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.chips-input__field:hover {
  border-color: var(--border-strong);
}
.chips-input__field:focus-visible {
  border-color: var(--accent);
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.chips-input__field:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.chips-input__field::placeholder {
  color: var(--text-placeholder);
}

/* ===== 操作栏 ===== */
.actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  padding-top: var(--space-2);
}

/* ===== 移动端 ===== */
@media (max-width: 768px) {
  .form-skeleton__grid,
  .card__grid {
    grid-template-columns: 1fr;
  }
  .actions {
    flex-direction: column-reverse;
    align-items: stretch;
  }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .card,
  .chip__remove,
  .chips-input__field {
    transition: none;
  }
}
</style>
