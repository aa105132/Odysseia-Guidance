<script setup lang="ts">
/* NodeMappingEditor — ComfyUI 节点映射编辑器。
 * 双 JsonEditor：placeholder_mapping（参数键→占位符 token）、node_mapping（参数键→[node_id, field]）。
 * 三个自动按钮委托父视图执行（需工作流 JSON 上下文）：auto-parameterize(all/prompt_only)、auto-node-mapping。
 * 两个 reset：占位符恢复默认、节点映射清空。文本非法时上报 invalid，父视图据此拦截保存。
 * 8 状态：默认/hover/focus-visible/disabled/loading(autoBusy)/空态/错误(JSON 校验)/reduced-motion。 */
import { ref, watch } from 'vue';
import { ArrowRightLeft, Boxes, Eraser, RotateCcw, Sparkles, Wand2 } from 'lucide-vue-next';
import JsonEditor from '@/components/ui/JsonEditor.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import type { ComfyUINodeMapping } from '@/api/models';

const props = withDefaults(
  defineProps<{
    placeholderMapping: Record<string, string>;
    nodeMapping: ComfyUINodeMapping;
    disabled?: boolean;
    /** 自动操作进行中（父视图传入，禁用按钮 + spinner） */
    autoBusy?: boolean;
    /** 是否已有可分析的工作流 JSON（无则禁用自动按钮） */
    hasWorkflow?: boolean;
  }>(),
  { disabled: false, autoBusy: false, hasWorkflow: false },
);

const emit = defineEmits<{
  (e: 'update:placeholderMapping', v: Record<string, string>): void;
  (e: 'update:nodeMapping', v: ComfyUINodeMapping): void;
  (e: 'auto-parameterize', mode: 'all' | 'prompt_only'): void;
  (e: 'auto-node-mapping'): void;
  (e: 'invalid', invalid: boolean): void;
}>();

// 默认占位符映射（service L569-587 + SPA L6910）
const DEFAULT_PLACEHOLDER_MAPPING: Record<string, string> = {
  positive_prompt: '{{positive_prompt}}',
  negative_prompt: '{{negative_prompt}}',
  width: '{{width}}',
  height: '{{height}}',
  steps: '{{steps}}',
  cfg: '{{cfg}}',
  sampler: '{{sampler}}',
  scheduler: '{{scheduler}}',
  seed: '{{seed}}',
  lora: '{{lora}}',
  lora_strength: '{{lora_strength}}',
  model_name: '{{model_name}}',
  vae_name: '{{vae_name}}',
  clip_name: '{{clip_name}}',
  input_image: '{{input_image}}',
  reference_image: '{{reference_image}}',
  init_image: '{{init_image}}',
};

const placeholderText = ref(JSON.stringify(props.placeholderMapping ?? {}, null, 2));
const nodeText = ref(JSON.stringify(props.nodeMapping ?? {}, null, 2));
const placeholderInvalid = ref(false);
const nodeInvalid = ref(false);

function emitInvalid(): void {
  emit('invalid', placeholderInvalid.value || nodeInvalid.value);
}

/** 外部值变更时同步进编辑器（自动操作/重置/保存后回写），避免覆盖用户正在输入的合法内容 */
watch(
  () => props.placeholderMapping,
  (v) => {
    const normalized = JSON.stringify(v ?? {}, null, 2);
    try {
      if (JSON.stringify(JSON.parse(placeholderText.value), null, 2) === normalized) return;
    } catch {
      /* 当前文本非法，用外部值覆盖 */
    }
    placeholderText.value = normalized;
    placeholderInvalid.value = false;
    emitInvalid();
  },
  { deep: true },
);

watch(
  () => props.nodeMapping,
  (v) => {
    const normalized = JSON.stringify(v ?? {}, null, 2);
    try {
      if (JSON.stringify(JSON.parse(nodeText.value), null, 2) === normalized) return;
    } catch {
      /* 当前文本非法，用外部值覆盖 */
    }
    nodeText.value = normalized;
    nodeInvalid.value = false;
    emitInvalid();
  },
  { deep: true },
);

function onPlaceholderInput(v: string): void {
  placeholderText.value = v;
  const trimmed = v.trim();
  if (!trimmed) {
    placeholderInvalid.value = false;
    emitInvalid();
    return;
  }
  try {
    const parsed = JSON.parse(v);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      placeholderInvalid.value = false;
      emit('update:placeholderMapping', parsed as Record<string, string>);
    } else {
      placeholderInvalid.value = true;
    }
  } catch {
    placeholderInvalid.value = true;
  }
  emitInvalid();
}

function onNodeInput(v: string): void {
  nodeText.value = v;
  const trimmed = v.trim();
  if (!trimmed) {
    nodeInvalid.value = false;
    emit('update:nodeMapping', {});
    emitInvalid();
    return;
  }
  try {
    const parsed = JSON.parse(v);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      nodeInvalid.value = false;
      emit('update:nodeMapping', parsed as ComfyUINodeMapping);
    } else {
      nodeInvalid.value = true;
    }
  } catch {
    nodeInvalid.value = true;
  }
  emitInvalid();
}

function resetPlaceholder(): void {
  placeholderText.value = JSON.stringify(DEFAULT_PLACEHOLDER_MAPPING, null, 2);
  placeholderInvalid.value = false;
  emit('update:placeholderMapping', { ...DEFAULT_PLACEHOLDER_MAPPING });
  emitInvalid();
}

function clearNodeMapping(): void {
  nodeText.value = '{}';
  nodeInvalid.value = false;
  emit('update:nodeMapping', {});
  emitInvalid();
}

const autoDisabled = (): boolean => props.disabled || props.autoBusy || !props.hasWorkflow;
</script>

<template>
  <div class="mapping-editor">
    <div class="mapping-editor__actions">
      <BaseButton
        variant="secondary"
        size="sm"
        :icon="Wand2"
        :loading="autoBusy"
        :disabled="autoDisabled()"
        @click="emit('auto-parameterize', 'all')"
      >
        自动参数化（全部）
      </BaseButton>
      <BaseButton
        variant="secondary"
        size="sm"
        :icon="Sparkles"
        :loading="autoBusy"
        :disabled="autoDisabled()"
        @click="emit('auto-parameterize', 'prompt_only')"
      >
        自动参数化（仅提示词）
      </BaseButton>
      <BaseButton
        variant="secondary"
        size="sm"
        :icon="Boxes"
        :loading="autoBusy"
        :disabled="autoDisabled()"
        @click="emit('auto-node-mapping')"
      >
        自动节点映射
      </BaseButton>
      <BaseButton
        variant="ghost"
        size="sm"
        :icon="RotateCcw"
        :disabled="disabled"
        @click="resetPlaceholder"
      >
        恢复默认占位符
      </BaseButton>
      <BaseButton
        variant="ghost"
        size="sm"
        :icon="Eraser"
        :disabled="disabled"
        @click="clearNodeMapping"
      >
        清空节点映射
      </BaseButton>
    </div>
    <p v-if="!hasWorkflow" class="mapping-editor__hint">
      <ArrowRightLeft :size="14" aria-hidden="true" />
      需先在工作流区载入或粘贴工作流 JSON，才能使用自动识别。
    </p>

    <div class="mapping-editor__grid">
      <div class="mapping-editor__cell">
        <label class="mapping-editor__label font-display">占位符映射</label>
        <p class="mapping-editor__sub">参数键 → 占位符 token（如 <code v-text="'{{width}}'"></code>）</p>
        <JsonEditor
          :model-value="placeholderText"
          @update:model-value="onPlaceholderInput"
        />
      </div>
      <div class="mapping-editor__cell">
        <label class="mapping-editor__label font-display">节点映射</label>
        <p class="mapping-editor__sub">参数键 → [节点 ID, 字段名]（如 <code>["9", "text"]</code>）</p>
        <JsonEditor
          :model-value="nodeText"
          @update:model-value="onNodeInput"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.mapping-editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.mapping-editor__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.mapping-editor__hint {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.mapping-editor__grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-4);
}

.mapping-editor__cell {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.mapping-editor__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}

.mapping-editor__sub {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.mapping-editor__sub code {
  padding: 0 0.25ch;
  background: var(--bg-inset);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-family: var(--font-sans);
}

@media (max-width: 768px) {
  .mapping-editor__grid {
    grid-template-columns: 1fr;
  }
}
</style>
