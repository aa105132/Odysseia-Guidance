<script setup lang="ts">
/* LoraManager — ComfyUI LoRA 管理子组件。
 * 列表来源是 GET config 的 available_lora_names（string[]，非分页端点，故不走 useCrudList）。
 * 无独立删除端点（recon 确认），LoRA 文件由磁盘管理；本组件提供：
 *   1) 已发现 LoRA 列表（chips，只读，点击填入快速选择）
 *   2) 默认 LoRA + 强度（绑定表单 default_lora / default_lora_strength）
 *   3) 快速 LoRA：选择 + 强度 → 生成 <lora:name:0.80> token 追加到正/负向固定提示词；可一键设为默认
 *   4) 下载表单（ComfyUI-Manager 队列安装，成功后 emit refresh 由父刷新 config）
 * 8 状态：默认/hover/focus-visible/disabled/loading(下载中)/空态/错误 toast/empty。 */
import { computed, ref, watch } from 'vue';
import { Download, Plus, Star, Wand2 } from 'lucide-vue-next';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseSelect from '@/components/ui/BaseSelect.vue';
import { useToastStore } from '@/stores/toast';
import { ApiError } from '@/api/client';
import { downloadLora } from '@/api/domains/comfyui';

const props = withDefaults(
  defineProps<{
    availableLoras: string[];
    defaultLora?: string;
    defaultLoraStrength?: number;
    fixedPositivePrompt?: string;
    fixedNegativePrompt?: string;
    disabled?: boolean;
  }>(),
  { disabled: false },
);

const emit = defineEmits<{
  (e: 'update:defaultLora', v: string): void;
  (e: 'update:defaultLoraStrength', v: number): void;
  (e: 'update:fixedPositivePrompt', v: string): void;
  (e: 'update:fixedNegativePrompt', v: string): void;
  (e: 'refresh'): void;
}>();

const toast = useToastStore();

// 默认 LoRA 选项（含"未指定"空项）
const defaultLoraOptions = computed(() => [
  { value: '', label: '（未指定）' },
  ...props.availableLoras.map((n) => ({ value: n, label: n })),
]);

// 快速 LoRA（独立于默认；用于生成 token 追加到提示词）
const quickLoraName = ref('');
const quickLoraStrength = ref(0.8);

watch(
  () => props.availableLoras,
  (list) => {
    // 默认快速选择首项，便于直接追加
    if (!quickLoraName.value && list.length > 0) quickLoraName.value = list[0];
  },
  { immediate: true },
);

const selectedValid = computed(() => !!quickLoraName.value);

// 下载表单
const dlUrl = ref('');
const dlFilename = ref('');
const dlSavePath = ref('');
const dlBusy = ref(false);

function buildToken(name: string, strength: number): string {
  const s = Number.isFinite(strength) ? strength.toFixed(2) : '0.80';
  return `<lora:${name}:${s}>`;
}

function setAsDefault(): void {
  if (!selectedValid.value) {
    toast.push({ type: 'warning', message: '请先选择一个 LoRA' });
    return;
  }
  emit('update:defaultLora', quickLoraName.value);
  toast.push({ type: 'success', message: `已设为默认 LoRA：${quickLoraName.value}`, duration: 2500 });
}

function appendToPrompt(target: 'positive' | 'negative'): void {
  if (!selectedValid.value) {
    toast.push({ type: 'warning', message: '请先选择一个 LoRA' });
    return;
  }
  const token = buildToken(quickLoraName.value, quickLoraStrength.value);
  if (target === 'positive') {
    const base = props.fixedPositivePrompt ?? '';
    if (base.includes(token)) {
      toast.push({ type: 'info', message: '该 LoRA token 已在正向提示词中', duration: 2000 });
      return;
    }
    emit('update:fixedPositivePrompt', base ? `${base}\n${token}` : token);
  } else {
    const base = props.fixedNegativePrompt ?? '';
    if (base.includes(token)) {
      toast.push({ type: 'info', message: '该 LoRA token 已在负向提示词中', duration: 2000 });
      return;
    }
    emit('update:fixedNegativePrompt', base ? `${base}\n${token}` : token);
  }
}

function setDefaultStrength(raw: string): void {
  if (raw === '') {
    emit('update:defaultLoraStrength', 0);
    return;
  }
  const n = Number(raw);
  if (!Number.isNaN(n)) emit('update:defaultLoraStrength', n);
}

function setQuickStrength(raw: string): void {
  if (raw === '') {
    quickLoraStrength.value = 0;
    return;
  }
  const n = Number(raw);
  if (!Number.isNaN(n)) quickLoraStrength.value = n;
}

async function onDownload(): Promise<void> {
  if (dlBusy.value) return;
  const url = dlUrl.value.trim();
  if (!url) {
    toast.push({ type: 'warning', message: '请填写 LoRA 下载地址' });
    return;
  }
  dlBusy.value = true;
  try {
    const res = await downloadLora({
      url,
      filename: dlFilename.value.trim() || undefined,
      save_path: dlSavePath.value.trim() || undefined,
    });
    if (res.success) {
      toast.push({
        type: 'success',
        message:
          res.message ??
          (res.fallback_mode ? `已回退直链保存：${res.saved_filename ?? ''}` : 'LoRA 下载任务已提交'),
      });
      if (res.queue_start_warning) {
        toast.push({ type: 'warning', message: res.queue_start_warning });
      }
      dlUrl.value = '';
      dlFilename.value = '';
      dlSavePath.value = '';
      // 下载为队列安装，非即时；通知父视图刷新 config 以读取最新 available_lora_names
      emit('refresh');
    } else {
      toast.push({ type: 'error', message: res.error ?? '下载 LoRA 失败' });
    }
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : e instanceof Error ? e.message : '下载 LoRA 失败';
    toast.push({ type: 'error', message: msg });
  } finally {
    dlBusy.value = false;
  }
}
</script>

<template>
  <div class="lora-manager">
    <!-- 已发现 LoRA 列表（只读，磁盘管理；无删除端点） -->
    <div class="lora-manager__section">
      <div class="lora-manager__head">
        <label class="lora-manager__label font-display">已发现 LoRA</label>
        <span class="lora-manager__count">{{ availableLoras.length }} 个</span>
      </div>
      <BaseEmpty
        v-if="availableLoras.length === 0"
        :icon="Wand2"
        title="暂无 LoRA"
        description="未在 ComfyUI 的 models/loras 目录发现文件。可通过下方下载表单新增，或于服务端磁盘放入后刷新。"
      />
      <div v-else class="lora-chips" role="list">
        <button
          v-for="name in availableLoras"
          :key="name"
          type="button"
          :class="['lora-chip', { 'is-active': name === quickLoraName, 'is-default': name === defaultLora }]"
          role="listitem"
          :disabled="disabled"
          :title="name === defaultLora ? `${name}（默认）` : name"
          @click="quickLoraName = name"
        >
          <Star v-if="name === defaultLora" :size="12" aria-hidden="true" class="lora-chip__star" />
          <span class="lora-chip__name">{{ name }}</span>
        </button>
      </div>
    </div>

    <!-- 默认 LoRA（绑定表单 default_lora / default_lora_strength） -->
    <div class="lora-manager__section">
      <label class="lora-manager__label font-display">默认 LoRA</label>
      <div class="lora-default">
        <BaseSelect
          :model-value="defaultLora ?? ''"
          :options="defaultLoraOptions"
          label="默认 LoRA"
          placeholder="（未指定）"
          :disabled="disabled"
          @update:model-value="(v) => emit('update:defaultLora', String(v))"
        />
        <BaseInput
          :model-value="defaultLoraStrength ?? ''"
          label="默认强度"
          type="number"
          placeholder="0.80"
          hint="≥0，默认 0.80"
          :disabled="disabled"
          @update:model-value="setDefaultStrength"
        />
      </div>
    </div>

    <!-- 快速 LoRA：生成 token 追加到固定提示词 -->
    <div class="lora-manager__section">
      <label class="lora-manager__label font-display">快速追加到提示词</label>
      <p class="lora-manager__sub">选择 LoRA 与强度，生成 <code>&lt;lora:name:0.80&gt;</code> token 去重追加。</p>
      <div class="lora-quick">
        <BaseSelect
          :model-value="quickLoraName"
          :options="defaultLoraOptions"
          label="选择 LoRA"
          placeholder="（未选择）"
          :disabled="disabled"
          @update:model-value="(v) => (quickLoraName = String(v))"
        />
        <BaseInput
          :model-value="quickLoraStrength"
          label="强度"
          type="number"
          placeholder="0.80"
          hint="0–2"
          :disabled="disabled"
          @update:model-value="setQuickStrength"
        />
        <div class="lora-quick__btns">
          <BaseButton
            variant="secondary"
            size="md"
            :icon="Star"
            :disabled="disabled || !selectedValid"
            @click="setAsDefault"
          >
            设为默认
          </BaseButton>
          <BaseButton
            variant="ghost"
            size="md"
            :icon="Plus"
            :disabled="disabled || !selectedValid"
            @click="appendToPrompt('positive')"
          >
            追加到正向
          </BaseButton>
          <BaseButton
            variant="ghost"
            size="md"
            :icon="Plus"
            :disabled="disabled || !selectedValid"
            @click="appendToPrompt('negative')"
          >
            追加到负向
          </BaseButton>
        </div>
      </div>
    </div>

    <!-- 下载表单（ComfyUI-Manager 队列安装） -->
    <div class="lora-manager__section">
      <label class="lora-manager__label font-display">下载 LoRA</label>
      <p class="lora-manager__sub">通过 ComfyUI-Manager 队列安装；下载后需刷新配置才能在列表中看到。</p>
      <div class="lora-dl">
        <BaseInput
          :model-value="dlUrl"
          label="下载地址"
          type="text"
          placeholder="https://civitai.com/... 或直链 .safetensors"
          required
          :disabled="dlBusy || disabled"
          @update:model-value="dlUrl = $event"
        />
        <BaseInput
          :model-value="dlFilename"
          label="文件名（可选）"
          type="text"
          placeholder="如 my_lora.safetensors"
          :disabled="dlBusy || disabled"
          @update:model-value="dlFilename = $event"
        />
        <BaseInput
          :model-value="dlSavePath"
          label="保存子路径（可选）"
          type="text"
          placeholder="如 models/loras/my_folder"
          :disabled="dlBusy || disabled"
          @update:model-value="dlSavePath = $event"
        />
        <BaseButton
          variant="primary"
          size="md"
          :icon="Download"
          :loading="dlBusy"
          :disabled="disabled"
          class="lora-dl__submit"
          @click="onDownload"
        >
          开始下载
        </BaseButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lora-manager {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.lora-manager__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.lora-manager__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.lora-manager__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}

.lora-manager__count {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

.lora-manager__sub {
  font-size: var(--text-xs);
  color: var(--text-muted);
}
.lora-manager__sub code {
  padding: 0 0.25ch;
  background: var(--bg-inset);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-family: var(--font-sans);
}

/* LoRA chips */
.lora-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}

.lora-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: var(--space-1) var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-surface);
  color: var(--text-secondary);
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  cursor: pointer;
  max-width: 100%;
  transition: background-color var(--dur-micro) var(--ease-out-quart),
    border-color var(--dur-micro) var(--ease-out-quart),
    color var(--dur-micro) var(--ease-out-quart);
}
.lora-chip:hover {
  background: var(--bg-surface-2);
  border-color: var(--border-strong);
  color: var(--text-primary);
}
.lora-chip.is-active {
  background: var(--accent-subtle);
  border-color: var(--accent);
  color: var(--accent);
  font-weight: var(--fw-medium);
}
.lora-chip.is-active:hover {
  border-color: var(--accent-hover);
  color: var(--accent-hover);
}
.lora-chip:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.lora-chip:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.lora-chip__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lora-chip__star {
  color: var(--accent);
  flex: none;
}

/* 默认 LoRA */
.lora-default {
  display: grid;
  grid-template-columns: 1fr 8rem;
  gap: var(--space-3);
  align-items: end;
}

/* 快速 LoRA */
.lora-quick {
  display: grid;
  grid-template-columns: 1fr 8rem;
  gap: var(--space-3);
  align-items: end;
}
.lora-quick__btns {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

/* 下载表单 */
.lora-dl {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  align-items: end;
}
.lora-dl__submit {
  grid-column: 1 / -1;
  justify-self: start;
}

@media (max-width: 768px) {
  .lora-default,
  .lora-quick,
  .lora-dl {
    grid-template-columns: 1fr;
  }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .lora-chip {
    transition: none;
  }
}
</style>
