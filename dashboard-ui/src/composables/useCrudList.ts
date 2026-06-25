/* useCrudList — 通用列表 + CRUD composable
 * 封装分页 / 搜索 debounce / 增删改查 / 加载·空·错误态，供 KnowledgeView、NovelAI 预设、
 * ComfyUI lora 等长列表视图复用。替代旧 SPA 各域手写的 loadXxxList / renderXxxList 分页逻辑。
 *
 * 设计要点：
 * - fetchList 由视图提供，负责把 composable 的 { page, pageSize, q, ...query } 映射到后端
 *   列表端点（如 knowledge 的 listDocuments({ page, page_size: pageSize, search: q })），
 *   并把后端响应归一化为 { items, total }。items 字段名固定，视图在后端 documents→items
 *   处完成映射。
 * - search ref 供输入框 v-model；setSearch 带 300ms debounce，触发后回到第 1 页并 reload。
 * - CRUD 成功后自动 reload 刷新列表，失败 toast error 并返回 null/false，视图无需重复处理。
 * - reload 内部 catch 全部异常（ApiError → error + toast），不向上抛，调用方可直接 await
 *   无需 try/catch；onMounted 自动拉取首页。
 * - debounce timer 在 onUnmounted 清理，避免组件销毁后仍触发 reload。 */
import { ref, computed, onMounted, onUnmounted, type Ref, type ComputedRef } from 'vue';
import { ApiError } from '@/api/client';
import { useToastStore } from '@/stores/toast';

/** 列表请求参数：视图自定义查询 Q + composable 注入的分页与搜索词 */
export type CrudListParams<Q extends Record<string, any>> = Q & {
  page: number;
  pageSize: number;
  /** 当前（已 debounce 的）搜索词，空串表示无搜索；视图映射到后端 search 参数 */
  q: string;
};

/** fetchList 归一化后的列表结果 */
export interface CrudListResult<T> {
  items: T[];
  total: number;
  /** 后端回传的当前页/页大小（可选，用于越界校正） */
  page?: number;
  pageSize?: number;
}

export interface UseCrudListOptions<T, Q extends Record<string, any>> {
  /** 拉取列表：接收 { page, pageSize, q, ...query }，返回归一化的 { items, total } */
  fetchList: (params: CrudListParams<Q>) => Promise<CrudListResult<T>>;
  /** 创建，未提供则 createItem 直接返回 null */
  create?: (body: any) => Promise<T>;
  /** 更新，未提供则 updateItem 直接返回 null */
  update?: (id: string | number, body: any) => Promise<T>;
  /** 删除，未提供则 removeItem 直接返回 false */
  remove?: (id: string | number) => Promise<void>;
  /** 页大小，默认 20 */
  pageSize?: number;
  /** 初始查询参数（搜索框之外的过滤项，如 category）；可传 reactive 对象，reload 时展开取最新值 */
  query?: Q;
}

export interface UseCrudListReturn<T, Q extends Record<string, any> = Record<string, any>> {
  items: Ref<T[]>;
  total: Ref<number>;
  page: Ref<number>;
  pageSize: Ref<number>;
  loading: Ref<boolean>;
  error: Ref<string | null>;
  /** 搜索词，绑定输入框 v-model；setSearch 才会 debounce 触发 reload */
  search: Ref<string>;
  /** 是否还有下一页（total > page * pageSize） */
  hasMore: ComputedRef<boolean>;
  /** 空态：非加载中且当前页无数据 */
  isEmpty: ComputedRef<boolean>;
  /** 重新拉取当前页（保留 page） */
  reload: () => Promise<void>;
  /** 跳转到指定页并拉取（p 小于 1 归一为 1） */
  goToPage: (p: number) => Promise<void>;
  /** 设置搜索词，300ms debounce 后回到第 1 页并 reload */
  setSearch: (q: string) => void;
  /** 创建：成功 toast + reload + 返回新项；失败 toast + 返回 null */
  createItem: (body: any) => Promise<T | null>;
  /** 更新：成功 toast + reload + 返回更新项；失败 toast + 返回 null */
  updateItem: (id: string | number, body: any) => Promise<T | null>;
  /** 删除：成功 toast + reload + 返回 true；失败 toast + 返回 false */
  removeItem: (id: string | number) => Promise<boolean>;
  /** @internal 仅用于保留 Q 类型参数，使视图可按 UseCrudListReturn<T,Q> 引用；勿读 */
  readonly _Q?: Q;
}

export function useCrudList<T, Q extends Record<string, any> = Record<string, any>>(
  opts: UseCrudListOptions<T, Q>,
): UseCrudListReturn<T, Q> {
  const toast = useToastStore();

  const items = ref<T[]>([]) as Ref<T[]>;
  const total = ref(0);
  const page = ref(1);
  const pageSize = ref(opts.pageSize ?? 20);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const search = ref('');
  // 初始查询参数：保留引用，reload 时展开取最新值（视图传 reactive 可动态过滤）
  const query: Q = opts.query ?? ({} as Q);

  // 手写 debounce timer（不引入 lodash）
  let searchTimer: ReturnType<typeof setTimeout> | null = null;

  const hasMore = computed<boolean>(() => page.value * pageSize.value < total.value);
  const isEmpty = computed<boolean>(() => !loading.value && items.value.length === 0);

  /** 拉取当前页：内部 catch 全部异常，不向上抛 */
  async function reload(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const params = {
        page: page.value,
        pageSize: pageSize.value,
        ...query,
        q: search.value,
      } as CrudListParams<Q>;
      const res = await opts.fetchList(params);
      items.value = res.items ?? [];
      total.value = res.total ?? 0;
      // 后端回传的 page/pageSize 用于校正（越界时后端可能回退到末页）
      if (typeof res.page === 'number' && res.page > 0) page.value = res.page;
      if (typeof res.pageSize === 'number' && res.pageSize > 0) pageSize.value = res.pageSize;
    } catch (e) {
      if (e instanceof ApiError) {
        error.value = e.message;
        toast.push({ type: 'error', message: e.message });
      } else {
        const msg = e instanceof Error ? e.message : '加载列表失败';
        error.value = msg;
        toast.push({ type: 'error', message: msg });
      }
    } finally {
      loading.value = false;
    }
  }

  async function goToPage(p: number): Promise<void> {
    page.value = Math.max(1, Math.floor(p));
    await reload();
  }

  /** 设置搜索词并 debounce 300ms 后回到第 1 页拉取 */
  function setSearch(q: string): void {
    search.value = q;
    if (searchTimer !== null) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchTimer = null;
      page.value = 1;
      void reload();
    }, 300);
  }

  async function createItem(body: any): Promise<T | null> {
    if (!opts.create) return null;
    try {
      const created = await opts.create(body);
      toast.push({ type: 'success', message: '创建成功' });
      await reload();
      return created;
    } catch (e) {
      if (e instanceof ApiError) {
        toast.push({ type: 'error', message: `创建失败：${e.message}` });
      } else {
        toast.push({ type: 'error', message: e instanceof Error ? e.message : '创建失败' });
      }
      return null;
    }
  }

  async function updateItem(id: string | number, body: any): Promise<T | null> {
    if (!opts.update) return null;
    try {
      const updated = await opts.update(id, body);
      toast.push({ type: 'success', message: '更新成功' });
      await reload();
      return updated;
    } catch (e) {
      if (e instanceof ApiError) {
        toast.push({ type: 'error', message: `更新失败：${e.message}` });
      } else {
        toast.push({ type: 'error', message: e instanceof Error ? e.message : '更新失败' });
      }
      return null;
    }
  }

  async function removeItem(id: string | number): Promise<boolean> {
    if (!opts.remove) return false;
    try {
      await opts.remove(id);
      toast.push({ type: 'success', message: '已删除' });
      await reload();
      return true;
    } catch (e) {
      if (e instanceof ApiError) {
        toast.push({ type: 'error', message: `删除失败：${e.message}` });
      } else {
        toast.push({ type: 'error', message: e instanceof Error ? e.message : '删除失败' });
      }
      return false;
    }
  }

  onMounted(() => {
    void reload();
  });

  onUnmounted(() => {
    if (searchTimer !== null) {
      clearTimeout(searchTimer);
      searchTimer = null;
    }
  });

  return {
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
    createItem,
    updateItem,
    removeItem,
  };
}
