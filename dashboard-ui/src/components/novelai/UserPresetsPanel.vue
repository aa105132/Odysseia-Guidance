<script setup lang="ts">
/* UserPresetsPanel — NovelAI 用户预设（只读列表 + 删除）。
 * 后端 user-presets：GET 列表（{presets,total}，preset 含 user_id）、DELETE/{id}。
 * 用户只能在 Discord /draw 面板保存预设，管理面板不可创建/更新，仅展示与删除。
 * 采用 useCrudList + 客户端过滤/分页（按 name/artist/user_id 过滤），复用加载/空/错误态。
 * 8 状态：默认/hover/active/focus-visible/disabled + loading 骨架 + empty + error 重试 + reduced-motion。 */
import { computed, ref } from 'vue';
import {
  AlertCircle,
  Inbox,
  RefreshCw,
  Search,
  Trash2,
  Users,
  X as XIcon,
} from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import { useCrudList } from '@/composables/useCrudList';
import { deleteUserPreset, listUserPresets } from '@/api/domains/novelai';
import type { NovelAIUserPreset } from '@/api/models';

// 客户端过滤 + 分页：后端 user-presets 无分页/搜索参数
const crud = useCrudList<NovelAIUserPreset>({
  fetchList: async ({ page, pageSize, q }) => {
    const res = await listUserPresets();
    const all = res.presets ?? [];
    const needle = q.trim().toLowerCase();
    const filtered = needle
      ? all.filter(
          (p) =>
            (p.name ?? '').toLowerCase().includes(needle) ||
            (p.artist_string ?? '').toLowerCase().includes(needle) ||
            String(p.user_id ?? '').toLowerCase().includes(needle),
        )
      : all;
    const start = (page - 1) * pageSize;
    const items = filtered.slice(start, start + pageSize);
    return { items, total: filtered.length, page, pageSize };
  },
  remove: async (id) => { await deleteUserPreset(id as number); },
  pageSize: 10,
});

const totalPages = computed(() =>
  Math.max(1, Math.ceil(crud.total.value / crud.pageSize.value)),
);
const showingFrom = computed(() =>
  crud.total.value === 0 ? 0 : (crud.page.value - 1) * crud.pageSize.value + 1,
);
const showingTo = computed(() =>
  Math.min(crud.page.value * crud.pageSize.value, crud.total.value),
);

// 搜索空结果态与列表空态分流（isEmpty 不区分搜索/无数据，需 hasSearch 拆分）
const hasSearch = computed(() => crud.search.value.trim().length > 0);
const showSearchEmpty = computed(
  () => !crud.loading.value && crud.isEmpty.value && hasSearch.value,
);
const showListEmpty = computed(
  () => !crud.loading.value && crud.isEmpty.value && !hasSearch.value,
);

// ===== 删除确认 =====
const deleteTarget = ref<NovelAIUserPreset | null>(null);
const deleteOpen = ref(false);
const deleting = ref(false);

function askDelete(preset: NovelAIUserPreset): void {
  deleteTarget.value = preset;
  deleteOpen.value = true;
}

function cancelDelete(): void {
  deleteOpen.value = false;
  deleteTarget.value = null;
}

async function confirmDelete(): Promise<void> {
  if (!deleteTarget.value) return;
  deleting.value = true;
  try {
    await crud.removeItem(deleteTarget.value.id);
  } finally {
    deleting.value = false;
    deleteOpen.value = false;
    deleteTarget.value = null;
  }
}

function truncate(s: string | undefined, n: number): string {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '…' : s;
}

function retry(): void {
  void crud.reload();
}

function clearSearch(): void {
  crud.setSearch('');
}
</script>

<template>
  <section class="panel">
    <div class="panel__head">
      <BaseSectionTitle
        :icon="Users"
        title="用户预设"
        subtitle="用户在 Discord /draw 保存的画师串，仅可查看与删除"
      />
    </div>

    <!-- 搜索栏 -->
    <div class="panel__search">
      <BaseInput
        :model-value="crud.search.value"
        type="text"
        placeholder="搜索名称、画师串或用户 ID"
        :disabled="crud.loading.value"
        @update:model-value="crud.setSearch"
      />
    </div>

    <!-- 加载骨架 -->
    <div v-if="crud.loading.value && crud.items.value.length === 0" class="preset-list" aria-busy="true" aria-live="polite">
      <div v-for="i in 4" :key="i" class="preset-row preset-row--skeleton">
        <BaseSkeleton width="28%" height="1.1rem" rounded="var(--radius-sm)" />
        <BaseSkeleton width="55%" height="0.9rem" rounded="var(--radius-sm)" />
        <BaseSkeleton width="4rem" height="1.75rem" rounded="var(--radius-md)" />
      </div>
    </div>

    <!-- 加载失败空状态 -->
    <BaseEmpty
      v-else-if="crud.error.value && crud.items.value.length === 0"
      :icon="AlertCircle"
      title="用户预设加载失败"
      :description="crud.error.value"
      action-text="重新加载"
      :action-icon="RefreshCw"
      @action="retry"
    />

    <!-- 搜索空结果态 -->
    <BaseEmpty
      v-else-if="showSearchEmpty"
      :icon="Search"
      title="未找到匹配预设"
      :description="`没有名称、画师串或用户 ID 包含「${crud.search.value}」的用户预设。`"
      action-text="清除搜索"
      :action-icon="XIcon"
      @action="clearSearch"
    />

    <!-- 空态 -->
    <BaseEmpty
      v-else-if="showListEmpty"
      :icon="Inbox"
      title="暂无用户预设"
      description="用户在 Discord 使用 /draw 保存画师串后，将在此显示。"
    />

    <!-- 列表 -->
    <div v-else class="preset-list" role="list">
      <div
        v-for="preset in crud.items.value"
        :key="preset.id"
        class="preset-row"
        role="listitem"
      >
        <div class="preset-row__main">
          <div class="preset-row__name-line">
            <span class="preset-row__name font-display">{{ preset.name }}</span>
            <span class="badge badge--user">用户 {{ preset.user_id }}</span>
            <span v-if="preset.negative_prompt" class="badge badge--muted">含负面</span>
          </div>
          <p class="preset-row__artist">{{ truncate(preset.artist_string, 200) }}</p>
          <p v-if="preset.negative_prompt" class="preset-row__negative">
            <span class="badge badge--negative">负面</span>
            <span class="preset-row__negative-text">{{ truncate(preset.negative_prompt, 160) }}</span>
          </p>
          <p v-if="preset.created_at" class="preset-row__meta">保存于 {{ preset.created_at }}</p>
        </div>
        <div class="preset-row__actions">
          <BaseButton
            variant="danger"
            size="sm"
            :icon="Trash2"
            :disabled="deleting"
            @click="askDelete(preset)"
          >
            删除
          </BaseButton>
        </div>
      </div>
    </div>

    <!-- 分页 -->
    <div v-if="crud.total.value > crud.pageSize.value" class="pager">
      <BaseButton
        variant="ghost"
        size="sm"
        :disabled="crud.page.value <= 1 || crud.loading.value"
        @click="crud.goToPage(crud.page.value - 1)"
      >
        上一页
      </BaseButton>
      <span class="pager__info">
        第 {{ crud.page.value }} / {{ totalPages }} 页 · {{ showingFrom }}-{{ showingTo }} / 共 {{ crud.total.value }} 条
      </span>
      <BaseButton
        variant="ghost"
        size="sm"
        :disabled="crud.page.value >= totalPages || crud.loading.value"
        @click="crud.goToPage(crud.page.value + 1)"
      >
        下一页
      </BaseButton>
    </div>

    <!-- ===== 删除确认 ===== -->
    <BaseConfirmDialog
      v-model="deleteOpen"
      title="删除用户预设？"
      :message="deleteTarget ? `将删除用户 ${deleteTarget.user_id} 的预设「${deleteTarget.name}」，此操作不可撤销。` : '将删除该用户预设，此操作不可撤销。'"
      confirm-text="删除"
      variant="danger"
      @confirm="confirmDelete"
      @cancel="cancelDelete"
    />
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
.panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.panel__search { max-width: 24rem; }

/* ===== 列表行 ===== */
.preset-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.preset-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.preset-row:hover { border-color: var(--border-strong); }
.preset-row:focus-within { border-color: var(--accent); }
.preset-row--skeleton {
  align-items: center;
  gap: var(--space-4);
  pointer-events: none;
}
.preset-row__main {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  flex: 1 1 auto;
}
.preset-row__name-line {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.preset-row__name {
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  word-break: break-word;
}
.preset-row__artist {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: var(--lh-relaxed);
  word-break: break-word;
  margin: 0;
}
.preset-row__negative {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--text-muted);
  line-height: var(--lh-relaxed);
  margin: 0;
}
.preset-row__negative-text { word-break: break-word; }
.preset-row__meta {
  font-size: var(--text-xs);
  color: var(--text-muted);
  margin: 0;
}
.preset-row__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: none;
}

/* ===== 徽标 ===== */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 0 var(--space-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: var(--text-muted);
  white-space: nowrap;
}
.badge--muted { color: var(--text-muted); }
.badge--user {
  color: var(--accent);
  border-color: color-mix(in oklch, var(--accent) 40%, transparent);
  background: color-mix(in oklch, var(--accent) 10%, transparent);
}
.badge--negative {
  color: var(--warning);
  border-color: color-mix(in oklch, var(--warning) 45%, transparent);
  background: color-mix(in oklch, var(--warning) 10%, transparent);
  flex: none;
  margin-top: 0.125rem;
}

/* ===== 分页 ===== */
.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border);
}
.pager__info {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

/* ===== 移动端 ===== */
@media (max-width: 768px) {
  .panel__head { flex-direction: column; align-items: stretch; }
  .preset-row { flex-direction: column; align-items: stretch; }
  .preset-row__actions { justify-content: flex-end; }
  .pager { flex-direction: column; align-items: stretch; gap: var(--space-2); }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .preset-row { transition: none; }
}
</style>
