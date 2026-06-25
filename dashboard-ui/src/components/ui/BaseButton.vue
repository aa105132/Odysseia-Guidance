<script setup lang="ts">
/* BaseButton — 通用按钮，8状态全覆盖（默认/hover/active/focus-visible/disabled + loading）
 * primary 琥珀底白字（单一强调），secondary 暖面边框，ghost 透明，danger 危险描边。 */
import { computed } from 'vue';
import { Loader2 } from 'lucide-vue-next';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

const props = withDefaults(
  defineProps<{
    variant?: Variant;
    size?: Size;
    loading?: boolean;
    disabled?: boolean;
    /** 前置图标，传 lucide 组件或任意 VNode */
    icon?: any;
    type?: 'button' | 'submit' | 'reset';
  }>(),
  {
    variant: 'primary',
    size: 'md',
    loading: false,
    disabled: false,
    type: 'button',
  },
);

const emit = defineEmits<{ (e: 'click', ev: MouseEvent): void }>();

const isDisabled = computed(() => props.disabled || props.loading);

function onClick(ev: MouseEvent): void {
  if (isDisabled.value) return;
  emit('click', ev);
}
</script>

<template>
  <button
    :type="type"
    :class="['btn', `btn--${variant}`, `btn--${size}`, { 'is-loading': loading, 'is-disabled': isDisabled }]"
    :disabled="isDisabled"
    :aria-busy="loading"
    @click="onClick"
  >
    <Loader2 v-if="loading" class="btn__spinner" aria-hidden="true" />
    <component :is="icon" v-else-if="icon" class="btn__icon" aria-hidden="true" />
    <span v-if="$slots.default" class="btn__label"><slot /></span>
  </button>
</template>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  font-family: var(--font-sans);
  font-weight: var(--fw-medium);
  line-height: var(--lh-tight);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  white-space: nowrap;
  user-select: none;
  transition: background-color var(--dur-micro) var(--ease-out-quart),
    border-color var(--dur-micro) var(--ease-out-quart),
    color var(--dur-micro) var(--ease-out-quart),
    filter var(--dur-micro) var(--ease-out-quart);
}

/* 尺寸：按 4/8/12/16 间距阶梯 */
.btn--sm { padding: var(--space-1) var(--space-2); font-size: var(--text-sm); }
.btn--md { padding: var(--space-2) var(--space-3); font-size: var(--text-base); }
.btn--lg { padding: var(--space-3) var(--space-4); font-size: var(--text-lg); }

.btn__icon { flex: none; width: 1em; height: 1em; }
.btn__spinner { flex: none; width: 1em; height: 1em; animation: btn-spin 0.8s linear infinite; }
.btn__label { display: inline-block; }

@keyframes btn-spin { to { transform: rotate(360deg); } }

/* primary：琥珀底 + 深字（--text-on-accent），白字 on 琥珀仅 2.19:1 不达 AA，深字≈6.9:1 达 AAA */
.btn--primary { background: var(--accent); color: var(--text-on-accent); border-color: var(--accent); }
.btn--primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); color: var(--text-on-accent); }
.btn--primary:active { filter: brightness(0.94); }

/* secondary：暖面 + 边框 */
.btn--secondary { background: var(--bg-surface); border-color: var(--border); color: var(--text-primary); }
.btn--secondary:hover { background: var(--bg-surface-2); border-color: var(--border-strong); }
.btn--secondary:active { filter: brightness(0.96); }

/* ghost：透明，悬停浮暖底 */
.btn--ghost { background: transparent; color: var(--text-secondary); }
.btn--ghost:hover { background: var(--bg-surface-2); color: var(--text-primary); }
.btn--ghost:active { background: var(--bg-surface); }

/* danger：危险描边 + 危险字。resting 暗红淡底(inset 混色降亮)+亮红字(--danger-text)≈4.8:1；
 * hover 转实心红底+深字(--text-on-accent)≈5.3:1，描边→实心的自然交互反馈。 */
.btn--danger {
  background: color-mix(in oklch, var(--danger) 12%, var(--bg-inset));
  border-color: color-mix(in oklch, var(--danger) 60%, transparent);
  color: var(--danger-text);
}
.btn--danger:hover {
  background: var(--danger);
  border-color: var(--danger);
  color: var(--text-on-accent);
}
.btn--danger:active { filter: brightness(0.94); }

/* 禁用 / 加载 */
.btn.is-disabled { opacity: 0.5; cursor: not-allowed; }
.btn.is-loading { cursor: wait; }

/* focus-visible 琥珀环：base.css 已提供全局 outline，此处补 focus 时边框反馈 */
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
