<script setup lang="ts">
/* Sparkline — 纯 SVG 迷你折线图，无图表库依赖。
 * 数值序列归一化到 [0,1] 映射 y（可选固定 min/max，否则自动）；当前值角落展示
 * （宋体数值 + unit）。纯展示组件，role="img" + aria-label；prefers-reduced-motion 禁过渡。 */
import { computed } from 'vue';

const props = withDefaults(
  defineProps<{
    data: number[];
    label?: string;
    unit?: string;
    color?: string; // 默认 var(--accent)
    height?: number; // 默认 40
    min?: number; // 可选固定下界，否则自动
    max?: number;
  }>(),
  { height: 40, color: 'var(--accent)' },
);

// viewBox 宽度固定 100，高度按 prop；preserveAspectRatio=none 横向拉伸填满宽度
const W = 100;
const PAD = 2; // 上下留白，避免折线贴边

const current = computed(() => (props.data.length ? props.data[props.data.length - 1] : null));

const range = computed(() => {
  if (!props.data.length) return { lo: 0, hi: 1, span: 1 };
  const lo = props.min ?? Math.min(...props.data);
  const hi = props.max ?? Math.max(...props.data);
  const span = hi - lo || 1; // 全相等 / hi==lo 防除零
  return { lo, hi, span };
});

// 折线点序列（length>=2 才有意义）
const points = computed(() => {
  const d = props.data;
  if (d.length < 2) return '';
  const { lo, span } = range.value;
  const innerH = props.height - PAD * 2;
  const stepX = (W - PAD) / (d.length - 1);
  return d
    .map((v, i) => {
      const x = PAD + i * stepX;
      const n = (v - lo) / span; // 0..1
      const y = PAD + (1 - n) * innerH; // 反转 y 轴
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(' ');
});

// 区域填充路径（折线 + 底边闭合）
const areaPath = computed(() => {
  const d = props.data;
  if (d.length < 2) return '';
  const { lo, span } = range.value;
  const H = props.height;
  const innerH = H - PAD * 2;
  const stepX = (W - PAD) / (d.length - 1);
  const top = d
    .map((v, i) => {
      const x = PAD + i * stepX;
      const n = (v - lo) / span;
      const y = PAD + (1 - n) * innerH;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(' L ');
  return `M ${PAD.toFixed(2)},${(H - PAD).toFixed(2)} L ${top} L ${(W - PAD).toFixed(2)},${(H - PAD).toFixed(2)} Z`;
});

// 单点：渲染一个小圆点（length===1 时折线无意义）
const singleDot = computed(() => {
  if (props.data.length !== 1) return null;
  const { lo, span } = range.value;
  const innerH = props.height - PAD * 2;
  const n = (props.data[0] - lo) / span;
  return { x: W / 2, y: PAD + (1 - n) * innerH };
});

const isEmpty = computed(() => !props.data.length);

const displayValue = computed(() => {
  const v = current.value;
  if (v === null) return '--';
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
});

const ariaLabel = computed(() => {
  const base = props.label ?? '趋势';
  const cur = current.value;
  if (cur === null) return `${base}：暂无数据`;
  const u = props.unit ?? '';
  return `${base}：当前 ${displayValue.value}${u}`;
});
</script>

<template>
  <div class="spark" :style="{ '--spark-color': color }">
    <div class="spark__head">
      <span v-if="label" class="spark__label">{{ label }}</span>
      <span class="spark__value font-display">
        {{ displayValue }}<span v-if="unit" class="spark__unit">{{ unit }}</span>
      </span>
    </div>

    <svg
      v-if="!isEmpty"
      class="spark__svg"
      :viewBox="`0 0 ${W} ${height}`"
      :height="height"
      preserveAspectRatio="none"
      role="img"
      :aria-label="ariaLabel"
    >
      <path v-if="areaPath" class="spark__area" :d="areaPath" />
      <polyline v-if="points" class="spark__line" :points="points" fill="none" />
      <circle
        v-if="singleDot"
        class="spark__dot"
        :cx="singleDot.x"
        :cy="singleDot.y"
        r="1.6"
      />
    </svg>

    <div
      v-else
      class="spark__empty"
      role="img"
      :aria-label="ariaLabel"
      :style="{ height: height + 'px' }"
    >
      暂无数据
    </div>
  </div>
</template>

<style scoped>
.spark {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.spark__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}
.spark__label {
  font-size: var(--text-xs);
  color: var(--text-muted);
  font-weight: var(--fw-medium);
}
.spark__value {
  color: var(--text-primary);
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  letter-spacing: -0.01em;
}
.spark__unit {
  font-family: var(--font-sans);
  font-size: var(--text-xs);
  color: var(--text-secondary);
  margin-left: 0.125rem;
}

.spark__svg {
  width: 100%;
  display: block;
  overflow: visible;
}
.spark__line {
  stroke: var(--spark-color, var(--accent));
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
  vector-effect: non-scaling-stroke;
}
.spark__area {
  fill: var(--spark-color, var(--accent));
  fill-opacity: 0.14;
  stroke: none;
  transition: fill-opacity var(--dur-micro) var(--ease-out-quart);
}
.spark:hover .spark__area {
  fill-opacity: 0.22;
}
.spark__dot {
  fill: var(--spark-color, var(--accent));
}

.spark__empty {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-xs);
  color: var(--text-muted);
}

/* 降低动效：禁过渡，hover 不加深填充 */
@media (prefers-reduced-motion: reduce) {
  .spark__area {
    transition: none;
  }
  .spark:hover .spark__area {
    fill-opacity: 0.14;
  }
}
</style>
