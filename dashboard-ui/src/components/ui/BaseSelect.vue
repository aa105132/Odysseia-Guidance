<script setup lang="ts">
/* BaseSelect — 阶段0用原生 select + 自定义箭头图标，v-model 双绑。
 * 状态：默认/hover/focus/disabled + error + hint。 */
import { useId } from 'vue';
import { ChevronDown } from 'lucide-vue-next';

interface Option {
  value: string | number;
  label: string;
}

withDefaults(
  defineProps<{
    modelValue?: string | number;
    options: Option[];
    label?: string;
    error?: string;
    hint?: string;
    disabled?: boolean;
    required?: boolean;
    placeholder?: string;
  }>(),
  { disabled: false, required: false },
);

const emit = defineEmits<{
  (e: 'update:modelValue', v: string | number): void;
  (e: 'change', v: string | number): void;
}>();

const uid = useId();

function onChange(ev: Event): void {
  const v = (ev.target as HTMLSelectElement).value;
  emit('update:modelValue', v);
  emit('change', v);
}
</script>

<template>
  <div :class="['field', { 'has-error': !!error, 'is-disabled': disabled }]">
    <label v-if="label" :for="uid" class="field__label font-display">
      {{ label }}<span v-if="required" class="field__req" aria-hidden="true">*</span>
    </label>
    <div class="field__control">
      <select
        :id="uid"
        :value="modelValue"
        :disabled="disabled"
        :required="required"
        :aria-invalid="!!error"
        :aria-describedby="error ? `${uid}-err` : hint ? `${uid}-hint` : undefined"
        class="field__select"
        @change="onChange"
      >
        <option v-if="placeholder" value="" disabled selected>{{ placeholder }}</option>
        <option v-for="opt in options" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <ChevronDown class="field__arrow" aria-hidden="true" :size="16" />
    </div>
    <p v-if="error" :id="`${uid}-err`" class="field__error" role="alert">{{ error }}</p>
    <p v-else-if="hint" :id="`${uid}-hint`" class="field__hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.field { display: flex; flex-direction: column; gap: var(--space-2); }
.field__label { font-size: var(--text-sm); font-weight: var(--fw-semibold); color: var(--text-secondary); }
.field__req { color: var(--accent); margin-left: 0.25ch; }

.field__control {
  position: relative;
  display: flex;
  align-items: center;
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.field__control:hover { border-color: var(--border-strong); }

.field__select {
  flex: 1 1 auto;
  width: 100%;
  padding: var(--space-2) var(--space-3);
  padding-right: var(--space-7);
  background: transparent;
  border: 0;
  outline: none;
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: var(--text-base);
  line-height: var(--lh-normal);
  appearance: none;
  -webkit-appearance: none;
  cursor: pointer;
}
.field__select:disabled { cursor: not-allowed; }
.field__select option { background: var(--bg-surface); color: var(--text-primary); }

.field__arrow {
  position: absolute;
  right: var(--space-3);
  pointer-events: none;
  color: var(--text-muted);
  flex: none;
}

.field__control:focus-within { border-color: var(--accent); outline: 2px solid var(--accent); outline-offset: 2px; }

.field.has-error .field__control { border-color: var(--danger); }
.field.has-error .field__control:focus-within { outline-color: var(--danger); }
.field__error { font-size: var(--text-xs); color: var(--danger); }
.field__hint { font-size: var(--text-xs); color: var(--text-muted); }
.field.is-disabled .field__control { opacity: 0.55; }
</style>
