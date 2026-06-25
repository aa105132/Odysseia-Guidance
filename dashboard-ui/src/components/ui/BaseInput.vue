<script setup lang="ts">
/* BaseInput — 通用输入框，v-model 双绑，label 用展示宋体。
 * 状态：默认/hover/focus/disabled + error + hint；error 时危险边框 + 错误文案。
 * 预留 #suffix 插槽供 PasswordInput 等扩展。 */
import { useId } from 'vue';

withDefaults(
  defineProps<{
    modelValue?: string | number;
    label?: string;
    type?: string;
    placeholder?: string;
    error?: string;
    hint?: string;
    disabled?: boolean;
    required?: boolean;
  }>(),
  { type: 'text', disabled: false, required: false },
);

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void;
}>();

const uid = useId();

function onInput(ev: Event): void {
  emit('update:modelValue', (ev.target as HTMLInputElement).value);
}
</script>

<template>
  <div :class="['field', { 'has-error': !!error, 'is-disabled': disabled }]">
    <label v-if="label" :for="uid" class="field__label font-display">
      {{ label }}<span v-if="required" class="field__req" aria-hidden="true">*</span>
    </label>
    <div class="field__control">
      <input
        :id="uid"
        :type="type"
        :value="modelValue"
        :placeholder="placeholder"
        :disabled="disabled"
        :required="required"
        :aria-invalid="!!error"
        :aria-describedby="error ? `${uid}-err` : hint ? `${uid}-hint` : undefined"
        class="field__input"
        @input="onInput"
      />
      <slot name="suffix" />
    </div>
    <p v-if="error" :id="`${uid}-err`" class="field__error" role="alert">{{ error }}</p>
    <p v-else-if="hint" :id="`${uid}-hint`" class="field__hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.field { display: flex; flex-direction: column; gap: var(--space-2); }

.field__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.field__req { color: var(--accent); margin-left: 0.25ch; }

.field__control {
  display: flex;
  align-items: center;
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-micro) var(--ease-out-quart),
    background-color var(--dur-micro) var(--ease-out-quart);
}
.field__control:hover { border-color: var(--border-strong); }

.field__input {
  flex: 1 1 auto;
  width: 100%;
  padding: var(--space-2) var(--space-3);
  background: transparent;
  border: 0;
  outline: none;
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: var(--text-base);
  line-height: var(--lh-normal);
}
.field__input::placeholder { color: var(--text-placeholder); }
.field__input:disabled { cursor: not-allowed; }

/* focus：边框转琥珀，控制环由 :focus-visible 补 */
.field__control:focus-within { border-color: var(--accent); }
.field__control:focus-within .field__input { color: var(--text-primary); }
.field__control:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }

/* error */
.field.has-error .field__control { border-color: var(--danger); }
.field.has-error .field__control:focus-within { outline-color: var(--danger); border-color: var(--danger); }
.field__error { font-size: var(--text-xs); color: var(--danger); }
.field__hint { font-size: var(--text-xs); color: var(--text-muted); }

/* disabled */
.field.is-disabled .field__control { opacity: 0.55; }
</style>
