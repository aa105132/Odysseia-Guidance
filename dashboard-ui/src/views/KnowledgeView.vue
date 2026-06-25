<script setup lang="ts">
/* KnowledgeView — 知识库文档管理。
 * useCrudList 驱动分页列表（listDocuments 的 search ← composable 的 q，debounce 300ms），
 * getStats 单独拉统计卡。无 rebuild-embeddings 端点，创建/更新后须手动运行嵌入脚本，
 * 以指南面板提示。查看/编辑走 KnowledgeEditorModal（编辑/查看时拉全文）。
 * 顶栏刷新经 registerRefresh 注册 reload+loadStats。 */
import { computed, inject, onMounted, ref } from 'vue';
import {
  BookOpen,
  BookText,
  Plus,
  Search,
  X as XIcon,
  RefreshCw,
  AlertCircle,
  Eye,
  Pencil,
  Trash2,
} from 'lucide-vue-next';
import { useCrudList } from '@/composables/useCrudList';
import {
  listDocuments,
  createDocument,
  updateDocument,
  deleteDocument,
  getStats,
} from '@/api/domains/knowledge';
import type { KnowledgeDoc, KnowledgeStatsResponse } from '@/api/models';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseKpiTile from '@/components/ui/BaseKpiTile.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import KnowledgeEditorModal from '@/components/knowledge/KnowledgeEditorModal.vue';

// 列表 + CRUD：composable 的 q 映射到 listDocuments 的 search 参数
const {
  items,
  total,
  page,
  pageSize,
  loading,
  error,
  search,
  hasMore,
  isEmpty,
  reload,
  goToPage,
  setSearch,
  removeItem,
} = useCrudList<KnowledgeDoc>({
  fetchList: async ({ page: p, pageSize: ps, q }) => {
    const res = await listDocuments({ page: p, page_size: ps, search: q || undefined });
    return {
      items: res.documents ?? [],
      total: res.total ?? 0,
      page: res.page,
      pageSize: res.page_size,
    };
  },
  create: (b) => createDocument(b),
  update: (id, b) => updateDocument(Number(id), b),
  remove: async (id) => { await deleteDocument(Number(id)); },
  pageSize: 20,
});

// 统计卡（独立于列表，删除/新增后刷新以同步分块数）
const stats = ref<KnowledgeStatsResponse | null>(null);
const statsLoading = ref(false);
const statsError = ref<string | null>(null);

async function loadStats(): Promise<void> {
  statsLoading.value = true;
  statsError.value = null;
  try {
    stats.value = await getStats();
  } catch (e) {
    statsError.value = e instanceof Error ? e.message : '统计加载失败';
  } finally {
    statsLoading.value = false;
  }
}

// 顶栏手动刷新注册：inject App.vue provide 的 registerRefresh
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh')!;

async function refreshAll(): Promise<void> {
  await Promise.all([reload(), loadStats()]);
}

onMounted(() => {
  registerRefresh?.(refreshAll);
  void loadStats();
});

// 派生状态
const hasSearch = computed(() => search.value.trim().length > 0);
const showSkeleton = computed(() => loading.value && items.value.length === 0);
const showSearchEmpty = computed(() => !loading.value && isEmpty.value && hasSearch.value);
const showListEmpty = computed(() => !loading.value && isEmpty.value && !hasSearch.value);
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)));
const sourceCount = computed(() =>
  stats.value?.by_source ? Object.keys(stats.value.by_source).length : 0,
);

function clearSearch(): void {
  setSearch('');
}

function onRetry(): void {
  void reload();
  void loadStats();
}

// 弹窗状态
const modalOpen = ref(false);
const modalDoc = ref<KnowledgeDoc | null>(null);
const modalMode = ref<'create' | 'edit' | 'view'>('create');

function openCreate(): void {
  modalDoc.value = null;
  modalMode.value = 'create';
  modalOpen.value = true;
}
function openView(doc: KnowledgeDoc): void {
  modalDoc.value = doc;
  modalMode.value = 'view';
  modalOpen.value = true;
}
function openEdit(doc: KnowledgeDoc): void {
  modalDoc.value = doc;
  modalMode.value = 'edit';
  modalOpen.value = true;
}
function onModalSaved(): void {
  modalOpen.value = false;
  void reload();
  void loadStats();
}

// 删除确认
const deleteOpen = ref(false);
const deleteTarget = ref<KnowledgeDoc | null>(null);

function requestDelete(doc: KnowledgeDoc): void {
  deleteTarget.value = doc;
  deleteOpen.value = true;
}

async function confirmDelete(): Promise<void> {
  deleteOpen.value = false;
  const target = deleteTarget.value;
  if (!target?.id) return;
  const ok = await removeItem(target.id);
  if (ok) void loadStats(); // 分块数随删除变化
  deleteTarget.value = null;
}

// 日期格式化：ISO → YYYY-MM-DD HH:mm
function formatDate(s?: string | null): string {
  if (!s) return '—';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
</script>

<template>
  <div class="view">
    <BaseSectionTitle :icon="BookOpen" title="知识库" subtitle="月月的长期记忆 · 文档分片与检索" />

    <!-- 统计卡：3 列错落，primary 跨 2 -->
    <div v-if="statsLoading && !stats" class="kpi-grid" aria-busy="true">
      <BaseSkeleton class="kpi-grid__primary" height="6rem" rounded="var(--radius-lg)" />
      <BaseSkeleton height="5rem" rounded="var(--radius-lg)" />
      <BaseSkeleton height="5rem" rounded="var(--radius-lg)" />
    </div>
    <div v-else-if="stats" class="kpi-grid">
      <BaseKpiTile
        class="kpi-grid__primary"
        label="文档总数"
        :value="stats.total_documents ?? 0"
        unit="篇"
        importance="primary"
      />
      <BaseKpiTile label="向量分块" :value="stats.total_chunks ?? 0" unit="块" importance="secondary" />
      <BaseKpiTile label="数据来源" :value="sourceCount" unit="类" importance="secondary" />
    </div>
    <div v-else-if="statsError" class="stats-error" role="alert">
      <AlertCircle :size="16" aria-hidden="true" />
      <span class="stats-error__text">统计加载失败：{{ statsError }}</span>
      <BaseButton variant="ghost" size="sm" :icon="RefreshCw" :loading="statsLoading" @click="loadStats">
        重试
      </BaseButton>
    </div>

    <!-- 指南面板：无 rebuild 端点，提示手动运行嵌入脚本 -->
    <aside class="guide" aria-label="嵌入重建说明">
      <BookText class="guide__icon" aria-hidden="true" />
      <div class="guide__body">
        <p class="guide__title font-display">嵌入重建说明</p>
        <p class="guide__text">
          新建或更新文档后，需在服务器执行
          <code>python scripts/re_embed_knowledge.py</code> 生成向量分块，文档才会进入检索。
        </p>
      </div>
    </aside>

    <!-- 工具栏：搜索 + 新建 -->
    <div class="toolbar">
      <div class="toolbar__search">
        <BaseInput
          :model-value="search"
          placeholder="搜索标题或正文…"
          aria-label="搜索知识文档"
          @update:model-value="setSearch"
        >
          <template #suffix>
            <button
              v-if="hasSearch"
              type="button"
              class="search-clear"
              aria-label="清除搜索"
              @click="clearSearch"
            >
              <XIcon :size="14" aria-hidden="true" />
            </button>
          </template>
        </BaseInput>
      </div>
      <BaseButton variant="primary" size="md" :icon="Plus" @click="openCreate">新建文档</BaseButton>
    </div>

    <!-- 列表错误横幅 -->
    <div v-if="error && !showSkeleton" class="error-banner" role="alert">
      <div class="error-banner__text">
        <AlertCircle :size="18" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>
      <BaseButton variant="ghost" size="sm" :icon="RefreshCw" :loading="loading" @click="onRetry">
        重试
      </BaseButton>
    </div>

    <!-- 骨架（首次加载） -->
    <div v-if="showSkeleton" class="doc-list" aria-busy="true" aria-live="polite">
      <BaseSkeleton v-for="i in 5" :key="i" height="5.75rem" rounded="var(--radius-md)" />
    </div>

    <!-- 搜索空结果态 -->
    <BaseEmpty
      v-else-if="showSearchEmpty"
      :icon="Search"
      title="未找到匹配文档"
      :description="`没有标题或正文包含「${search}」的文档。`"
      action-text="清除搜索"
      :action-icon="XIcon"
      @action="clearSearch"
    />

    <!-- 列表空态 -->
    <BaseEmpty
      v-else-if="showListEmpty"
      :icon="BookOpen"
      title="暂无知识文档"
      description="月月还没有任何长期记忆，点击新建开始添加。"
      action-text="新建文档"
      :action-icon="Plus"
      @action="openCreate"
    />

    <!-- 文档列表 -->
    <ul v-else class="doc-list">
      <li v-for="doc in items" :key="doc.id" class="doc-row">
        <div class="doc-row__main">
          <div class="doc-row__head">
            <h3 class="doc-row__title font-display">{{ doc.title || '未命名' }}</h3>
            <span v-if="doc.category" class="doc-row__chip">{{ doc.category }}</span>
          </div>
          <p v-if="doc.preview" class="doc-row__preview">{{ doc.preview }}</p>
          <p class="doc-row__meta">
            <span>更新于 {{ formatDate(doc.updated_at) }}</span>
            <span v-if="doc.external_id" class="doc-row__ext">{{ doc.external_id }}</span>
          </p>
        </div>
        <div class="doc-row__actions">
          <BaseButton variant="ghost" size="sm" :icon="Eye" @click="openView(doc)">查看</BaseButton>
          <BaseButton variant="ghost" size="sm" :icon="Pencil" @click="openEdit(doc)">编辑</BaseButton>
          <BaseButton variant="danger" size="sm" :icon="Trash2" @click="requestDelete(doc)">删除</BaseButton>
        </div>
      </li>
    </ul>

    <!-- 分页 -->
    <nav v-if="totalPages > 1 && !isEmpty" class="pagination" aria-label="分页">
      <BaseButton
        variant="secondary"
        size="sm"
        :disabled="page <= 1 || loading"
        @click="goToPage(page - 1)"
      >
        上一页
      </BaseButton>
      <span class="pagination__info">
        第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 篇
      </span>
      <BaseButton
        variant="secondary"
        size="sm"
        :disabled="!hasMore || loading"
        @click="goToPage(page + 1)"
      >
        下一页
      </BaseButton>
    </nav>

    <!-- 新建/编辑/查看弹窗 -->
    <KnowledgeEditorModal
      v-model="modalOpen"
      :doc="modalDoc"
      :mode="modalMode"
      @saved="onModalSaved"
    />

    <!-- 删除确认 -->
    <BaseConfirmDialog
      v-model="deleteOpen"
      title="删除文档"
      :message="
        deleteTarget
          ? `将删除文档「${deleteTarget.title || '未命名'}」及其所有向量分块，此操作不可撤销。`
          : '将删除该文档及其所有向量分块。'
      "
      confirm-text="删除"
      variant="danger"
      @confirm="confirmDelete"
    />
  </div>
</template>

<style scoped>
.view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

/* ===== 统计卡网格：3 列，primary 跨 2 ===== */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-4);
}
.kpi-grid__primary {
  grid-column: span 2;
}
.kpi-grid :deep(.kpi) {
  transition: border-color var(--dur-micro) var(--ease-out-quart),
    background-color var(--dur-micro) var(--ease-out-quart);
}
.kpi-grid :deep(.kpi:hover) {
  border-color: var(--border-strong);
  background: var(--bg-surface-2);
}
.kpi-grid :deep(.kpi:active) {
  filter: brightness(0.97);
}

.stats-error {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: color-mix(in oklch, var(--danger) 8%, var(--bg-surface));
  border: 1px solid color-mix(in oklch, var(--danger) 35%, transparent);
  color: var(--danger);
  font-size: var(--text-sm);
}
.stats-error__text {
  flex: 1;
}

/* ===== 指南面板 ===== */
.guide {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  background: var(--accent-subtle);
  border: 1px solid color-mix(in oklch, var(--accent) 25%, transparent);
  border-radius: var(--radius-lg);
}
.guide__icon {
  flex: none;
  width: var(--space-5);
  height: var(--space-5);
  margin-top: 0.125rem;
  color: var(--accent);
}
.guide__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.guide__title {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}
.guide__text {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: var(--lh-relaxed);
}
.guide__text code {
  padding: 0 var(--space-1);
  background: var(--bg-inset);
  border-radius: var(--radius-sm);
  font-family: var(--font-sans);
  font-size: var(--text-xs);
  color: var(--accent);
}

/* ===== 工具栏 ===== */
.toolbar {
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
}
.toolbar__search {
  flex: 1 1 auto;
  max-width: 32rem;
}
.search-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-right: var(--space-2);
  width: 1.25rem;
  height: 1.25rem;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: background-color var(--dur-micro) var(--ease-out-quart),
    color var(--dur-micro) var(--ease-out-quart);
}
.search-clear:hover {
  background: var(--bg-surface-2);
  color: var(--text-primary);
}
.search-clear:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* ===== 错误横幅 ===== */
.error-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  background: color-mix(in oklch, var(--danger) 10%, var(--bg-surface));
  border: 1px solid color-mix(in oklch, var(--danger) 40%, transparent);
  border-radius: var(--radius-md);
}
.error-banner__text {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--danger);
  font-size: var(--text-sm);
}

/* ===== 文档列表 ===== */
.doc-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  list-style: none;
  margin: 0;
  padding: 0;
}
.doc-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-micro) var(--ease-out-quart),
    background-color var(--dur-micro) var(--ease-out-quart);
}
.doc-row:hover {
  border-color: var(--border-strong);
  background: var(--bg-surface-2);
}
.doc-row:active {
  background: var(--bg-inset);
}
.doc-row__main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.doc-row__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.doc-row__title {
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  line-height: var(--lh-snug);
  /* 单行省略，避免长标题撑高行 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.doc-row__chip {
  flex: none;
  padding: 0 var(--space-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: var(--text-muted);
}
.doc-row__preview {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  line-height: var(--lh-relaxed);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.doc-row__meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-xs);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.doc-row__ext {
  font-size: var(--text-xs);
  color: var(--text-placeholder);
}
.doc-row__actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

/* ===== 分页 ===== */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
}
.pagination__info {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

/* ===== 移动端 ===== */
@media (max-width: 768px) {
  .kpi-grid {
    grid-template-columns: 1fr;
  }
  .kpi-grid__primary {
    grid-column: span 1;
  }
  .toolbar {
    flex-direction: column;
    align-items: stretch;
  }
  .toolbar__search {
    max-width: none;
  }
  .doc-row {
    flex-direction: column;
    align-items: stretch;
  }
  .doc-row__actions {
    justify-content: flex-end;
  }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .kpi-grid :deep(.kpi),
  .doc-row,
  .search-clear {
    transition: none;
  }
}
</style>
