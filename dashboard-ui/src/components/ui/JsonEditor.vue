<script setup lang="ts">
/* JsonEditor — 阶段0：textarea + JSON.parse 校验 + 行号错误定位。
 * 高级编辑器（高亮/折叠）后续阶段补。 */
import { ref, watch, computed } from 'vue';

const props = defineProps<{
  modelValue: string;
}>();

const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>();

const text = ref(props.modelValue);
const error = ref<{ message: string; line: number | null } | null>(null);

// 外部更新同步进内部（避免覆盖用户正在输入的内容）
watch(
  () => props.modelValue,
  (v) => {
    if (v !== text.value) text.value = v;
  },
);

function onInput(ev: Event): void {
  const v = (ev.target as HTMLTextAreaElement).value;
  text.value = v;
  emit('update:modelValue', v);
  validate(v);
}

function validate(v: string): void {
  const trimmed = v.trim();
  if (!trimmed) {
    error.value = null;
    return;
  }
  try {
    JSON.parse(v);
    error.value = null;
  } catch (e: any) {
    const msg: string = e?.message ?? 'JSON 解析失败';
    let line: number | null = null;
    // V8/SpiderMonkey 报 "position N (line L column C)" 或 "Unexpected token at position N"
    const posMatch = msg.match(/position\s+(\d+)/i);
    if (posMatch) {
      const pos = Number(posMatch[1]);
      line = v.slice(0, pos).split('\n').length;
    } else {
      const lineMatch = msg.match(/line\s+(\d+)/i);
      if (lineMatch) line = Number(lineMatch[1]);
    }
    error.value = { message: msg, line };
  }
}

const statusText = computed(() => {
  if (error.value) {
    return error.value.line ? `第 ${error.value.line} 行：${error.value.message}` : error.value.message;
  }
  return text.value.trim() ? 'JSON 合法' : '';
});

const isValid = computed(() => !error.value && text.value.trim().length > 0);
</script>

<template>
  <div class="json-editor">
    <textarea
      class="json-editor__area"
      :value="text"
      spellcheck="false"
      autocomplete="off"
      autocapitalize="off"
      aria-label="JSON 编辑器"
      :aria-invalid="!!error"
      @input="onInput"
    />
    <p v-if="error" class="json-editor__status is-error" role="alert">{{ statusText }}</p>
    <p v-else-if="isValid" class="json-editor__status is-ok" role="status">{{ statusText }}</p>
  </div>
</template>

<style scoped>
.json-editor { display: flex; flex-direction: column; gap: var(--space-2); }

.json-editor__area {
  width: 100%;
  min-height: 12rem;
  padding: var(--space-3) var(--space-4);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  line-height: var(--lh-relaxed);
  resize: vertical;
  outline: none;
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.json-editor__area:hover { border-color: var(--border-strong); }
.json-editor__area:focus-visible { border-color: var(--accent); outline: 2px solid var(--accent); outline-offset: 2px; }

.json-editor__status { font-size: var(--text-xs); }
.json-editor__status.is-error { color: var(--danger); }
.json-editor__status.is-ok { color: var(--success); }
</style>
