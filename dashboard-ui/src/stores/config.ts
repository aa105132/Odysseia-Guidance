/* config.ts — 配置快照（Pinia）
 * 集中替代旧 SPA syncForms()（index.html L5489）的 config→form 拆分映射。
 * 各视图只读自己关心的 section，保存后调用 load() 刷新。
 * 阶段0骨架：fetchAllConfig 由 api/config 提供，阶段1接真实端点后按 section 拆分。 */
// TODO: 阶段1用 openapi-typescript 自动生成类型替换手写 any
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { fetchAllConfig } from '@/api/config';

export const useConfigStore = defineStore('config', () => {
  // 整份配置快照；null 表示尚未加载。结构由 api/config 的 fetchAllConfig 决定
  const snapshot = ref<Record<string, any> | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  // 拉取全量配置写入快照；失败记录 message 并向上抛，由调用处 toast
  async function load(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      snapshot.value = await fetchAllConfig();
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载配置失败';
      throw e;
    } finally {
      loading.value = false;
    }
  }

  // 按 section 取片段；未加载或缺失返回 undefined
  function getBySection(section: string): any {
    return snapshot.value?.[section];
  }

  return { snapshot, loading, error, load, getBySection };
});
