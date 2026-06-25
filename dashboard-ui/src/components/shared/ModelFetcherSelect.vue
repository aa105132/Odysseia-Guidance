<script setup lang="ts">
/* ModelFetcherSelect — 模型懒拉取复用组件
 * 懒加载：挂载不自动拉，点"加载可用模型"才调父传入的 fetchModels。
 * 选择后回填 modelValue。当前值不在列表时追加"当前: xxx"兜底选项。
 * 8 状态：默认/hover/active/focus-visible/disabled + loading + empty + error。
 * 解耦：fetchModels 由父注入，本组件不关心具体端点（/api/models/list 等）。 */
import { ref, computed } from 'vue';
import { RefreshCw } from 'lucide-vue-next';
import BaseSelect from '@/components/ui/BaseSelect.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import { useToastStore } from '@/stores/toast';

interface Props {
  /** 当前选中模型（v-model） */
  modelValue: string;
  /** 由父注入的拉取函数，返回模型名列表 */
  fetchModels: () => Promise<string[]>;
  label?: string;
  disabled?: boolean;
  hint?: string;
  /** 空列表时 BaseEmpty 的描述文案，可覆盖默认 */
  emptyDescription?: string;
}

const props = withDefaults(defineProps<Props>(), {
  disabled: false,
  emptyDescription: '未获取到任何可用模型，请检查 API 配置后重试。',
});

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void;
}>();

const toast = useToastStore();

const loaded = ref(false);
const loading = ref(false);
const models = ref<string[]>([]);
const error = ref<string | null>(null);

// 已成功加载但列表为空（且无错误）→ 显示 BaseEmpty
const loadedEmpty = computed(
  () => loaded.value && models.value.length === 0 && !error.value,
);

// 选项：当前值不在列表则追加兜底项置顶
const selectOptions = computed<{ value: string; label: string }[]>(() => {
  const opts: { value: string; label: string }[] = [];
  const cur = (props.modelValue ?? '').trim();
  if (cur && !models.value.includes(cur)) {
    opts.push({ value: cur, label: `当前: ${cur}` });
  }
  for (const m of models.value) opts.push({ value: m, label: m });
  return opts;
});

const buttonLabel = computed(() => {
  if (error.value) return '重试';
  return loaded.value ? '重新加载' : '加载可用模型';
});

function onSelect(v: string | number): void {
  emit('update:modelValue', String(v));
}

async function load(): Promise<void> {
  if (loading.value || props.disabled) return;
  loading.value = true;
  error.value = null;
  try {
    const list = await props.fetchModels();
    models.value = Array.isArray(list)
      ? list.filter((m): m is string => typeof m === 'string' && m.length > 0)
      : [];
    loaded.value = true;
    if (models.value.length === 0) {
      toast.push({ type: 'warning', message: '未找到可用模型' });
    } else {
      toast.push({ type: 'success', message: `已加载 ${models.value.length} 个可用模型` });
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : '加载模型列表失败';
    error.value = msg;
    toast.push({ type: 'error', message: msg });
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="model-fetcher" role="group" :aria-label="label ?? '模型选择'">
    <label v-if="label" class="model-fetcher__label font-display">{{ label }}</label>

    <!-- 空列表：BaseEmpty 兜底（modelValue 仍保留于组件状态，不丢失） -->
    <BaseEmpty
      v-if="loadedEmpty"
      :icon="RefreshCw"
      title="暂无可用模型"
      :description="emptyDescription"
      action-text="重新加载"
      :action-icon="RefreshCw"
      @action="load"
    />

    <!-- 默认 / 已加载有模型 / 错误：select + 加载按钮 -->
    <div v-else class="model-fetcher__row">
      <BaseSelect
        :model-value="modelValue"
        :options="selectOptions"
        :disabled="disabled || loading"
        :placeholder="modelValue ? undefined : '请选择模型'"
        :error="error ?? undefined"
        class="model-fetcher__select"
        @update:model-value="onSelect"
      />
      <BaseButton
        variant="secondary"
        size="md"
        :loading="loading"
        :disabled="disabled"
        :icon="RefreshCw"
        class="model-fetcher__btn"
        :aria-label="buttonLabel"
        @click="load"
      >
        {{ buttonLabel }}
      </BaseButton>
    </div>

    <p v-if="error" class="model-fetcher__error" role="alert">{{ error }}</p>
    <p v-else-if="hint && !error" class="model-fetcher__hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.model-fetcher {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.model-fetcher__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}

.model-fetcher__row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
}

.model-fetcher__select {
  flex: 1 1 auto;
  min-width: 0;
}

.model-fetcher__btn {
  flex: 0 0 auto;
  margin-top: 0;
  /* 与 select label 对齐：select 无 label 时按钮顶对齐输入框 */
  align-self: stretch;
}

.model-fetcher__error {
  font-size: var(--text-xs);
  color: var(--danger);
}

.model-fetcher__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

/* focus-visible 琥珀环：BaseSelect/BaseButton 自带，此处仅补容器内联动 */
.model-fetcher:focus-within .model-fetcher__btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* prefers-reduced-motion：停用按钮 spinner 旋转 */
@media (prefers-reduced-motion: reduce) {
  .model-fetcher :deep(.btn__spinner) {
    animation: none;
  }
}
</style>
