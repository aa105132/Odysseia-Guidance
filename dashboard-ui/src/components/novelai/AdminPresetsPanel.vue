<script setup lang="ts">
/* AdminPresetsPanel — NovelAI 管理员画师串预设 CRUD。
 * 后端 admin-presets：GET 列表（{presets,total}，无分页/搜索）、POST upsert-by-name、
 * PUT update-by-id、DELETE。列表量小，采用 useCrudList + 客户端过滤/分页：
 * fetchList 拉全量后按 q 过滤、按 page 切片，复用 composable 的加载/空/错误态与 debounce 搜索。
 * 新建走 POST（upsert by name，同名覆盖），编辑走 PUT/{id}；删除走 DELETE。
 * 8 状态：默认/hover/active/focus-visible/disabled（BaseButton/BaseInput）+ loading 骨架 +
 * empty（BaseEmpty）+ error 横幅+重试 + dirty 表单校验 + reduced-motion。 */
import { computed, ref } from 'vue';
import {
  AlertCircle,
  BookMarked,
  Inbox,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X as XIcon,
} from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseModal from '@/components/ui/BaseModal.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import { useCrudList } from '@/composables/useCrudList';
import {
  createAdminPreset,
  deleteAdminPreset,
  listAdminPresets,
  updateAdminPreset,
} from '@/api/domains/novelai';
import type { NovelAIAdminPreset, NovelAIAdminPresetUpsert } from '@/api/models';

// 客户端过滤 + 分页：后端 admin-presets 无分页/搜索参数
const crud = useCrudList<NovelAIAdminPreset>({
  fetchList: async ({ page, pageSize, q }) => {
    const res = await listAdminPresets();
    const all = res.presets ?? [];
    const needle = q.trim().toLowerCase();
    const filtered = needle
      ? all.filter(
          (p) =>
            (p.name ?? '').toLowerCase().includes(needle) ||
            (p.artist_string ?? '').toLowerCase().includes(needle),
        )
      : all;
    const start = (page - 1) * pageSize;
    const items = filtered.slice(start, start + pageSize);
    return { items, total: filtered.length, page, pageSize };
  },
  create: async (b: NovelAIAdminPresetUpsert) => {
    await createAdminPreset(b);
    // 后端 POST 仅回 {success,name}，不返回完整预设；reload 会拉取真实列表
    return { ...b, id: 0 } as NovelAIAdminPreset;
  },
  update: async (id, b: NovelAIAdminPresetUpsert) => {
    await updateAdminPreset(id as number, b);
    return { ...b, id: id as number } as NovelAIAdminPreset;
  },
  remove: async (id) => { await deleteAdminPreset(id as number); },
  pageSize: 8,
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

// ===== 编辑表单（新建/编辑复用）=====
const editorOpen = ref(false);
const editingId = ref<number | null>(null);
const form = ref<NovelAIAdminPresetUpsert>({ name: '', artist_string: '', negative_prompt: '' });
const formErrors = ref<Record<string, string>>({});
const saving = ref(false);
const isEdit = computed(() => editingId.value !== null);
const editorTitle = computed(() => (isEdit.value ? '编辑管理员预设' : '新建管理员预设'));

function openCreate(): void {
  editingId.value = null;
  form.value = { name: '', artist_string: '', negative_prompt: '' };
  formErrors.value = {};
  editorOpen.value = true;
}

function openEdit(preset: NovelAIAdminPreset): void {
  editingId.value = preset.id;
  form.value = {
    name: preset.name ?? '',
    artist_string: preset.artist_string ?? '',
    negative_prompt: preset.negative_prompt ?? '',
  };
  formErrors.value = {};
  editorOpen.value = true;
}

function closeEditor(): void {
  editorOpen.value = false;
}

// 客户端校验（对齐后端 api.py L3428-3436：name 非空≤100、artist_string 非空）
function validate(): boolean {
  const errs: Record<string, string> = {};
  const name = (form.value.name ?? '').trim();
  const artist = (form.value.artist_string ?? '').trim();
  if (!name) errs.name = '预设名称不能为空';
  else if (name.length > 100) errs.name = '名称长度不能超过 100';
  if (!artist) errs.artist_string = '画师串提示词不能为空';
  formErrors.value = errs;
  return Object.keys(errs).length === 0;
}

async function savePreset(): Promise<void> {
  if (saving.value) return;
  if (!validate()) return;
  saving.value = true;
  try {
    const body: NovelAIAdminPresetUpsert = {
      name: form.value.name.trim(),
      artist_string: form.value.artist_string.trim(),
      negative_prompt: (form.value.negative_prompt ?? '').trim(),
    };
    if (editingId.value !== null) {
      const ok = await crud.updateItem(editingId.value, body);
      if (ok) editorOpen.value = false;
    } else {
      const ok = await crud.createItem(body);
      if (ok) editorOpen.value = false;
    }
  } finally {
    saving.value = false;
  }
}

// ===== 删除确认 =====
const deleteTarget = ref<NovelAIAdminPreset | null>(null);
const deleteOpen = ref(false);
const deleting = ref(false);

function askDelete(preset: NovelAIAdminPreset): void {
  deleteTarget.value = preset;
  deleteOpen.value = true;
}

function cancelDelete(): void {
  deleteOpen.value = false;
  deleteTarget.value = null;
}

async function confirmDelete(): Promise<void> {
  if (!deleteTarget.value) return;
  const id = deleteTarget.value.id;
  deleting.value = true;
  try {
    const ok = await crud.removeItem(id);
    // 删的正在编辑则取消编辑（对齐 SPA L7596 行为）
    if (ok && editingId.value === id) {
      editorOpen.value = false;
      editingId.value = null;
    }
  } finally {
    deleting.value = false;
    deleteOpen.value = false;
    deleteTarget.value = null;
  }
}

// 文本截断（对齐 SPA：artist 截断 260、negative 截断 160）
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
        :icon="BookMarked"
        title="管理员画师串预设"
        subtitle="维护 NovelAI 画师串模板，供 /draw 调用"
      />
      <BaseButton variant="primary" size="md" :icon="Plus" @click="openCreate">
        新建预设
      </BaseButton>
    </div>

    <!-- 搜索栏 -->
    <div class="panel__search">
      <BaseInput
        :model-value="crud.search.value"
        type="text"
        placeholder="搜索名称或画师串"
        :disabled="crud.loading.value"
        @update:model-value="crud.setSearch"
      />
    </div>

    <!-- 加载骨架 -->
    <div v-if="crud.loading.value && crud.items.value.length === 0" class="preset-list" aria-busy="true" aria-live="polite">
      <div v-for="i in 4" :key="i" class="preset-row preset-row--skeleton">
        <BaseSkeleton width="30%" height="1.1rem" rounded="var(--radius-sm)" />
        <BaseSkeleton width="60%" height="0.9rem" rounded="var(--radius-sm)" />
        <BaseSkeleton width="4rem" height="1.75rem" rounded="var(--radius-md)" />
      </div>
    </div>

    <!-- 加载失败空状态 -->
    <BaseEmpty
      v-else-if="crud.error.value && crud.items.value.length === 0"
      :icon="AlertCircle"
      title="管理员预设加载失败"
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
      :description="`没有名称或画师串包含「${crud.search.value}」的管理员预设。`"
      action-text="清除搜索"
      :action-icon="XIcon"
      @action="clearSearch"
    />

    <!-- 空态 -->
    <BaseEmpty
      v-else-if="showListEmpty"
      :icon="Inbox"
      title="暂无管理员画师串"
      description="点击「新建预设」添加一个画师串模板。"
      action-text="新建预设"
      :action-icon="Plus"
      @action="openCreate"
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
            <span v-if="preset.negative_prompt" class="badge badge--muted">含负面</span>
          </div>
          <p class="preset-row__artist">{{ truncate(preset.artist_string, 260) }}</p>
          <p v-if="preset.negative_prompt" class="preset-row__negative">
            <span class="badge badge--negative">负面</span>
            <span class="preset-row__negative-text">{{ truncate(preset.negative_prompt, 160) }}</span>
          </p>
          <p v-if="preset.created_at" class="preset-row__meta">创建于 {{ preset.created_at }}</p>
        </div>
        <div class="preset-row__actions">
          <BaseButton variant="ghost" size="sm" :icon="Pencil" @click="openEdit(preset)">
            编辑
          </BaseButton>
          <BaseButton variant="danger" size="sm" :icon="Trash2" @click="askDelete(preset)">
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

    <!-- ===== 编辑/新建 Modal ===== -->
    <BaseModal
      :model-value="editorOpen"
      :title="editorTitle"
      size="md"
      @update:model-value="(v: boolean) => (editorOpen = v)"
    >
      <div class="editor">
        <BaseInput
          :model-value="form.name"
          label="预设名称"
          type="text"
          placeholder="如 治愈系风景"
          :error="formErrors['name']"
          :disabled="saving"
          required
          @update:model-value="form.name = $event"
        />
        <div class="field">
          <label class="field__label font-display">画师串提示词<span class="field__req" aria-hidden="true">*</span></label>
          <textarea
            v-model="form.artist_string"
            class="field__textarea"
            rows="4"
            placeholder="artist:xxx, ...（NovelAI 画师串标签）"
            :disabled="saving"
            :aria-invalid="!!formErrors['artist_string']"
          />
          <p v-if="formErrors['artist_string']" class="field__error" role="alert">{{ formErrors['artist_string'] }}</p>
          <p v-else class="field__hint">将追加到生成请求的正面提示词，同名保存会覆盖原预设</p>
        </div>
        <div class="field">
          <label class="field__label font-display">负面提示词（可选）</label>
          <textarea
            v-model="form.negative_prompt"
            class="field__textarea"
            rows="3"
            placeholder="留空使用默认负面"
            :disabled="saving"
          />
          <p class="field__hint">覆盖该预设专属的负面提示词，留空则不附加</p>
        </div>
      </div>
      <template #footer>
        <BaseButton variant="ghost" size="md" :disabled="saving" @click="closeEditor">取消</BaseButton>
        <BaseButton variant="primary" size="md" :loading="saving" :disabled="saving" @click="savePreset">
          {{ isEdit ? '保存修改' : '创建预设' }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- ===== 删除确认 ===== -->
    <BaseConfirmDialog
      v-model="deleteOpen"
      title="删除管理员预设？"
      :message="deleteTarget ? `将删除预设「${deleteTarget.name}」，此操作不可撤销。` : '将删除该预设，此操作不可撤销。'"
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

/* ===== 编辑器表单（textarea 复用 BaseInput 字段样式）===== */
.editor { display: flex; flex-direction: column; gap: var(--space-4); }
.field { display: flex; flex-direction: column; gap: var(--space-2); }
.field__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.field__req { color: var(--accent); margin-left: 0.25ch; }
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
.field__textarea:hover { border-color: var(--border-strong); }
.field__textarea:focus-visible { border-color: var(--accent); outline: 2px solid var(--accent); outline-offset: 2px; }
.field__textarea:disabled { cursor: not-allowed; opacity: 0.55; }
.field__textarea::placeholder { color: var(--text-placeholder); }
.field__error { font-size: var(--text-xs); color: var(--danger); }
.field__hint { font-size: var(--text-xs); color: var(--text-muted); }

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
  .field__textarea { transition: none; }
}
</style>
