<script setup lang="ts">
/* BaseKpiTile — KPI 瓦片，按 importance 分层错落，禁等宽四卡网格。
 * primary 大号展示宋体数值，secondary 小号；数值用 var(--font-display) 避免 mono。 */
import { TrendingUp, TrendingDown } from 'lucide-vue-next';

withDefaults(
  defineProps<{
    label: string;
    value: string | number;
    unit?: string;
    trend?: { value: string; direction: 'up' | 'down' };
    importance?: 'primary' | 'secondary';
  }>(),
  { importance: 'secondary' },
);
</script>

<template>
  <div :class="['kpi', `kpi--${importance}`]">
    <p class="kpi__label">{{ label }}</p>
    <div class="kpi__value-row">
      <span class="kpi__value font-display">{{ value }}</span>
      <span v-if="unit" class="kpi__unit">{{ unit }}</span>
    </div>
    <div
      v-if="trend"
      :class="['kpi__trend', trend.direction === 'up' ? 'is-up' : 'is-down']"
    >
      <component :is="trend.direction === 'up' ? TrendingUp : TrendingDown" :size="14" aria-hidden="true" />
      <span>{{ trend.value }}</span>
    </div>
  </div>
</template>

<style scoped>
.kpi {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}

/* importance 分层错落：primary 大号大内距，secondary 小号小内距 */
.kpi--primary { padding: var(--space-6); }
.kpi--secondary { padding: var(--space-4); }

.kpi__label {
  font-size: var(--text-sm);
  color: var(--text-muted);
  font-weight: var(--fw-medium);
}

.kpi__value-row { display: flex; align-items: baseline; gap: var(--space-2); }
.kpi__value {
  color: var(--text-primary);
  font-weight: var(--fw-semibold);
  line-height: var(--lh-tight);
  letter-spacing: -0.02em;
}
.kpi--primary .kpi__value { font-size: var(--text-3xl); }
.kpi--secondary .kpi__value { font-size: var(--text-xl); }

.kpi__unit {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  font-family: var(--font-sans);
}

.kpi__trend {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-size: var(--text-xs);
  font-weight: var(--fw-medium);
}
.kpi__trend.is-up { color: var(--success); }
.kpi__trend.is-down { color: var(--danger); }
</style>
