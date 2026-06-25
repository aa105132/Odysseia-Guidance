<script setup lang="ts">
/* ComfyUIView — ComfyUI 设置：配置 + 工作流导入/查看/删除 + 节点映射 + LoRA + 测试连接。
 * GET/PUT /api/config/comfyui（配置读写，PUT 兼工作流导入）+ workflow-content/delete +
 * test-comfyui + auto-node-mapping + auto-parameterize-workflow。
 * useConfigForm 统一 load/save/validate/dirty/beforeunload；保存只送脏字段（PATCH 语义）。
 * 工作流导入走独立路径：workflow_json/filename/auto_detect_node_mapping 为写型字段，
 * GET 不回传，故不入 form，由独立 ref 持有，导入时直调 saveComfyUIConfig 后重拉配置。
 * 节点映射的"自动识别"由本视图执行（需工作流 JSON 上下文），结果写回 form 占位符/节点映射。 */
import { computed, inject, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import {
  AlertTriangle,
  ArrowRightLeft,
  Boxes,
  Eye,
  FileJson,
  Plug,
  RotateCw,
  Save,
  Server,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Upload,
  Wand2,
  Workflow,
} from 'lucide-vue-next';
import BaseSectionTitle from '@/components/ui/BaseSectionTitle.vue';
import BaseButton from '@/components/ui/BaseButton.vue';
import BaseInput from '@/components/ui/BaseInput.vue';
import BaseSelect from '@/components/ui/BaseSelect.vue';
import BaseSkeleton from '@/components/ui/BaseSkeleton.vue';
import BaseEmpty from '@/components/ui/BaseEmpty.vue';
import BaseConfirmDialog from '@/components/ui/BaseConfirmDialog.vue';
import ToggleSwitch from '@/components/ui/ToggleSwitch.vue';
import JsonEditor from '@/components/ui/JsonEditor.vue';
import NodeMappingEditor from '@/components/comfyui/NodeMappingEditor.vue';
import LoraManager from '@/components/comfyui/LoraManager.vue';
import { useConfigForm } from '@/composables/useConfigForm';
import { useToastStore } from '@/stores/toast';
import { ApiError } from '@/api/client';
import {
  autoNodeMapping,
  autoParameterizeWorkflow,
  deleteWorkflow,
  getComfyUIConfig,
  getWorkflowContent,
  saveComfyUIConfig,
  testComfyUI,
} from '@/api/domains/comfyui';
import type {
  ComfyUIAutoNodeMappingRequest,
  ComfyUIAutoParameterizeRequest,
  ComfyUIConfig,
  ComfyUINodeMapping,
} from '@/api/models';

const toast = useToastStore();

// 顶栏手动刷新注入：注册当前视图的 force 刷新 = 强制重拉配置
const registerRefresh = inject<(fn: () => Promise<void>) => void>('registerRefresh');

// ===== 字段级校验（前端早筛，减少 400 往返） =====
// mappingInvalid 由 NodeMappingEditor 上报（JSON 非法），并入校验拦截保存
const mappingInvalid = ref(false);
function validate(f: ComfyUIConfig): Record<string, string> | null {
  const e: Record<string, string> = {};
  const num = (v: unknown): v is number => typeof v === 'number' && !Number.isNaN(v);
  if (num(f.default_width) && f.default_width < 64) e.default_width = '宽度需 ≥ 64';
  if (num(f.default_height) && f.default_height < 64) e.default_height = '高度需 ≥ 64';
  if (num(f.default_steps) && f.default_steps < 1) e.default_steps = '步数需 ≥ 1';
  if (num(f.default_cfg) && f.default_cfg < 0) e.default_cfg = 'CFG 需 ≥ 0';
  if (num(f.default_lora_strength) && (f.default_lora_strength < 0 || f.default_lora_strength > 2))
    e.default_lora_strength = 'LoRA 强度需在 0–2 之间';
  if (num(f.max_user_lora_uploads) && f.max_user_lora_uploads < 0)
    e.max_user_lora_uploads = '上传数上限需 ≥ 0';
  if (num(f.request_timeout_seconds) && f.request_timeout_seconds < 1)
    e.request_timeout_seconds = '请求超时需 ≥ 1 秒';
  if (num(f.poll_interval_seconds) && f.poll_interval_seconds < 0.1)
    e.poll_interval_seconds = '轮询间隔需 ≥ 0.1 秒';
  if (mappingInvalid.value)
    e.node_mapping = '节点映射或占位符映射 JSON 非法，请修正后再保存';
  return Object.keys(e).length ? e : null;
}

const {
  form,
  loading,
  saving,
  error,
  fieldErrors,
  dirty,
  loadForm,
  submit,
  reset,
  patch,
} = useConfigForm<ComfyUIConfig>({
  // ComfyUI 无敏感密钥字段（server_address 明文），load 直接透传
  load: async () => getComfyUIConfig(),
  // PUT 仅回 {success, updated}，需重新 GET 刷新 available_* 列表与 workflow_path
  save: async (body) => {
    const res = await saveComfyUIConfig(body);
    if (!res.success) throw new Error(res.message ?? '保存失败');
    return getComfyUIConfig();
  },
  validate,
  successMessage: 'ComfyUI 配置已保存',
});

// ===== 派生状态（8 态分流） =====
const hasData = computed(
  () => !!form.value.server_address || Object.keys(form.value || {}).length > 0,
);
const showSkeleton = computed(() => loading.value && !hasData.value);
const showEmpty = computed(() => !loading.value && !error.value && !hasData.value);
const showLoadError = computed(() => !loading.value && !!error.value && !hasData.value);
const showForm = computed(() => hasData.value);

// ===== 选项列表（从只读 available_* 派生） =====
function basename(p: string): string {
  const parts = p.replace(/\\/g, '/').split('/');
  return parts[parts.length - 1] || p;
}

const workflowOptions = computed(() => [
  { value: '', label: '（未选择）' },
  ...(form.value.available_workflow_paths ?? []).map((p) => ({ value: p, label: basename(p) })),
]);

const modelOptions = computed(() => [
  { value: '', label: '（未选择）' },
  ...(form.value.available_model_names ?? []).map((m) => ({ value: m, label: m })),
]);

const serviceAvailable = computed(() => !!form.value.service_available);

// ===== 字段写入助手（BaseInput 始终 emit string，数值字段需转换） =====
function setStr(key: keyof ComfyUIConfig, v: string): void {
  patch(key, v as unknown as ComfyUIConfig[keyof ComfyUIConfig]);
}
function setNum(key: keyof ComfyUIConfig, v: string): void {
  if (v === '') {
    patch(key, undefined as unknown as ComfyUIConfig[keyof ComfyUIConfig]);
    return;
  }
  const n = Number(v);
  if (!Number.isNaN(n)) patch(key, n as unknown as ComfyUIConfig[keyof ComfyUIConfig]);
}

// ===== 工作流导入（独立于配置表单 dirty；写型字段 GET 不回传） =====
const importJson = ref('');
const importFilename = ref('');
const importAutoDetect = ref(false);
const importing = ref(false);
const viewingPath = ref<string | null>(null);
const fileInputEl = ref<HTMLInputElement | null>(null);

function onFileChosen(ev: Event): void {
  const input = ev.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    importJson.value = String(reader.result ?? '');
    importFilename.value = file.name;
    viewingPath.value = null;
  };
  reader.onerror = () => toast.push({ type: 'error', message: '读取工作流文件失败' });
  reader.readAsText(file);
  // 清空 value 使同一文件可再次触发 change
  input.value = '';
}

function pickImportFile(): void {
  fileInputEl.value?.click();
}

async function doImportWorkflow(): Promise<void> {
  if (importing.value) return;
  const json = importJson.value.trim();
  if (!json) {
    toast.push({ type: 'warning', message: '请粘贴或上传工作流 JSON' });
    return;
  }
  try {
    JSON.parse(json);
  } catch (e) {
    toast.push({ type: 'error', message: `工作流 JSON 非法：${e instanceof Error ? e.message : ''}` });
    return;
  }
  importing.value = true;
  try {
    const res = await saveComfyUIConfig({
      workflow_json: json,
      workflow_filename: importFilename.value.trim() || undefined,
      auto_detect_node_mapping: importAutoDetect.value,
    });
    if (res.success) {
      const updated = (res.updated ?? {}) as Record<string, unknown>;
      const importedName = String(updated.workflow_imported_filename ?? '');
      toast.push({
        type: 'success',
        message: importedName ? `工作流已导入：${importedName}` : '工作流已导入',
      });
      const keys = updated.auto_detected_node_mapping_keys;
      if (Array.isArray(keys) && keys.length) {
        toast.push({
          type: 'info',
          message: `自动识别节点映射 ${keys.length} 项：${(keys as string[]).join(', ')}`,
          duration: 4000,
        });
      }
      importJson.value = '';
      importFilename.value = '';
      importAutoDetect.value = false;
      viewingPath.value = null;
      await loadForm(true);
    } else {
      toast.push({ type: 'error', message: res.message ?? '导入工作流失败' });
    }
  } catch (e) {
    toast.push({
      type: 'error',
      message: e instanceof ApiError ? e.message : e instanceof Error ? e.message : '导入工作流失败',
    });
  } finally {
    importing.value = false;
  }
}

async function viewWorkflow(path: string): Promise<void> {
  try {
    const res = await getWorkflowContent(path);
    importJson.value = res.workflow_json;
    importFilename.value = res.workflow_name;
    viewingPath.value = res.workflow_path;
    toast.push({ type: 'info', message: `已载入工作流：${res.workflow_name}`, duration: 2500 });
  } catch (e) {
    toast.push({
      type: 'error',
      message: e instanceof ApiError ? e.message : e instanceof Error ? e.message : '读取工作流失败',
    });
  }
}

// ===== 工作流删除（BaseConfirmDialog 确认） =====
const deleteConfirm = ref(false);
let pendingDeletePath = '';
const deleting = ref(false);
function askDeleteWorkflow(path: string): void {
  pendingDeletePath = path;
  deleteConfirm.value = true;
}
async function confirmDeleteWorkflow(): Promise<void> {
  deleteConfirm.value = false;
  const path = pendingDeletePath;
  pendingDeletePath = '';
  if (!path) return;
  deleting.value = true;
  try {
    const res = await deleteWorkflow(path);
    if (res.success) {
      toast.push({ type: 'success', message: `已删除工作流：${res.deleted_workflow_name}` });
      if (res.service_reload_error) {
        toast.push({ type: 'warning', message: `服务重载警告：${res.service_reload_error}` });
      }
      // 若删除的正是当前载入编辑器的工作流，清空编辑器
      if (viewingPath.value && path && basename(viewingPath.value) === basename(path)) {
        importJson.value = '';
        importFilename.value = '';
        viewingPath.value = null;
      }
      await loadForm(true);
    } else {
      toast.push({ type: 'error', message: '删除工作流失败' });
    }
  } catch (e) {
    toast.push({
      type: 'error',
      message: e instanceof ApiError ? e.message : e instanceof Error ? e.message : '删除工作流失败',
    });
  } finally {
    deleting.value = false;
  }
}

// ===== 测试连接（独立按钮，不影响表单 dirty） =====
const testing = ref(false);
async function doTest(): Promise<void> {
  if (testing.value) return;
  testing.value = true;
  try {
    const res = await testComfyUI();
    if (res.success) {
      const models = res.available_model_names?.length ?? 0;
      const loras = res.available_lora_names?.length ?? 0;
      toast.push({
        type: 'success',
        message: res.message ?? `连接成功（${models} 模型 / ${loras} LoRA）`,
        duration: 3500,
      });
      if (res.assets_error) {
        toast.push({ type: 'warning', message: `资源枚举警告：${res.assets_error}` });
      }
      // 热探测后刷新可用列表
      await loadForm(true);
    } else {
      toast.push({ type: 'error', message: res.error ?? res.message ?? '连接失败' });
    }
  } catch (e) {
    toast.push({
      type: 'error',
      message: e instanceof ApiError ? e.message : e instanceof Error ? e.message : '测试连接失败',
    });
  } finally {
    testing.value = false;
  }
}

// ===== 节点映射自动识别（NodeMappingEditor 委托本视图执行） =====
const autoBusy = ref(false);

/** 构造自动操作请求体：优先用导入编辑器中的工作流 JSON，否则回退当前 workflow_path */
function buildAutoRequestBody(): ComfyUIAutoNodeMappingRequest {
  const json = importJson.value.trim();
  if (json) return { workflow_json: json };
  return { workflow_path: form.value.workflow_path };
}

const hasWorkflow = computed(() => !!importJson.value.trim() || !!form.value.workflow_path);

async function doAutoNodeMapping(): Promise<void> {
  if (autoBusy.value) return;
  const body = buildAutoRequestBody();
  if (!body.workflow_json && !body.workflow_path) {
    toast.push({ type: 'warning', message: '请先载入或粘贴工作流 JSON' });
    return;
  }
  autoBusy.value = true;
  try {
    const res = await autoNodeMapping(body);
    if (res.success) {
      patch('node_mapping', res.node_mapping);
      toast.push({
        type: 'success',
        message: `已识别 ${res.mapped_keys.length} 个节点映射：${res.mapped_keys.join(', ') || '无'}`,
        duration: 4000,
      });
    } else {
      toast.push({ type: 'error', message: '自动节点映射失败' });
    }
  } catch (e) {
    toast.push({
      type: 'error',
      message: e instanceof ApiError ? e.message : e instanceof Error ? e.message : '自动节点映射失败',
    });
  } finally {
    autoBusy.value = false;
  }
}

async function doAutoParameterize(mode: 'all' | 'prompt_only'): Promise<void> {
  if (autoBusy.value) return;
  const base = buildAutoRequestBody();
  if (!base.workflow_json && !base.workflow_path) {
    toast.push({ type: 'warning', message: '请先载入或粘贴工作流 JSON' });
    return;
  }
  const body: ComfyUIAutoParameterizeRequest = { ...base, mode };
  autoBusy.value = true;
  try {
    const res = await autoParameterizeWorkflow(body);
    if (res.success) {
      patch('placeholder_mapping', res.placeholder_mapping);
      if (!res.prompt_only_mode) patch('node_mapping', res.node_mapping);
      // 用参数化后的 workflow_json 回填导入编辑器，便于复查或重新导入
      if (res.workflow_json) {
        importJson.value = res.workflow_json;
        viewingPath.value = null;
      }
      toast.push({
        type: 'success',
        message: `参数化完成：替换 ${res.replaced_keys.length} 项，映射 ${res.mapped_keys.length} 项${
          res.skipped_keys.length ? `，跳过 ${res.skipped_keys.length} 项` : ''
        }`,
        duration: 4500,
      });
    } else {
      toast.push({ type: 'error', message: '自动参数化失败' });
    }
  } catch (e) {
    toast.push({
      type: 'error',
      message: e instanceof ApiError ? e.message : e instanceof Error ? e.message : '自动参数化失败',
    });
  } finally {
    autoBusy.value = false;
  }
}

// ===== 操作 =====
function retry(): void {
  loadForm(true).catch(() => {
    /* 错误已由 useConfigForm 内部 toast 并置 error */
  });
}
function onSubmit(): void {
  void submit();
}

// ===== 路由离开拦截：dirty 时弹确认框（同 AIView 模式） =====
const leaveConfirm = ref(false);
let leaveNext: ((ok?: boolean) => void) | null = null;
onBeforeRouteLeave((_to, _from, next) => {
  if (!dirty.value) {
    next();
    return;
  }
  leaveNext = next;
  leaveConfirm.value = true;
});
function confirmLeave(): void {
  leaveConfirm.value = false;
  leaveNext?.(true);
  leaveNext = null;
}
function cancelLeave(): void {
  leaveConfirm.value = false;
  leaveNext?.(false);
  leaveNext = null;
}

onMounted(() => {
  // 顶栏 @refresh → 强制重拉（配置静态，无需轮询）
  registerRefresh?.(() => loadForm(true));
});
</script>

<template>
  <div class="view">
    <BaseSectionTitle
      :icon="Workflow"
      title="ComfyUI 设置"
      subtitle="工作流与节点配置"
    />

    <!-- 保存时错误横幅（表单已存在，inline + 字段级错误） -->
    <div v-if="error && showForm" class="error-banner" role="alert">
      <div class="error-banner__text">
        <AlertTriangle :size="18" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>
      <BaseButton variant="ghost" size="sm" :icon="RotateCw" @click="retry">重试</BaseButton>
    </div>

    <!-- 骨架屏：初始加载 -->
    <div v-if="showSkeleton" class="cm-skeleton" aria-busy="true" aria-live="polite">
      <BaseSkeleton height="1.25rem" width="8rem" />
      <div class="cm-skeleton__card">
        <BaseSkeleton height="1.5rem" width="40%" />
        <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
        <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
      </div>
      <div class="cm-skeleton__card">
        <BaseSkeleton height="1.5rem" width="40%" />
        <div class="cm-skeleton__grid">
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
          <BaseSkeleton height="2.5rem" rounded="var(--radius-md)" />
        </div>
      </div>
    </div>

    <!-- 空状态：已加载但无配置 -->
    <BaseEmpty
      v-else-if="showEmpty"
      :icon="Workflow"
      title="暂无 ComfyUI 配置"
      description="尚未读取到任何 ComfyUI 配置数据，请尝试重新加载。"
      action-text="重新加载"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 加载错误：初始拉取失败 -->
    <BaseEmpty
      v-else-if="showLoadError"
      :icon="AlertTriangle"
      title="配置加载失败"
      :description="error ?? '请检查后端服务后重试。'"
      action-text="重试"
      :action-icon="RotateCw"
      @action="retry"
    />

    <!-- 表单 -->
    <form v-else-if="showForm" class="cm-form" novalidate @submit.prevent="onSubmit">
      <!-- 基础接入 -->
      <section class="card">
        <h3 class="card__title font-display">
          <Server class="card__title-icon" :size="16" aria-hidden="true" />
          基础接入
        </h3>
        <div class="toggle-row">
          <ToggleSwitch
            :model-value="!!form.enabled"
            label="启用 ComfyUI"
            :disabled="saving || loading"
            @update:model-value="(v) => patch('enabled', v)"
          />
          <p class="toggle-row__hint">开启后 ComfyUI 作为图像生成后端之一。</p>
        </div>
        <div class="toggle-row">
          <ToggleSwitch
            :model-value="!!form.enable_slash_command"
            label="启用斜杠命令"
            :disabled="saving || loading"
            @update:model-value="(v) => patch('enable_slash_command', v)"
          />
          <p class="toggle-row__hint">允许通过斜杠命令直接调用 ComfyUI 工作流。</p>
        </div>
        <BaseInput
          :model-value="form.server_address ?? ''"
          label="服务器地址"
          placeholder="http://127.0.0.1:8188"
          hint="ComfyUI 服务地址，不含尾部斜杠"
          :error="fieldErrors.server_address"
          :disabled="saving || loading"
          @update:model-value="(v) => setStr('server_address', v)"
        />
        <div class="test-row">
          <BaseButton
            variant="secondary"
            size="md"
            :icon="Plug"
            :loading="testing"
            :disabled="saving || loading"
            @click="doTest"
          >
            测试连接
          </BaseButton>
          <span class="badge" :class="serviceAvailable ? 'badge--ok' : 'badge--warn'">
            {{ serviceAvailable ? '服务可用' : '服务未就绪' }}
          </span>
        </div>
      </section>

      <!-- 工作流管理 -->
      <section class="card">
        <h3 class="card__title font-display">
          <Workflow class="card__title-icon" :size="16" aria-hidden="true" />
          工作流管理
        </h3>
        <BaseSelect
          :model-value="form.workflow_path ?? ''"
          :options="workflowOptions"
          label="当前工作流"
          placeholder="（未选择）"
          :disabled="saving || loading"
          @update:model-value="(v) => setStr('workflow_path', String(v))"
        />
        <div class="wf-list">
          <div class="wf-list__head">
            <span class="wf-list__label font-display">已发现工作流</span>
            <span class="wf-list__count">{{ form.available_workflow_paths?.length ?? 0 }} 个</span>
          </div>
          <BaseEmpty
            v-if="!(form.available_workflow_paths?.length)"
            :icon="FileJson"
            title="暂无工作流"
            description="未在 data/comfyui/workflows 发现 .json 文件，可通过下方导入新增。"
          />
          <ul v-else class="wf-list__items" role="list">
            <li
              v-for="p in form.available_workflow_paths"
              :key="p"
              :class="['wf-item', { 'is-active': p === form.workflow_path }]"
              role="listitem"
            >
              <span class="wf-item__name" :title="p">{{ basename(p) }}</span>
              <span v-if="p === form.workflow_path" class="wf-item__cur">当前</span>
              <div class="wf-item__btns">
                <BaseButton
                  variant="ghost"
                  size="sm"
                  :icon="Eye"
                  :disabled="importing || deleting"
                  @click="viewWorkflow(p)"
                >
                  查看
                </BaseButton>
                <BaseButton
                  variant="ghost"
                  size="sm"
                  :icon="Trash2"
                  :disabled="importing || deleting"
                  @click="askDeleteWorkflow(p)"
                >
                  删除
                </BaseButton>
              </div>
            </li>
          </ul>
        </div>

        <!-- 导入工作流 -->
        <div class="wf-import">
          <div class="wf-import__head">
            <label class="wf-import__label font-display">导入工作流</label>
            <div class="wf-import__tools">
              <BaseButton
                variant="ghost"
                size="sm"
                :icon="Upload"
                :disabled="importing || saving || loading"
                @click="pickImportFile"
              >
                从文件上传
              </BaseButton>
              <input
                ref="fileInputEl"
                type="file"
                accept=".json,application/json"
                class="wf-import__file"
                @change="onFileChosen"
              />
            </div>
          </div>
          <p class="wf-import__sub">
            粘贴 ComfyUI 导出的工作流 JSON，或从文件载入；导入后可勾选自动识别节点映射。
            <span v-if="viewingPath" class="wf-import__viewing">正在查看：{{ basename(viewingPath) }}</span>
          </p>
          <JsonEditor :model-value="importJson" @update:model-value="importJson = $event" />
          <div class="wf-import__row">
            <BaseInput
              :model-value="importFilename"
              label="目标文件名（可选）"
              type="text"
              placeholder="如 my_workflow.json"
              :disabled="importing || saving || loading"
              @update:model-value="importFilename = $event"
            />
            <div class="wf-import__auto">
              <ToggleSwitch
                :model-value="importAutoDetect"
                label="导入时自动识别节点映射"
                :disabled="importing || saving || loading"
                @update:model-value="importAutoDetect = $event"
              />
            </div>
          </div>
          <BaseButton
            variant="primary"
            size="md"
            :icon="FileJson"
            :loading="importing"
            :disabled="saving || loading || !importJson.trim()"
            @click="doImportWorkflow"
          >
            导入并保存
          </BaseButton>
        </div>
      </section>

      <!-- 默认工作流与模型 -->
      <section class="card">
        <h3 class="card__title font-display">
          <Boxes class="card__title-icon" :size="16" aria-hidden="true" />
          默认工作流与模型
        </h3>
        <BaseSelect
          :model-value="form.default_realistic_workflow_path ?? ''"
          :options="workflowOptions"
          label="写实默认工作流"
          placeholder="（未选择）"
          :disabled="saving || loading"
          @update:model-value="(v) => setStr('default_realistic_workflow_path', String(v))"
        />
        <BaseSelect
          :model-value="form.default_anime_workflow_path ?? ''"
          :options="workflowOptions"
          label="二次元默认工作流"
          placeholder="（未选择）"
          :disabled="saving || loading"
          @update:model-value="(v) => setStr('default_anime_workflow_path', String(v))"
        />
        <BaseInput
          :model-value="form.image_output_node_id ?? ''"
          label="图像输出节点 ID"
          placeholder="如 9"
          hint="工作流中 VAE Decode / SaveImage 节点的 ID"
          :error="fieldErrors.image_output_node_id"
          :disabled="saving || loading"
          @update:model-value="(v) => setStr('image_output_node_id', v)"
        />
        <BaseSelect
          :model-value="form.default_model_name ?? ''"
          :options="modelOptions"
          label="默认模型"
          placeholder="（未选择）"
          :disabled="saving || loading"
          @update:model-value="(v) => setStr('default_model_name', String(v))"
        />
        <div class="card__grid">
          <BaseSelect
            :model-value="form.default_realistic_model_name ?? ''"
            :options="modelOptions"
            label="写实默认模型"
            placeholder="（未选择）"
            :disabled="saving || loading"
            @update:model-value="(v) => setStr('default_realistic_model_name', String(v))"
          />
          <BaseSelect
            :model-value="form.default_anime_model_name ?? ''"
            :options="modelOptions"
            label="二次元默认模型"
            placeholder="（未选择）"
            :disabled="saving || loading"
            @update:model-value="(v) => setStr('default_anime_model_name', String(v))"
          />
        </div>
      </section>

      <!-- 生成参数 -->
      <section class="card">
        <h3 class="card__title font-display">
          <SlidersHorizontal class="card__title-icon" :size="16" aria-hidden="true" />
          生成参数
        </h3>
        <div class="card__grid">
          <BaseInput
            :model-value="form.default_width ?? ''"
            type="number"
            label="默认宽度"
            hint="≥ 64"
            :error="fieldErrors.default_width"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('default_width', v)"
          />
          <BaseInput
            :model-value="form.default_height ?? ''"
            type="number"
            label="默认高度"
            hint="≥ 64"
            :error="fieldErrors.default_height"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('default_height', v)"
          />
          <BaseInput
            :model-value="form.default_steps ?? ''"
            type="number"
            label="默认步数"
            hint="≥ 1"
            :error="fieldErrors.default_steps"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('default_steps', v)"
          />
          <BaseInput
            :model-value="form.default_cfg ?? ''"
            type="number"
            label="默认 CFG"
            hint="≥ 0"
            :error="fieldErrors.default_cfg"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('default_cfg', v)"
          />
          <BaseInput
            :model-value="form.default_sampler ?? ''"
            label="默认采样器"
            placeholder="如 euler"
            :disabled="saving || loading"
            @update:model-value="(v) => setStr('default_sampler', v)"
          />
          <BaseInput
            :model-value="form.default_scheduler ?? ''"
            label="默认调度器"
            placeholder="如 normal"
            :disabled="saving || loading"
            @update:model-value="(v) => setStr('default_scheduler', v)"
          />
          <BaseInput
            :model-value="form.default_seed ?? ''"
            type="number"
            label="默认种子"
            hint="-1 为随机"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('default_seed', v)"
          />
          <BaseInput
            :model-value="form.generation_cost ?? ''"
            type="number"
            label="生成消耗"
            hint="≥ 0"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('generation_cost', v)"
          />
          <BaseInput
            :model-value="form.max_user_lora_uploads ?? ''"
            type="number"
            label="用户 LoRA 上传上限"
            hint="≥ 0"
            :error="fieldErrors.max_user_lora_uploads"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('max_user_lora_uploads', v)"
          />
          <BaseInput
            :model-value="form.request_timeout_seconds ?? ''"
            type="number"
            label="请求超时（秒）"
            hint="≥ 1"
            :error="fieldErrors.request_timeout_seconds"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('request_timeout_seconds', v)"
          />
          <BaseInput
            :model-value="form.poll_interval_seconds ?? ''"
            type="number"
            label="轮询间隔（秒）"
            hint="≥ 0.1"
            :error="fieldErrors.poll_interval_seconds"
            :disabled="saving || loading"
            @update:model-value="(v) => setNum('poll_interval_seconds', v)"
          />
        </div>
      </section>

      <!-- 固定提示词（与 LoraManager 双向：LoRA token 追加回流到此） -->
      <section class="card">
        <h3 class="card__title font-display">
          <Sparkles class="card__title-icon" :size="16" aria-hidden="true" />
          固定提示词
        </h3>
        <div class="prompt-field">
          <label class="prompt-field__label font-display" for="cm-fixed-pos">正向固定提示词</label>
          <textarea
            id="cm-fixed-pos"
            class="prompt-field__area"
            :value="form.fixed_positive_prompt ?? ''"
            placeholder="每行一条，将注入到生成正向提示词"
            :disabled="saving || loading"
            @input="setStr('fixed_positive_prompt', ($event.target as HTMLTextAreaElement).value)"
          />
        </div>
        <div class="prompt-field">
          <label class="prompt-field__label font-display" for="cm-fixed-neg">负向固定提示词</label>
          <textarea
            id="cm-fixed-neg"
            class="prompt-field__area"
            :value="form.fixed_negative_prompt ?? ''"
            placeholder="每行一条，将注入到生成负向提示词"
            :disabled="saving || loading"
            @input="setStr('fixed_negative_prompt', ($event.target as HTMLTextAreaElement).value)"
          />
        </div>
      </section>

      <!-- LoRA 管理（自管下载；default/fixed 绑定表单） -->
      <section class="card">
        <h3 class="card__title font-display">
          <Wand2 class="card__title-icon" :size="16" aria-hidden="true" />
          LoRA 管理
        </h3>
        <LoraManager
          :available-loras="form.available_lora_names ?? []"
          :default-lora="form.default_lora ?? ''"
          :default-lora-strength="form.default_lora_strength ?? 0"
          :fixed-positive-prompt="form.fixed_positive_prompt ?? ''"
          :fixed-negative-prompt="form.fixed_negative_prompt ?? ''"
          :disabled="saving || loading"
          @update:default-lora="(v) => setStr('default_lora', v)"
          @update:default-lora-strength="(v) => patch('default_lora_strength', v)"
          @update:fixed-positive-prompt="(v) => setStr('fixed_positive_prompt', v)"
          @update:fixed-negative-prompt="(v) => setStr('fixed_negative_prompt', v)"
          @refresh="retry"
        />
      </section>

      <!-- 节点映射 -->
      <section class="card">
        <h3 class="card__title font-display">
          <ArrowRightLeft class="card__title-icon" :size="16" aria-hidden="true" />
          节点映射
        </h3>
        <NodeMappingEditor
          :placeholder-mapping="form.placeholder_mapping ?? {}"
          :node-mapping="form.node_mapping ?? {}"
          :disabled="saving || loading"
          :auto-busy="autoBusy"
          :has-workflow="hasWorkflow"
          @update:placeholder-mapping="(v) => patch('placeholder_mapping', v)"
          @update:node-mapping="(v) => patch('node_mapping', v as ComfyUINodeMapping)"
          @auto-parameterize="(m) => doAutoParameterize(m)"
          @auto-node-mapping="doAutoNodeMapping"
          @invalid="mappingInvalid = $event"
        />
        <p v-if="fieldErrors.node_mapping" class="field-error" role="alert">
          {{ fieldErrors.node_mapping }}
        </p>
      </section>

      <!-- 操作栏 -->
      <div class="actions">
        <span v-if="dirty" class="actions__dirty">
          <AlertTriangle :size="14" aria-hidden="true" />
          有未保存更改
        </span>
        <span v-else-if="!saving" class="actions__saved">所有更改已保存</span>
        <BaseButton
          variant="ghost"
          size="md"
          :disabled="!dirty || saving || loading"
          @click="reset"
        >
          放弃更改
        </BaseButton>
        <BaseButton
          type="submit"
          variant="primary"
          size="md"
          :loading="saving"
          :disabled="!dirty || saving || loading"
          :icon="Save"
        >
          保存
        </BaseButton>
      </div>
    </form>

    <!-- 离开确认（dirty 拦截） -->
    <BaseConfirmDialog
      v-model="leaveConfirm"
      title="放弃未保存的更改？"
      message="当前 ComfyUI 配置有未保存的更改，离开将丢弃这些更改。"
      confirm-text="离开"
      variant="danger"
      @confirm="confirmLeave"
      @cancel="cancelLeave"
    />

    <!-- 删除工作流确认 -->
    <BaseConfirmDialog
      v-model="deleteConfirm"
      title="删除工作流？"
      message="将删除该工作流文件并清理关联配置（命中当前/写实/二次元默认工作流则清空对应项）。"
      confirm-text="删除"
      variant="danger"
      @confirm="confirmDeleteWorkflow"
      @cancel="deleteConfirm = false"
    />
  </div>
</template>

<style scoped>
.view {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
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

/* ===== 骨架屏 ===== */
.cm-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.cm-skeleton__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.cm-skeleton__grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3);
}

/* ===== 表单卡片 ===== */
.cm-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}
.card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  transition: border-color var(--dur-micro) var(--ease-out-quart);
}
.card:hover {
  border-color: var(--border-strong);
}
.card__title {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}
.card__title-icon {
  flex: none;
  color: var(--accent);
}
.card__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4) var(--space-5);
}

/* 开关行 */
.toggle-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.toggle-row__hint {
  font-size: var(--text-xs);
  color: var(--text-muted);
}

/* 测试连接行 + 服务徽标 */
.test-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.badge {
  padding: 0 var(--space-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  line-height: var(--lh-tight);
}
.badge--ok {
  color: var(--success);
  border-color: color-mix(in oklch, var(--success) 40%, transparent);
  background: color-mix(in oklch, var(--success) 10%, transparent);
}
.badge--warn {
  color: var(--warning);
  border-color: color-mix(in oklch, var(--warning) 40%, transparent);
  background: color-mix(in oklch, var(--warning) 10%, transparent);
}

/* ===== 工作流列表 ===== */
.wf-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.wf-list__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}
.wf-list__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.wf-list__count {
  font-size: var(--text-xs);
  color: var(--text-muted);
}
.wf-list__items {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: var(--space-2);
  list-style: none;
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.wf-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  transition: background-color var(--dur-micro) var(--ease-out-quart);
}
.wf-item:hover {
  background: var(--bg-surface-2);
}
.wf-item.is-active {
  background: var(--accent-subtle);
}
.wf-item__name {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  color: var(--text-primary);
}
.wf-item__cur {
  flex: none;
  padding: 0 var(--space-2);
  font-size: var(--text-xs);
  color: var(--accent);
  border: 1px solid color-mix(in oklch, var(--accent) 40%, transparent);
  border-radius: var(--radius-sm);
  background: color-mix(in oklch, var(--accent) 10%, transparent);
}
.wf-item__btns {
  flex: none;
  display: inline-flex;
  gap: var(--space-1);
}

/* ===== 工作流导入 ===== */
.wf-import {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.wf-import__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.wf-import__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.wf-import__tools {
  display: inline-flex;
  gap: var(--space-2);
}
.wf-import__file {
  display: none;
}
.wf-import__sub {
  font-size: var(--text-xs);
  color: var(--text-muted);
}
.wf-import__viewing {
  margin-left: var(--space-2);
  color: var(--accent);
}
.wf-import__row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--space-3);
  align-items: end;
}
.wf-import__auto {
  display: flex;
  align-items: center;
  min-height: 2.5rem;
}

/* ===== 固定提示词文本域 ===== */
.prompt-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.prompt-field__label {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}
.prompt-field__area {
  width: 100%;
  min-height: 6rem;
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
.prompt-field__area:hover {
  border-color: var(--border-strong);
}
.prompt-field__area:focus-visible {
  border-color: var(--accent);
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.prompt-field__area:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.field-error {
  font-size: var(--text-xs);
  color: var(--danger);
}

/* ===== 操作栏（sticky 底部，实心底色，禁 blur） ===== */
.actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-5);
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  position: sticky;
  bottom: var(--space-2);
  z-index: 1;
}
.actions__dirty {
  margin-right: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-sm);
  color: var(--warning);
}
.actions__saved {
  margin-right: auto;
  font-size: var(--text-sm);
  color: var(--text-muted);
}

/* ===== 移动端单列 ===== */
@media (max-width: 768px) {
  .card__grid,
  .cm-skeleton__grid,
  .wf-import__row {
    grid-template-columns: 1fr;
  }
  .actions {
    position: static;
  }
}

/* ===== 降低动效 ===== */
@media (prefers-reduced-motion: reduce) {
  .card,
  .wf-item,
  .prompt-field__area {
    transition: none;
  }
}
</style>
