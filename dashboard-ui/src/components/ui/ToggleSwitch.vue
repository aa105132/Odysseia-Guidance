<script setup lang="ts">
/* ToggleSwitch — 开关，role=switch aria-checked。
 * 开：var(--accent) 底；切换 var(--dur-micro)。焦点环落在 track 上（input 视觉隐藏）。 */
const props = defineProps<{
  modelValue: boolean;
  label?: string;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void;
  (e: 'change', v: boolean): void;
}>();

function toggle(): void {
  if (props.disabled) return;
  const next = !props.modelValue;
  emit('update:modelValue', next);
  emit('change', next);
}
</script>

<template>
  <label :class="['toggle', { 'is-disabled': disabled, 'is-on': modelValue }]">
    <input
      type="checkbox"
      class="toggle__input"
      :checked="modelValue"
      :disabled="disabled"
      role="switch"
      :aria-checked="modelValue"
      :aria-label="label"
      @change="toggle"
    />
    <span class="toggle__track" aria-hidden="true">
      <span class="toggle__thumb" />
    </span>
    <span v-if="label" class="toggle__label">{{ label }}</span>
  </label>
</template>

<style scoped>
.toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  user-select: none;
}
.toggle.is-disabled { cursor: not-allowed; opacity: 0.55; }

/* 视觉隐藏 input 但保持可聚焦 */
.toggle__input {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  border: 0;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  overflow: hidden;
  white-space: nowrap;
}

.toggle__track {
  position: relative;
  width: 2.5rem;
  height: 1.5rem;
  border-radius: 9999px;
  background: var(--bg-inset);
  border: 1px solid var(--border);
  transition: background-color var(--dur-micro) var(--ease-out-quart),
    border-color var(--dur-micro) var(--ease-out-quart);
}
.toggle__thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 1rem;
  height: 1rem;
  border-radius: 9999px;
  background: var(--text-secondary);
  transition: transform var(--dur-micro) var(--ease-out-quart),
    background-color var(--dur-micro) var(--ease-out-quart);
}

/* 开：琥珀底 + 白圆点右移 */
.toggle.is-on .toggle__track { background: var(--accent); border-color: var(--accent); }
.toggle.is-on .toggle__thumb { transform: translateX(1rem); background: var(--text-primary); }

/* 焦点环落在 track（input 隐藏，全局 outline 不可见） */
.toggle__input:focus-visible + .toggle__track {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.toggle__label { font-size: var(--text-sm); color: var(--text-secondary); }
.toggle.is-disabled .toggle__label { color: var(--text-muted); }
</style>
