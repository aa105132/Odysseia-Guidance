<script setup lang="ts">
/* EmojiView — 表情映射管理。
 * default_mappings 走 useCrudList（客户端过滤/分页，后端 GET 无分页/搜索参数，同 AdminPresetsPanel 模式）：
 *   fetchList 拉 getEmojiConfig，按 q 过滤 placeholder/emoji 后切片；
 *   create → addEmojiMapping（POST /add，占位符已存在 400）；
 *   update → saveEmojiMappings（PUT 批量，单条 mappings 数组，按 placeholder 匹配覆盖/追加）；
 *   remove → deleteEmojiMapping（DELETE，id=placeholder，丢弃 {success,message} 返回值）。
 * faction_mappings 只读（无后端 CRUD 端点），交 FactionEmojiPanel 折叠展示。
 * available_placeholders 作 datalist 提示与校验参考。
 * 8 状态：默认/hover/active/focus-visible/disabled（BaseButton/BaseInput）+ loading 骨架 + empty +
 *   error 横幅+重试 + 编辑器 dirty 关闭确认 + reduced-motion。顶栏刷新注册 reload+刷新全量配置。 */
import { computed, inject, onMounted, ref } from 'vue';
import {
  AlertCircle,
  Inbox,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Smile,
  Trash2,
  X as XIcon,
} from 'lucide-vue-next';
import { useCrudList } from '@/composables/useCrudList';
import {
  addEmojiMapping,
  deleteEmojiMapping,
  getEmojiConfig,
  saveEmojiMappings,
} from '@/api/domains/emoji';
import type { EmojiConfig, EmojiMapping } from '@/api/models';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseModal from '@/components/ui/BaseModal.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import FactionEmojiPanel from '@/components/emoji/FactionEmojiPanel.vue';

// 占位符与 emoji 格式正则（对齐后端 api.py L5090 / L5055）
const PLACEHOLDER_RE = /^<\S+>$/;
const EMOJI_RE = /^<a?:\w+:\d+>$/;

// 全量配置（含 faction_mappings / available_placeholders），供面板与占位符提示复用
const fullConfig = ref<EmojiConfig | null>(null);
const configLoading = ref(false);
const configError = ref<string | null>(null);

async function loadConfig(): Promise<EmojiConfig> {
  configLoading.value = true;
  configError.value = null;
  try {
    const cfg = await getEmojiConfig();
    fullConfig.value = cfg;
    return cfg;
  } catch (e) {
    configError.value = e instanceof Error ? e.message : '表情配置加载失败';
    throw e;
  } finally {
    configLoading.value = false;
  }
}

// default_mappings CRUD：客户端过滤 + 分页（后端 GET 无分页/搜索参数）
const crud = useCrudList<EmojiMapping>({
  fetchList: async ({ page, pageSize, q }) => {
    const cfg = await loadConfig();
    const all = cfg.default_mappings ?? [];
    const needle = q.trim().toLowerCase();
    const filtered = needle
      ? all.filter((m) => {
          const ph = (m.placeholder ?? '').toLowerCase();
          const emojis = (m.discord_emojis ?? []).join(' ').toLowerCase();
          return ph.includes(needle) || emojis.includes(needle);
        })
      : all;
    const start = (page - 1) * pageSize;
    const items = filtered.slice(start, start + pageSize);
    return { items, total: filtered.length, page, pageSize };
  },
  create: async (b: EmojiMapping) => {
    await addEmojiMapping(b);
    // POST 仅回 {success,message}，不返回完整映射；reload 拉取真实列表
    return { ...b } as EmojiMapping;
  },
  update: async (id, b: Partial<EmojiMapping>) => {
    // id=placeholder；PUT 批量按 placeholder 匹配覆盖，发单条 mappings
    const mapping: EmojiMapping = {
      placeholder: String(id),
      discord_emojis: b.discord_emojis ?? [],
    };
    await saveEmojiMappings([mapping]);
    return { ...mapping } as EmojiMapping;
  },
  remove: async (id) => {
    // id=placeholder；DELETE 返回 {success,message}，丢弃返回值以满足 useCrudList 的 void 约定
    await deleteEmojiMapping(String(id));
  },
  pageSize: 12,
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

const hasSearch = computed(() => crud.search.value.trim().length > 0);
const showSkeleton = computed(
  () => crud.loading.value && crud.items.value.length === 0,
);
const showSearchEmpty = computed(
  () => !crud.loading.value && crud.isEmpty.value && hasSearch.value,
);
const showListEmpty = computed(
  () => !crud.loading.value && crud.isEmpty.value && !hasSearch.value,
);
const showListError = computed(
  () => !!crud.error.value && !showSkeleton.value && crud.items.value.length === 0,
);

const availablePlaceholders = computed(
  () => fullConfig.value?.available_placeholders ?? [],
);

function clearSearch(): void {
  crud.setSearch('');
}

function onRetry(): void {
  void crud.reload();
}

// ===== 编辑器（新建/编辑复用）=====
const editorOpen = ref(false);
const editingPlaceholder = ref<string | null>(null);
const form = ref<{ placeholder: string; emojisText: string }>({ placeholder: '', emojisText: '' });
const formErrors = ref<Record<string, string>>({});
const saving = ref(false);
const editorDirty = ref(false);
const isEdit = computed(() => editingPlaceholder.value !== null);
const editorTitle = computed(() => (isEdit.value ? '编辑表情映射' : '新建表情映射'));

function openCreate(): void {
  editingPlaceholder.value = null;
  form.value = { placeholder: '', emojisText: '' };
  formErrors.value = {};
  editorDirty.value = false;
  editorOpen.value = true;
}

function openEdit(m: EmojiMapping): void {
  editingPlaceholder.value = m.placeholder ?? null;
  form.value = {
    placeholder: m.placeholder ?? '',
    emojisText: (m.discord_emojis ?? []).join('\n'),
  };
  formErrors.value = {};
  editorDirty.value = false;
  editorOpen.value = true;
}

function markDirty(): void {
  editorDirty.value = true;
}

// 解析 emoji 文本：按逗号或换行分割，去空白，去空串
function parseEmojis(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

// 客户端校验（对齐后端 api.py L5090 / L5055）
function validate(): boolean {
  const errs: Record<string, string> = {};
  const ph = form.value.placeholder.trim();
  if (!ph) errs.placeholder = '占位符不能为空';
  else if (!PLACEHOLDER_RE.test(ph)) errs.placeholder = '占位符格式必须为 <名称>';
  const emojis = parseEmojis(form.value.emojisText);
  if (emojis.length === 0) errs.discord_emojis = '至少填写一个 Discord 表情';
  else {
    const bad = emojis.find((e) => !EMOJI_RE.test(e));
    if (bad) errs.discord_emojis = `无效的 Discord 表情格式：${bad}`;
  }
  formErrors.value = errs;
  return Object.keys(errs).length === 0;
}

async function saveMapping(): Promise<void> {
  if (saving.value) return;
  if (!validate()) return;
  saving.value = true;
  try {
    const body: EmojiMapping = {
      placeholder: form.value.placeholder.trim(),
      discord_emojis: parseEmojis(form.value.emojisText),
    };
    if (editingPlaceholder.value !== null) {
      // 编辑：占位符不可改（id 锁定），仅更新 emojis
      const ok = await crud.updateItem(editingPlaceholder.value, {
        discord_emojis: body.discord_emojis,
      });
      if (ok) editorOpen.value = false;
    } else {
      const ok = await crud.createItem(body);
      if (ok) editorOpen.value = false;
    }
  } finally {
    saving.value = false;
  }
}

// 关闭编辑器：dirty 时弹确认（避免丢弃未保存编辑）
const discardOpen = ref(false);

function requestClose(): void {
  if (!editorOpen.value) return;
  if (editorDirty.value) {
    discardOpen.value = true;
  } else {
    editorOpen.value = false;
  }
}

function onModalUpdate(v: boolean): void {
  if (!v) {
    requestClose();
  } else {
    editorOpen.value = true;
  }
}

function confirmDiscard(): void {
  discardOpen.value = false;
  editorDirty.value = false;
  editorOpen.value = false;
}

function cancelDiscard(): void {
  discardOpen.value = false;
}

// ===== 删除确认 =====
const deleteOpen = ref(false);
const deleteTarget = ref<EmojiMapping | null>(null);

function requestDelete(m: EmojiMapping): void {
  deleteTarget.value = m;
  deleteOpen.value = true;
}

async function confirmDelete(): Promise<void> {
  deleteOpen.value = false;
  const target = deleteTarget.value;
  if (!target?.placeholder) return;
  await crud.removeItem(target.placeholder);
  // 删的正在编辑则关闭编辑器
  if (editingPlaceholder.value === target.placeholder) {
    editorOpen.value = false;
    editingPlaceholder.value = null;
  }
  deleteTarget.value = null;
}

// ===== 顶栏刷新注册 =====
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');

async function refreshAll(): Promise<void> {
  await crud.reload();
}

onMounted(() => {
  registerRefresh?.(refreshAll);
});
</script>

<template>
  <div class="view">
    <BaseSectionTitle
      :icon="Smile"
      title="表情管理"
      subtitle="Discord 表情占位符映射 · 月月回复中的 &lt;占位符&gt; 自动替换"
    />

    <!-- 工具栏：搜索 + 新建 -->
    <div class="toolbar">
      <div class="toolbar__search">
        <BaseInput
          :model-value="crud.search.value"
          placeholder="搜索占位符或表情…"
          aria-label="搜索表情映射"
          :disabled="crud.loading.value"
          @update:model-value="crud.setSearch"
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
      <BaseButton variant="primary" size="md" :icon="Plus" @click="openCreate">
        新建映射
      </BaseButton>
    </div>

    <!-- 列表错误横幅（已有数据时 inline） -->
    <div v-if="crud.error.value && !showSkeleton && crud.items.value.length > 0" class="error-banner" role="alert">
      <div class="error-banner__text">
        <AlertCircle :size="18" aria-hidden="true" />
        <span>{{ crud.error.value }}</span>
      </div>
      <BaseButton variant="ghost" size="sm" :icon="RefreshCw" :loading="crud.loading.value" @click="onRetry">
        重试
      </BaseButton>
    </div>

    <!-- 骨架（首次加载） -->
    <div v-if="showSkeleton" class="mapping-list" aria-busy="true" aria-live="polite">
      <div v-for="i in 5" :key="i" class="mapping-row mapping-row--skeleton">
        <BaseSkeleton width="6rem" height="1.1rem" rounded="var(--radius-sm)" />
        <BaseSkeleton width="50%" height="0.9rem" rounded="var(--radius-sm)" />
        <BaseSkeleton width="6rem" height="1.75rem" rounded="var(--radius-md)" />
      </div>
    </div>

    <!-- 加载失败空态 -->
    <BaseEmpty
      v-else-if="showListError"
      :icon="AlertCircle"
      title="表情映射加载失败"
      :description="crud.error.value ?? '无法读取表情映射。'"
      action-text="重新加载"
      :action-icon="RefreshCw"
      @action="onRetry"
    />

    <!-- 搜索空结果 -->
    <BaseEmpty
      v-else-if="showSearchEmpty"
      :icon="Search"
      title="未找到匹配映射"
      :description="`没有占位符或表情包含「${crud.search.value}」。`"
      action-text="清除搜索"
      :action-icon="XIcon"
      @action="clearSearch"
    />

    <!-- 列表空态 -->
    <BaseEmpty
      v-else-if="showListEmpty"
      :icon="Inbox"
      title="暂无表情映射"
      description="月月还没有任何表情占位符映射，点击新建开始添加。"
      action-text="新建映射"
      :action-icon="Plus"
      @action="openCreate"
    />

    <!-- 映射列表 -->
    <ul v-else class="mapping-list" role="list">
      <li
        v-for="m in crud.items.value"
        :key="m.placeholder"
        class="mapping-row"
        role="listitem"
      >
        <div class="mapping-row__main">
          <div class="mapping-row__head">
            <span class="mapping-row__placeholder font-display">{{ m.placeholder }}</span>
            <span v-if="(m.discord_emojis ?? []).length > 1" class="badge">
              {{ m.discord_emojis?.length }} 个
            </span>
          </div>
          <p class="mapping-row__emojis">
            <span
              v-for="(e, i) in (m.discord_emojis ?? [])"
              :key="i"
              class="mapping-row__emoji"
            >{{ e }}</span>
          </p>
        </div>
        <div class="mapping-row__actions">
          <BaseButton variant="ghost" size="sm" :icon="Pencil" @click="openEdit(m)">编辑</BaseButton>
          <BaseButton variant="danger" size="sm" :icon="Trash2" @click="requestDelete(m)">删除</BaseButton>
        </div>
      </li>
    </ul>

    <!-- 分页 -->
    <nav v-if="totalPages > 1 && !crud.isEmpty.value" class="pagination" aria-label="分页">
      <BaseButton
        variant="secondary"
        size="sm"
        :disabled="crud.page.value <= 1 || crud.loading.value"
        @click="crud.goToPage(crud.page.value - 1)"
      >
        上一页
      </BaseButton>
      <span class="pagination__info">
        第 {{ crud.page.value }} / {{ totalPages }} 页 · {{ showingFrom }}-{{ showingTo }} / 共 {{ crud.total.value }} 条
      </span>
      <BaseButton
        variant="secondary"
        size="sm"
        :disabled="crud.page.value >= totalPages || crud.loading.value"
        @click="crud.goToPage(crud.page.value + 1)"
      >
        下一页
      </BaseButton>
    </nav>

    <!-- 活动阵营表情（只读） -->
    <FactionEmojiPanel
      :mappings="fullConfig?.faction_mappings"
      :loading="configLoading"
      :error="configError"
      @refresh="onRetry"
    />

    <!-- ===== 编辑/新建 Modal ===== -->
    <BaseModal
      :model-value="editorOpen"
      :title="editorTitle"
      size="md"
      @update:model-value="onModalUpdate"
    >
      <div class="editor">
        <BaseInput
          :model-value="form.placeholder"
          label="占位符"
          type="text"
          placeholder="如 <微笑>"
          :error="formErrors['placeholder']"
          :disabled="saving || isEdit"
          required
          :list="availablePlaceholders.length ? 'emoji-placeholders' : undefined"
          @update:model-value="(v) => { form.placeholder = v; markDirty(); }"
        />
        <datalist v-if="availablePlaceholders.length" id="emoji-placeholders">
          <option v-for="p in availablePlaceholders" :key="p" :value="p" />
        </datalist>
        <div class="field">
          <label class="field__label font-display">
            Discord 表情<span class="field__req" aria-hidden="true">*</span>
          </label>
          <textarea
            v-model="form.emojisText"
            class="field__textarea"
            rows="4"
            aria-label="Discord 表情"
            placeholder="每行一个，或用逗号分隔&#10;如 &lt:a:123&gt;,&lt:smile:456&gt;"
            :disabled="saving"
            :aria-invalid="!!formErrors['discord_emojis']"
            @input="markDirty"
          />
          <p v-if="formErrors['discord_emojis']" class="field__error" role="alert">
            {{ formErrors['discord_emojis'] }}
          </p>
          <p v-else class="field__hint">
            格式 &lt;a?:名称:ID&gt;，支持多个表情（按顺序随机/依次替换）。编辑模式下占位符不可修改。
          </p>
        </div>
      </div>
      <template #footer>
        <BaseButton variant="ghost" size="md" :disabled="saving" @click="requestClose">取消</BaseButton>
        <BaseButton variant="primary" size="md" :loading="saving" :disabled="saving" @click="saveMapping">
          {{ isEdit ? '保存修改' : '创建映射' }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- ===== 删除确认 ===== -->
    <BaseConfirmDialog
      v-model="deleteOpen"
      title="删除表情映射"
      :message="
        deleteTarget
          ? `将删除占位符「${deleteTarget.placeholder}」的映射，此操作不可撤销。`
          : '将删除该表情映射，此操作不可撤销。'
      "
      confirm-text="删除"
      variant="danger"
      @confirm="confirmDelete"
    />

    <!-- ===== 编辑器未保存丢弃确认 ===== -->
    <BaseConfirmDialog
      v-model="discardOpen"
      title="放弃未保存的修改？"
      message="当前编辑的内容尚未保存，关闭将丢弃这些修改。"
      confirm-text="放弃"
      variant="danger"
      @confirm="confirmDiscard"
      @cancel="cancelDiscard"
    />
  </div>
</template>

<style scoped>
.view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
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

/* ===== 映射列表 ===== */
.mapping-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  list-style: none;
  margin: 0;
  padding: 0;
}
.mapping-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-micro) var(--ease-out-quart),
    background-color var(--dur-micro) var(--ease-out-quart);
}
.mapping-row:hover {
  border-color: var(--border-strong);
  background: var(--bg-surface-2);
}
.mapping-row:active {
  background: var(--bg-inset);
}
.mapping-row--skeleton {
  align-items: center;
  pointer-events: none;
}
.mapping-row__main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.mapping-row__head {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.mapping-row__placeholder {
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--accent);
  word-break: break-all;
}
.mapping-row__emojis {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin: 0;
}
.mapping-row__emoji {
  font-size: var(--text-sm);
  color: var(--text-secondary);
  font-family: var(--font-sans);
  word-break: break-all;
}
.mapping-row__actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--space-1);
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

/* ===== 编辑器表单 ===== */
.editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.field__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.field__req {
  color: var(--accent);
  margin-left: 0.25ch;
}
.field__textarea {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: var(--text-base);
  line-height: var(--lh-relaxed);
  resize: vertical;
  outline: none;
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.field__textarea:hover {
  border-color: var(--border-strong);
}
.field__textarea:focus-visible {
  border-color: var(--accent);
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.field__textarea:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.field__textarea::placeholder {
  color: var(--text-placeholder);
}
.field__error {
  font-size: var(--text-xs);
  color: var(--danger);
}
.field__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

/* ===== 移动端 ===== */
@media (max-width: 768px) {
  .toolbar {
    flex-direction: column;
    align-items: stretch;
  }
  .toolbar__search {
    max-width: none;
  }
  .mapping-row {
    flex-direction: column;
    align-items: stretch;
  }
  .mapping-row__actions {
    justify-content: flex-end;
  }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .mapping-row,
  .search-clear,
  .field__textarea {
    transition: none;
  }
}
</style>
