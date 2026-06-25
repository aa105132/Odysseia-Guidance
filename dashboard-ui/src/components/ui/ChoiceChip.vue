<script setup lang="ts">
/* ChoiceChip — 选项芯片组，支持单选/多选(multiple)。
 * 选中：var(--accent-subtle) 底 + var(--text-primary) 文字 + var(--accent) 边框
 *   （琥珀文字 on 琥珀淡底仅 3.52:1 不达 AA，改 text-primary≈7.7:1；琥珀由边框+底色承载）。
 * a11y：单选 role=radiogroup + roving tabindex + ←→↑↓/Home/End 切换聚焦；
 *   多选 role=group + role=switch（Space 默认切换，全项 tabindex=0）。
 * 单选组可访问名由 label prop 提供（绑定 aria-label）。 */
import { computed, ref, nextTick } from 'vue';

interface Option {
  value: string | number;
  label: string;
}

const props = withDefaults(
  defineProps<{
    modelValue: string | number | (string | number)[];
    options: Option[];
    multiple?: boolean;
    /** 单选组可访问名，映射到 radiogroup 的 aria-label */
    label?: string;
  }>(),
  { multiple: false },
);

const emit = defineEmits<{ (e: 'update:modelValue', v: string | number | (string | number)[]): void }>();

const groupRef = ref<HTMLElement | null>(null);

const selected = computed<(string | number)[]>(() =>
  Array.isArray(props.modelValue) ? props.modelValue : props.modelValue === undefined ? [] : [props.modelValue],
);

function isSelected(v: string | number): boolean {
  return selected.value.includes(v);
}

/* 单选 roving tabindex：选中项 tabindex=0，其余 -1；无选中时首项 0 */
function tabindexFor(v: string | number, idx: number): number {
  if (props.multiple) return 0;
  const sel = selected.value;
  if (sel.length === 0) return idx === 0 ? 0 : -1;
  return sel[0] === v ? 0 : -1;
}

function select(v: string | number): void {
  if (props.multiple) {
    const arr = Array.isArray(props.modelValue) ? [...props.modelValue] : [];
    const idx = arr.indexOf(v);
    if (idx >= 0) arr.splice(idx, 1);
    else arr.push(v);
    emit('update:modelValue', arr);
  } else {
    emit('update:modelValue', v);
  }
}

/* 单选方向键：←→↑↓ 环切、Home/End 首末，切换并聚焦目标项 */
function onKeydown(e: KeyboardEvent, idx: number): void {
  if (props.multiple) return;
  const keys = ['ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown', 'Home', 'End'];
  if (!keys.includes(e.key)) return;
  e.preventDefault();
  const n = props.options.length;
  if (n === 0) return;
  let next = idx;
  if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (idx - 1 + n) % n;
  else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (idx + 1) % n;
  else if (e.key === 'Home') next = 0;
  else if (e.key === 'End') next = n - 1;
  const opt = props.options[next];
  if (!opt) return;
  select(opt.value);
  void nextTick(() => {
    const el = groupRef.value?.querySelector<HTMLElement>(`[data-chip-idx="${next}"]`);
    el?.focus();
  });
}
</script>

<template>
  <div ref="groupRef" class="chip-group" :role="multiple ? 'group' : 'radiogroup'" :aria-label="label">
    <button
      v-for="(opt, idx) in options"
      :key="opt.value"
      type="button"
      :data-chip-idx="idx"
      :class="['chip', { 'is-selected': isSelected(opt.value) }]"
      :role="multiple ? 'switch' : 'radio'"
      :aria-checked="isSelected(opt.value)"
      :tabindex="tabindexFor(opt.value, idx)"
      @click="select(opt.value)"
      @keydown="onKeydown($event, idx)"
    >
      {{ opt.label }}
    </button>
  </div>
</template>

<style scoped>
.chip-group {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.chip {
  display: inline-flex;
  align-items: center;
  padding: var(--space-1) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  cursor: pointer;
  white-space: nowrap;
  transition: background-color var(--dur-micro) var(--ease-out-quart),
    border-color var(--dur-micro) var(--ease-out-quart),
    color var(--dur-micro) var(--ease-out-quart);
}
.chip:hover { background: var(--bg-surface-2); border-color: var(--border-strong); color: var(--text-primary); }

/* 选中：琥珀淡底 + 琥珀边框 + 高对比文字（text-primary on accent-subtle≈7.7:1） */
.chip.is-selected {
  background: var(--accent-subtle);
  border-color: var(--accent);
  color: var(--text-primary);
  font-weight: var(--fw-medium);
}
.chip.is-selected:hover {
  background: var(--accent-subtle);
  border-color: var(--accent-hover);
  color: var(--text-primary);
}

.chip:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
