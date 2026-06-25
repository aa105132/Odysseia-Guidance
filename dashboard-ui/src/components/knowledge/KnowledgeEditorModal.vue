<script setup lang="ts">
/* KnowledgeEditorModal — 知识文档新建/编辑/查看弹窗。
 * doc=null + mode='create' 新建；doc 非 null + mode='edit' 编辑；mode='view' 只读。
 * 编辑/查看时挂载即调 getDocument 拉全文（列表项仅含 preview）。category 创建后不可改，
 * 编辑/查看态置只读。保存：新建走 createDocument，编辑走 updateDocument（仅 title/content，
 * 后端 KnowledgeDocumentUpdate 不含 category）。成功 toast（含后端嵌入脚本提示）+ emit saved + 关闭。
 * 8 状态：按钮默认/hover/active/focus-visible/disabled + loading + 字段 disabled/readonly + 错误 inline。 */
import { computed, ref, watch } from 'vue';
import { BookPlus, Save, Edit3 } from 'lucide-vue-next';
import BaseModal from '@/components/ui/BaseModal.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import { useToastStore } from '@/stores/toast';
import { ApiError } from '@/api/client';
import { createDocument, updateDocument, getDocument } from '@/api/domains/knowledge';
import type { KnowledgeDoc, KnowledgeDocCreate, KnowledgeDocUpdate } from '@/api/models';

type ModalMode = 'create' | 'edit' | 'view';

const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    doc?: KnowledgeDoc | null;
    mode?: ModalMode;
  }>(),
  { doc: null, mode: 'create' },
);

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void;
  (e: 'saved'): void;
}>();

const toast = useToastStore();

// 当前模式：view 可内部切到 edit（不关弹窗）
const currentMode = ref<ModalMode>('create');
const fetching = ref(false);
const saving = ref(false);
const title = ref('');
const content = ref('');
const category = ref('');
const errors = ref<{ title?: string; content?: string }>({});

const isCreate = computed(() => currentMode.value === 'create');
const isView = computed(() => currentMode.value === 'view');
const readonly = computed(() => currentMode.value === 'view');
const modalTitle = computed(() => (isCreate.value ? '新建文档' : isView.value ? '查看文档' : '编辑文档'));

function resetForm(): void {
  title.value = '';
  content.value = '';
  category.value = '';
  errors.value = {};
}

function fillFromDoc(d: KnowledgeDoc): void {
  title.value = d.title ?? '';
  content.value = ''; // 全文需拉详情
  category.value = d.category ?? '';
}

// 编辑/查看：拉详情补全正文与分类（详情 category 在 metadata.category）
async function fetchDetail(id: number): Promise<void> {
  fetching.value = true;
  try {
    const detail = await getDocument(id);
    content.value = detail.content ?? '';
    if (detail.metadata && typeof detail.metadata === 'object') {
      const cat = (detail.metadata as Record<string, unknown>).category;
      if (typeof cat === 'string') category.value = cat;
    }
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : e instanceof Error ? e.message : '加载文档失败';
    toast.push({ type: 'error', message: msg, title: '加载失败' });
    close();
  } finally {
    fetching.value = false;
  }
}

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return;
    currentMode.value = props.mode;
    resetForm();
    if (props.mode !== 'create' && props.doc?.id != null) {
      fillFromDoc(props.doc);
      await fetchDetail(props.doc.id);
    }
  },
);

function close(): void {
  emit('update:modelValue', false);
}

function switchToEdit(): void {
  currentMode.value = 'edit';
}

function validate(): boolean {
  const e: { title?: string; content?: string } = {};
  if (!title.value.trim()) e.title = '标题不能为空';
  if (!content.value.trim()) e.content = '正文不能为空';
  errors.value = e;
  return !e.title && !e.content;
}

async function onSave(): Promise<void> {
  if (!validate()) return;
  saving.value = true;
  try {
    if (isCreate.value) {
      const body: KnowledgeDocCreate = {
        title: title.value.trim(),
        content: content.value,
        category: category.value.trim() || null,
      };
      const res = await createDocument(body);
      // 后端 message 提示需运行嵌入脚本，延长 toast 时长以便阅读
      toast.push({ type: 'success', message: res.message ?? '创建成功', duration: 6000 });
      emit('saved');
      close();
    } else if (props.doc?.id != null) {
      const body: KnowledgeDocUpdate = {
        title: title.value.trim(),
        content: content.value,
      };
      const res = await updateDocument(props.doc.id, body);
      toast.push({ type: 'success', message: res.message ?? '已保存', duration: 6000 });
      emit('saved');
      close();
    }
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : e instanceof Error ? e.message : '保存失败';
    toast.push({ type: 'error', message: msg, title: '保存失败' });
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <BaseModal
    :model-value="modelValue"
    :title="modalTitle"
    size="lg"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <!-- 加载详情骨架 -->
    <div v-if="fetching" class="modal-skeleton" aria-busy="true">
      <BaseSkeleton height="2.75rem" />
      <BaseSkeleton height="16rem" />
      <BaseSkeleton height="2.75rem" />
    </div>

    <form v-else class="doc-form" @submit.prevent="onSave">
      <BaseInput
        v-model="title"
        label="标题"
        placeholder="文档标题"
        :required="!readonly"
        :disabled="readonly"
        :error="errors.title"
      />

      <div class="content-field" :class="{ 'is-readonly': readonly, 'has-error': !!errors.content }">
        <label class="content-field__label font-display">
          正文<span v-if="!readonly" class="content-field__req" aria-hidden="true">*</span>
        </label>
        <textarea
          v-model="content"
          class="content-field__area"
          placeholder="文档正文（支持长文本）"
          :readonly="readonly"
          spellcheck="false"
          aria-label="正文"
          :aria-invalid="!!errors.content"
        />
        <p v-if="errors.content" class="content-field__error" role="alert">{{ errors.content }}</p>
        <p v-else class="content-field__hint">创建/更新后需运行嵌入脚本生成向量分块</p>
      </div>

      <BaseInput
        v-model="category"
        label="分类"
        placeholder="可选，如 规则 / 设定 / 剧情"
        :disabled="!isCreate"
        :hint="!isCreate ? '创建后不可修改' : undefined"
      />
    </form>

    <template #footer>
      <template v-if="readonly">
        <BaseButton variant="ghost" size="md" @click="close">关闭</BaseButton>
        <BaseButton variant="primary" size="md" :icon="Edit3" @click="switchToEdit">编辑</BaseButton>
      </template>
      <template v-else>
        <BaseButton variant="ghost" size="md" :disabled="saving" @click="close">取消</BaseButton>
        <BaseButton
          variant="primary"
          size="md"
          :icon="isCreate ? BookPlus : Save"
          :loading="saving"
          :disabled="fetching"
          @click="onSave"
        >
          {{ isCreate ? '创建' : '保存' }}
        </BaseButton>
      </template>
    </template>
  </BaseModal>
</template>

<style scoped>
.modal-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.doc-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

/* 正文 textarea：与 BaseInput 的 field__control 同语言，凹陷底 + 琥珀 focus */
.content-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.content-field__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.content-field__req {
  color: var(--accent);
  margin-left: 0.25ch;
}
.content-field__area {
  width: 100%;
  min-height: 16rem;
  padding: var(--space-3) var(--space-4);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  line-height: var(--lh-relaxed);
  resize: vertical;
  outline: none;
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.content-field__area::placeholder {
  color: var(--text-placeholder);
}
.content-field__area:hover {
  border-color: var(--border-strong);
}
.content-field__area:focus-visible {
  border-color: var(--accent);
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.content-field.is-readonly .content-field__area {
  cursor: default;
}
.content-field.has-error .content-field__area {
  border-color: var(--danger);
}
.content-field.has-error .content-field__area:focus-visible {
  border-color: var(--danger);
  outline-color: var(--danger);
}
.content-field__error {
  font-size: var(--text-xs);
  color: var(--danger);
}
.content-field__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

@media (prefers-reduced-motion: reduce) {
  .content-field__area {
    transition: none;
  }
}
</style>
