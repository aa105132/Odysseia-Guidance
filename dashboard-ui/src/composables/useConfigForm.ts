/* useConfigForm — 通用配置表单 composable
 * 统一封装 load / save / validate / dirty / beforeunload / 防重复提交。
 * 替代旧 SPA syncForms()（index.html L5489）的 config→form 拆分 + 手动 dirty 跟踪。
 * 视图只负责字段绑定与领域校验，错误处理与未保存拦截由本 composable 接管。
 *
 * 设计要点（借鉴旧 SPA）：
 * - 保存送"脏字段子集"（PATCH 语义），避免把表单里被清空的敏感字段（如 api_key）
 *   覆盖回后端。视图应在 load 包装器里把敏感字段清空，使其在 form 与 original 中
 *   同为空，从而不进入 dirty 集合；用户重新填入才会被送出。
 * - saving=true 期间禁用提交按钮（绑 :disabled/:loading），杜绝重复提交。
 * - 400 且后端 detail 为字段错误结构时，反解 client.ts 拼成的 JSON 字符串回填 fieldErrors。
 * - dirty 时 beforeunload 拦截，离开页面弹原生确认。 */
import { ref, computed, onMounted, onUnmounted, type Ref, type ComputedRef } from 'vue';
import { ApiError } from '@/api/client';
import { useToastStore } from '@/stores/toast';

export interface UseConfigFormOptions<T extends Record<string, any>> {
  /** 拉取当前配置；视图可在此清空敏感字段（api_key 等）后再返回 */
  load: () => Promise<T>;
  /** 保存（PATCH 语义：composable 只送脏字段子集） */
  save: (body: Partial<T>) => Promise<T>;
  /** 可选客户端字段级校验，返回错误 map 触发 fieldErrors 并中止提交 */
  validate?: (form: T) => Record<string, string> | null;
  /** 可选初始值，提供则跳过 onMounted 自动 load */
  initial?: T;
  /** 保存成功 toast 文案 */
  successMessage?: string;
  /** 保存失败 toast 文案覆盖（默认 "保存失败：" + 错误信息） */
  errorMessage?: string;
  /** 无变更时是否提示，默认 true */
  notifyNoChange?: boolean;
}

export interface UseConfigFormReturn<T> {
  form: Ref<T>;
  original: Ref<T>;
  loading: Ref<boolean>;
  saving: Ref<boolean>;
  error: Ref<string | null>;
  fieldErrors: Ref<Record<string, string>>;
  dirty: ComputedRef<boolean>;
  dirtyFields: ComputedRef<string[]>;
  hasUnsavedChanges: ComputedRef<boolean>;
  /** 加载配置；force=true 绕过"已加载"缓存强制重拉 */
  loadForm: (force?: boolean) => Promise<void>;
  /** 保存，返回成功 bool；失败已 toast，视图无需重复处理 */
  submit: () => Promise<boolean>;
  /** 放弃编辑，恢复 form 到 original */
  reset: () => void;
  /** 安全更新单个字段（响应式赋值） */
  patch: <K extends keyof T>(key: K, value: T[K]) => void;
}

/** 深比较（键序无关、数组按索引、原始值严格相等） */
function deepEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (a == null || b == null) return a === b;
  if (typeof a !== typeof b) return false;
  if (typeof a !== 'object') return a === b;
  const aArr = Array.isArray(a);
  const bArr = Array.isArray(b);
  if (aArr !== bArr) return false;
  if (aArr) {
    const aa = a as unknown[];
    const bb = b as unknown[];
    if (aa.length !== bb.length) return false;
    for (let i = 0; i < aa.length; i++) {
      if (!deepEqual(aa[i], bb[i])) return false;
    }
    return true;
  }
  const ao = a as Record<string, unknown>;
  const bo = b as Record<string, unknown>;
  const ak = Object.keys(ao);
  const bk = Object.keys(bo);
  if (ak.length !== bk.length) return false;
  for (const k of ak) {
    if (!Object.prototype.hasOwnProperty.call(bo, k)) return false;
    if (!deepEqual(ao[k], bo[k])) return false;
  }
  return true;
}

/** 深拷贝（structuredClone 优先，降级 JSON） */
function deepClone<T>(v: T): T {
  if (v == null || typeof v !== 'object') return v;
  if (typeof structuredClone === 'function') {
    try {
      return structuredClone(v);
    } catch {
      /* fallthrough */
    }
  }
  return JSON.parse(JSON.stringify(v)) as T;
}

/** 从后端 400 detail 反解字段级错误（兼容 FastAPI [{loc,msg}] 与 {field:msg}） */
function extractFieldErrors(raw: unknown): Record<string, string> | null {
  if (Array.isArray(raw)) {
    const map: Record<string, string> = {};
    for (const item of raw) {
      if (!item || typeof item !== 'object') continue;
      const it = item as Record<string, any>;
      if (Array.isArray(it.loc) && it.msg) {
        const loc = it.loc[it.loc.length - 1];
        if (typeof loc === 'string') map[loc] = String(it.msg);
      } else if (typeof it.field === 'string' && it.message) {
        map[it.field] = String(it.message);
      }
    }
    return Object.keys(map).length ? map : null;
  }
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const map: Record<string, string> = {};
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      if (typeof v === 'string') map[k] = v;
      else if (v && typeof v === 'object' && typeof (v as Record<string, any>).message === 'string') {
        map[k] = (v as Record<string, any>).message;
      }
    }
    return Object.keys(map).length ? map : null;
  }
  return null;
}

export function useConfigForm<T extends Record<string, any>>(
  opts: UseConfigFormOptions<T>,
): UseConfigFormReturn<T> {
  const toast = useToastStore();

  const form = ref<T>(opts.initial ? deepClone(opts.initial) : ({} as T)) as Ref<T>;
  const original = ref<T>(opts.initial ? deepClone(opts.initial) : ({} as T)) as Ref<T>;
  const loading = ref(false);
  const saving = ref(false);
  const error = ref<string | null>(null);
  const fieldErrors = ref<Record<string, string>>({});
  const loaded = ref(false);

  const dirtyFields = computed<string[]>(() => {
    const f = form.value as Record<string, unknown>;
    const o = original.value as Record<string, unknown>;
    const keys = new Set<string>([...Object.keys(f), ...Object.keys(o)]);
    const result: string[] = [];
    for (const k of keys) {
      if (!deepEqual(f[k], o[k])) result.push(k);
    }
    return result;
  });

  const dirty = computed<boolean>(() => dirtyFields.value.length > 0);
  const hasUnsavedChanges = dirty;

  function buildDirtyPayload(): Partial<T> {
    const payload: Record<string, unknown> = {};
    const f = form.value as Record<string, unknown>;
    for (const k of dirtyFields.value) {
      payload[k] = deepClone(f[k]);
    }
    return payload as Partial<T>;
  }

  async function loadForm(force?: boolean): Promise<void> {
    if (loaded.value && !force) return;
    loading.value = true;
    error.value = null;
    fieldErrors.value = {};
    try {
      const data = await opts.load();
      const cloned = deepClone(data);
      form.value = cloned;
      original.value = deepClone(cloned);
      loaded.value = true;
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载配置失败';
      error.value = msg;
      toast.push({ type: 'error', message: msg });
      throw e;
    } finally {
      loading.value = false;
    }
  }

  async function submit(): Promise<boolean> {
    // 防重复提交
    if (saving.value) return false;

    // 无变更：视为成功，可选提示
    if (!dirty.value) {
      if (opts.notifyNoChange !== false) {
        toast.push({ type: 'info', message: '没有需要保存的更改', duration: 2500 });
      }
      return true;
    }

    // 客户端字段校验
    if (opts.validate) {
      const verrs = opts.validate(form.value);
      if (verrs && Object.keys(verrs).length) {
        fieldErrors.value = verrs;
        toast.push({ type: 'error', message: '请修正表单中的错误' });
        return false;
      }
    }

    saving.value = true;
    error.value = null;
    fieldErrors.value = {};
    try {
      const payload = buildDirtyPayload();
      const saved = await opts.save(payload);
      // 以后端返回值为准刷新 form + original，保持一致
      const next = saved && typeof saved === 'object' ? deepClone(saved) : deepClone(form.value);
      form.value = next as T;
      original.value = deepClone(next) as T;
      toast.push({ type: 'success', message: opts.successMessage ?? '配置已保存' });
      return true;
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 400) {
          // client.ts 把 detail JSON.stringify 进 message，尝试反解字段级错误
          let parsed: unknown = null;
          try {
            parsed = JSON.parse(e.message);
          } catch {
            parsed = null;
          }
          const fe = extractFieldErrors(parsed);
          if (fe) {
            fieldErrors.value = fe;
            error.value = '请修正表单中的错误';
            toast.push({ type: 'error', message: '请修正表单中的错误' });
          } else {
            error.value = e.message;
            toast.push({ type: 'error', message: opts.errorMessage ?? `保存失败：${e.message}` });
          }
        } else {
          error.value = e.message;
          toast.push({ type: 'error', message: opts.errorMessage ?? `保存失败：${e.message}` });
        }
      } else {
        const msg = e instanceof Error ? e.message : '保存失败';
        error.value = msg;
        toast.push({ type: 'error', message: msg });
      }
      return false;
    } finally {
      saving.value = false;
    }
  }

  function reset(): void {
    form.value = deepClone(original.value);
    fieldErrors.value = {};
    error.value = null;
  }

  function patch<K extends keyof T>(key: K, value: T[K]): void {
    (form.value as Record<string, unknown>)[key as string] = value;
  }

  // beforeunload：dirty 时拦截离开
  function onBeforeUnload(e: BeforeUnloadEvent): void {
    if (dirty.value) {
      e.preventDefault();
      e.returnValue = '';
    }
  }
  window.addEventListener('beforeunload', onBeforeUnload);

  onMounted(() => {
    // 提供初始值则跳过自动 load（视图自行控制）
    if (!opts.initial) {
      loadForm().catch(() => {
        /* 错误已在 loadForm 内 toast 并置 error */
      });
    }
  });

  onUnmounted(() => {
    window.removeEventListener('beforeunload', onBeforeUnload);
  });

  return {
    form,
    original,
    loading,
    saving,
    error,
    fieldErrors,
    dirty,
    dirtyFields,
    hasUnsavedChanges,
    loadForm,
    submit,
    reset,
    patch,
  };
}
