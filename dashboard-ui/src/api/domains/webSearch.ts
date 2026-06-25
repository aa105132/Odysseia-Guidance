/* webSearch.ts — 网络搜索配置领域 API
 * 对应 GET/PUT /api/config/web-search、POST /api/config/test-web-search。
 * GET 回传 grok/tavily 双源 + masked 密钥 + configured 标志；
 * PUT 响应仅 {success, updated?, message}，不回完整配置——
 * 故 save 在 PUT 后重新 GET 返回完整配置，刷新 masked 字段，契合 useConfigForm
 * 的 save: (body) => Promise<T> 契约（composable 据 save 返回值刷新 form/original）。
 * 写入型密钥 grok_api_key/tavily_api_key 在 GET 中不存在，归一为空串保持表单形状一致。 */
import { client } from '../client';
import type { WebSearchConfig } from '../models';

/** GET /api/config/web-search — 读取网络搜索配置（grok + tavily 双源） */
export function getWebSearchConfig(): Promise<WebSearchConfig> {
  return client.get<WebSearchConfig>('/api/config/web-search');
}

/** PUT /api/config/web-search — 更新网络搜索配置；保存后重新 GET 返回完整配置（刷新 masked） */
export async function saveWebSearchConfig(
  body: Partial<WebSearchConfig>,
): Promise<WebSearchConfig> {
  await client.put<{ success: boolean; updated?: Record<string, unknown>; message?: string }>(
    '/api/config/web-search',
    body,
  );
  const data = await getWebSearchConfig();
  // 写入型密钥 GET 不回传，归一为空串（与视图 load 包装器一致）
  return { ...data, grok_api_key: '', tavily_api_key: '' };
}

/** POST /api/config/test-web-search — 测试 Grok + Tavily 连接（无请求体）
 * success 仅看 grok 是否「连接成功」；tavily 独立判定。 */
export interface WebSearchTestResult {
  success: boolean;
  results: {
    grok: { status: string; models_count?: number };
    tavily: { status: string };
  };
}
export function testWebSearch(): Promise<WebSearchTestResult> {
  return client.post<WebSearchTestResult>('/api/config/test-web-search');
}
