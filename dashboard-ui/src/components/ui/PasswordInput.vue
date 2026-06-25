<script setup lang="ts">
/* PasswordInput — 基于 BaseInput 扩展，加可见性切换按钮 aria-label="显示密码/隐藏密码"。 */
import { ref, computed } from 'vue';
import { Eye, EyeOff } from 'lucide-vue-next';
import BaseInput from './BaseInput.vue';

const props = withDefaults(
  defineProps<{
    modelValue?: string;
    label?: string;
    placeholder?: string;
    error?: string;
    hint?: string;
    disabled?: boolean;
    required?: boolean;
  }>(),
  { disabled: false, required: false },
);

const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>();

const visible = ref(false);
const type = computed(() => (visible.value ? 'text' : 'password'));
const toggleLabel = computed(() => (visible.value ? '隐藏密码' : '显示密码'));

function toggle(): void {
  if (props.disabled) return;
  visible.value = !visible.value;
}

function onModel(v: string): void {
  emit('update:modelValue', v);
}
</script>

<template>
  <BaseInput
    :model-value="modelValue"
    :label="label"
    :type="type"
    :placeholder="placeholder"
    :error="error"
    :hint="hint"
    :disabled="disabled"
    :required="required"
    @update:model-value="onModel"
  >
    <template #suffix>
      <button
        type="button"
        class="pwd-toggle"
        :aria-label="toggleLabel"
        :disabled="disabled"
        @click="toggle"
      >
        <component :is="visible ? EyeOff : Eye" :size="16" aria-hidden="true" />
      </button>
    </template>
  </BaseInput>
</template>

<style scoped>
.pwd-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 1.75rem;
  height: 1.75rem;
  margin-right: var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: background-color var(--dur-micro) var(--ease-out-quart), color var(--dur-micro) var(--ease-out-quart);
}
.pwd-toggle:hover { background: var(--bg-surface-2); color: var(--text-primary); }
.pwd-toggle:disabled { cursor: not-allowed; opacity: 0.55; }
.pwd-toggle:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
