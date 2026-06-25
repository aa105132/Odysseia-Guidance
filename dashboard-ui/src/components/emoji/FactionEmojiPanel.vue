<script setup lang="ts">
/* FactionEmojiPanel — 活动阵营表情映射只读面板。
 * 后端 faction_mappings 仅 GET 暴露，无增删改端点（api.py L4998-5011，recon 明确只读），
 * 故本面板为只读折叠展示，不提供 CRUD（不臆造端点）。旧 SPA 同样仅折叠展示（HEAD L3992-4012）。
 * 结构：event_id → faction_id → EmojiMapping[]；事件区可折叠，阵营分组列出占位符与 Discord 表情。
 * 8 状态：默认/hover/focus-visible/active（折叠头）+ loading 骨架 + empty + error+重试 + reduced-motion。 */
import { computed, ref } from 'vue';
import { AlertCircle, ChevronDown, RefreshCw, Swords, Inbox } from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import type { EmojiMapping } from '@/api/models';

const props = defineProps<{
  /** faction_mappings：event_id → faction_id → EmojiMapping[] */
  mappings?: Record<string, Record<string, EmojiMapping[]>>;
  /** 父视图加载态 */
  loading?: boolean;
  /** 父视图加载错误 */
  error?: string | null;
}>();

const emit = defineEmits<{
  (e: 'refresh'): void;
}>();

// 事件展开态：默认全部展开（量小）；用户可点击折叠头切换
const collapsed = ref<Record<string, boolean>>({});

function toggle(eventId: string): void {
  collapsed.value = { ...collapsed.value, [eventId]: !collapsed.value[eventId] };
}

const events = computed(() => {
  const m = props.mappings ?? {};
  return Object.keys(m).sort((a, b) => a.localeCompare(b, 'zh-CN'));
});

/** 单事件下的映射总数 */
function eventTotal(eventId: string): number {
  const factions = props.mappings?.[eventId] ?? {};
  let n = 0;
  for (const fac of Object.keys(factions)) n += (factions[fac] ?? []).length;
  return n;
}

const totalMappings = computed(() => {
  let n = 0;
  for (const ev of events.value) n += eventTotal(ev);
  return n;
});

const showSkeleton = computed(() => !!props.loading && !props.mappings);
const showError = computed(() => !props.loading && !!props.error && !props.mappings);
const showEmpty = computed(
  () => !props.loading && !props.error && totalMappings.value === 0,
);
const showList = computed(() => !!props.mappings && totalMappings.value > 0);

function factionKeys(eventId: string): string[] {
  const factions = props.mappings?.[eventId] ?? {};
  return Object.keys(factions).sort((a, b) => a.localeCompare(b, 'zh-CN'));
}

function retry(): void {
  emit('refresh');
}
</script>

<template>
  <section class="panel">
    <BaseSectionTitle
      :icon="Swords"
      title="活动阵营表情"
      subtitle="按活动与阵营分组的表情映射（只读，后端未提供修改端点）"
    />

    <!-- 加载骨架 -->
    <div v-if="showSkeleton" class="rows" aria-busy="true" aria-live="polite">
      <div v-for="i in 2" :key="i" class="row row--skeleton">
        <BaseSkeleton width="6rem" height="1.25rem" rounded="var(--radius-sm)" />
        <BaseSkeleton width="80%" height="0.9rem" rounded="var(--radius-sm)" />
      </div>
    </div>

    <!-- 加载错误 -->
    <BaseEmpty
      v-else-if="showError"
      :icon="AlertCircle"
      title="活动阵营表情加载失败"
      :description="error ?? '无法读取活动阵营表情映射。'"
      action-text="重新加载"
      :action-icon="RefreshCw"
      @action="retry"
    />

    <!-- 空态 -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="Inbox"
      title="暂无活动阵营表情"
      description="当前没有配置任何活动阵营表情映射。"
    />

    <!-- 事件折叠列表 -->
    <div v-else-if="showList" class="events" role="list">
      <div
        v-for="eventId in events"
        :key="eventId"
        class="event"
        role="listitem"
      >
        <button
          type="button"
          class="event__head"
          :aria-expanded="!collapsed[eventId]"
          :aria-controls="`faction-${eventId}`"
          @click="toggle(eventId)"
        >
          <ChevronDown
            class="event__chevron"
            :class="{ 'event__chevron--collapsed': collapsed[eventId] }"
            aria-hidden="true"
            :size="16"
          />
          <span class="event__title font-display">{{ eventId }}</span>
          <span class="event__count">{{ eventTotal(eventId) }} 项</span>
        </button>
        <div
          v-show="!collapsed[eventId]"
          :id="`faction-${eventId}`"
          class="event__body"
        >
          <div
            v-for="fac in factionKeys(eventId)"
            :key="fac"
            class="faction"
          >
            <p class="faction__title">{{ fac }}</p>
            <ul class="faction__list">
              <li
                v-for="(m, idx) in (mappings?.[eventId]?.[fac] ?? [])"
                :key="idx"
                class="mapping"
              >
                <span class="mapping__placeholder font-display">{{ m.placeholder }}</span>
                <span class="mapping__arrows" aria-hidden="true">→</span>
                <span class="mapping__emojis">
                  <span
                    v-for="(e, j) in (m.discord_emojis ?? [])"
                    :key="j"
                    class="mapping__emoji"
                  >{{ e }}</span>
                </span>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}

/* ===== 骨架 ===== */
.rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.row--skeleton {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  pointer-events: none;
}

/* ===== 事件折叠 ===== */
.events {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.event {
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.event:hover {
  border-color: var(--border-strong);
}
.event__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-3) var(--space-4);
  border: 0;
  background: transparent;
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: var(--text-base);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-micro) var(--ease-out-quart);
}
.event__head:hover {
  background: var(--bg-surface-2);
}
.event__head:active {
  background: var(--bg-surface);
}
.event__head:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.event__chevron {
  flex: none;
  color: var(--text-muted);
  transition: transform var(--dur-micro) var(--ease-out-quart);
}
.event__chevron--collapsed {
  transform: rotate(-90deg);
}
.event__title {
  font-weight: var(--fw-semibold);
  word-break: break-all;
}
.event__count {
  margin-left: auto;
  font-size: var(--text-xs);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.event__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4) var(--space-4);
  border-top: 1px solid var(--border);
}

/* ===== 阵营分组 ===== */
.faction {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.faction__title {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.faction__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  list-style: none;
  margin: 0;
  padding: 0;
}

/* ===== 单条映射 ===== */
.mapping {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  flex-wrap: wrap;
}
.mapping__placeholder {
  flex: none;
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--accent);
}
.mapping__arrows {
  flex: none;
  color: var(--text-muted);
  font-size: var(--text-sm);
}
.mapping__emojis {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1);
  min-width: 0;
}
.mapping__emoji {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  font-family: var(--font-sans);
  word-break: break-all;
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .event,
  .event__head,
  .event__chevron {
    transition: none;
  }
}
</style>
