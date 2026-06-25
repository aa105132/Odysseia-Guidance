<script setup lang="ts">
/* VoiceTester — 语音试听组件（处理二进制音频响应）
 * 调 POST /api/config/test-voice，后端返回音频 blob（非 JSON）。
 * 成功：URL.createObjectURL 生成可播放链接，渲染 <audio controls>（不自动播放）。
 * 释放：重新试听或组件卸载时 URL.revokeObjectURL 释放旧 objectURL，避免内存泄漏
 * （旧 SPA 未 revoke，本组件补齐）。元信息取自 X-Voice-* 响应头。
 * 音色/情感/情感开关/情感强度仅 provider==='doubao' 时随请求送出（对齐后端显隐逻辑）。
 * 8 状态：默认/hover/active/focus-visible/disabled + loading + empty + error。 */
import { ref, computed, onUnmounted } from 'vue';
import { Play, Download } from 'lucide-vue-next';
import BaseButton from '@/components/ui/BaseButton.vue';
import { testVoice, type VoiceTestRequest, type VoiceTestResult } from '@/api/domains/voice';
import { useToastStore } from '@/stores/toast';

interface Props {
  /** 当前表单音色（随试听送出） */
  voiceType?: string;
  /** 豆包情感（仅 doubao 送出） */
  emotion?: string;
  enableEmotion?: boolean;
  emotionScale?: number;
  /** 当前供应商：doubao 才送情感参数 */
  provider?: string;
  /** 父级禁用（配置加载中/保存中/未启用） */
  disabled?: boolean;
}

const props = withDefaults(defineProps<Props>(), { disabled: false });

const toast = useToastStore();

const DEFAULT_TEXT = '你好呀，我是月月，这是语音测试。';
const text = ref(DEFAULT_TEXT);
const loading = ref(false);
const error = ref<string | null>(null);
const result = ref<VoiceTestResult | null>(null);

const isDoubao = computed(() => props.provider === 'doubao');
const sizeKb = computed(() =>
  result.value ? (Math.max(0.1, result.value.size / 1024)).toFixed(1) : '',
);
const fileExt = computed(() => {
  if (!result.value) return 'mp3';
  const ext = (result.value.ext || 'mp3').replace(/[^a-zA-Z0-9]/g, '');
  return ext || 'mp3';
});

/** 释放当前 objectURL，避免内存泄漏 */
function revokeUrl(): void {
  if (result.value?.url) {
    URL.revokeObjectURL(result.value.url);
  }
}

async function runTest(): Promise<void> {
  if (loading.value || props.disabled) return;
  const t = text.value.trim();
  if (!t) {
    error.value = '测试文本不能为空';
    toast.push({ type: 'warning', message: '测试文本不能为空' });
    return;
  }
  error.value = null;
  loading.value = true;
  // 释放旧 URL 再生成新结果
  revokeUrl();
  result.value = null;

  const payload: VoiceTestRequest = { text: t };
  if (props.voiceType) payload.voice_type = props.voiceType;
  // 情感参数仅 doubao 送出，其他 provider 后端不消费
  if (isDoubao.value) {
    if (props.emotion) payload.emotion = props.emotion;
    if (props.enableEmotion != null) payload.enable_emotion = props.enableEmotion;
    if (props.emotionScale != null) payload.emotion_scale = props.emotionScale;
  }

  try {
    result.value = await testVoice(payload);
    toast.push({ type: 'success', message: '试听音频已生成', duration: 2500 });
  } catch (e) {
    const msg = e instanceof Error ? e.message : '试听失败';
    error.value = msg;
    toast.push({ type: 'error', message: `试听失败：${msg}` });
  } finally {
    loading.value = false;
  }
}

function download(): void {
  if (!result.value) return;
  const a = document.createElement('a');
  a.href = result.value.url;
  a.download = `voice-test-${Date.now()}.${fileExt.value}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// 卸载时释放 objectURL（旧 SPA 缺失此清理）
onUnmounted(() => revokeUrl());
</script>

<template>
  <section class="tester" role="group" aria-label="语音试听">
    <div class="tester__head">
      <h3 class="tester__title font-display">试听</h3>
      <p class="tester__sub">用当前表单的音色生成示例语音。需先启用并保存语音配置。</p>
    </div>

    <div class="tester__row">
      <textarea
        v-model="text"
        class="tester__text"
        spellcheck="false"
        :disabled="disabled || loading"
        :aria-label="'试听文本'"
        :aria-invalid="!!error"
        placeholder="输入要试听的文本"
        rows="2"
      />
      <BaseButton
        variant="primary"
        size="md"
        :loading="loading"
        :disabled="disabled"
        :icon="Play"
        class="tester__btn"
        :aria-label="loading ? '正在生成试听' : '生成试听'"
        @click="runTest"
      >
        {{ loading ? '生成中' : '试听' }}
      </BaseButton>
    </div>

    <p v-if="error" class="tester__error" role="alert">{{ error }}</p>

    <!-- 成功结果：音频播放器 + 元信息 + 下载 -->
    <div v-if="result" class="tester__result">
      <audio :src="result.url" controls class="tester__audio" preload="metadata" />
      <div class="tester__meta">
        <span class="tester__chip" v-if="result.provider">{{ result.provider }}</span>
        <span class="tester__chip" v-if="result.model">{{ result.model }}</span>
        <span class="tester__chip" v-if="result.voiceType">{{ result.voiceType }}</span>
        <span class="tester__chip tester__chip--muted">{{ fileExt }} · {{ sizeKb }} KB</span>
        <BaseButton variant="ghost" size="sm" :icon="Download" class="tester__dl" @click="download">
          下载
        </BaseButton>
      </div>
    </div>

    <!-- empty：未生成时占位提示 -->
    <p v-else-if="!error" class="tester__hint">点击「试听」生成音频后可在此播放与下载。</p>
  </section>
</template>

<style scoped>
.tester {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}

.tester__head { display: flex; flex-direction: column; gap: var(--space-1); }
.tester__title { font-size: var(--text-lg); font-weight: var(--fw-semibold); color: var(--text-primary); }
.tester__sub { font-size: var(--text-sm); color: var(--text-muted); line-height: var(--lh-snug); }

.tester__row { display: flex; align-items: stretch; gap: var(--space-3); }

.tester__text {
  flex: 1 1 auto;
  min-width: 0;
  padding: var(--space-2) var(--space-3);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  line-height: var(--lh-normal);
  resize: vertical;
  outline: none;
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.tester__text:hover { border-color: var(--border-strong); }
.tester__text:focus-visible { border-color: var(--accent); outline: 2px solid var(--accent); outline-offset: 2px; }
.tester__text:disabled { cursor: not-allowed; opacity: 0.55; }

.tester__btn { flex: 0 0 auto; align-self: stretch; }

.tester__error { font-size: var(--text-xs); color: var(--danger); }
.tester__hint { font-size: var(--text-xs); color: var(--text-muted); }

.tester__result {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.tester__audio { width: 100%; height: 2.25rem; }

.tester__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}
.tester__chip {
  padding: 0 var(--space-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: var(--text-secondary);
}
.tester__chip--muted { color: var(--text-muted); }
.tester__dl { margin-left: auto; }

/* prefers-reduced-motion：停用按钮 spinner 旋转 */
@media (prefers-reduced-motion: reduce) {
  .tester :deep(.btn__spinner) { animation: none; }
  .tester__text { transition: none; }
}
</style>
