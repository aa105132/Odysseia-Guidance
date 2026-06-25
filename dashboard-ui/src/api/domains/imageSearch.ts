/* imageSearch.ts — 图片搜索配置领域 API
 * 对应 GET/PUT /api/config/image-search、POST /api/config/test-image-search。
 * PUT 响应仅 {success, updated?, message}，不回完整配置——
 * 故 save 在 PUT 后重新 GET 返回完整配置，刷新 masked 字段，契合 useConfigForm
 * 的 save: (body) => Promise<T> 契约。写入型 api_key GET 不回传，归一为空串。 */
import { client } from '../client';
import type { ImageSearchConfig } from '../models';

/** GET /api/config/image-search — 读取图片搜索配置（OpenAI 兼容接口） */
export function getImageSearchConfig(): Promise<ImageSearchConfig> {
  return client.get<ImageSearchConfig>('/api/config/image-search');
}

/** PUT /api/config/image-search — 更新图片搜索配置；保存后重新 GET 返回完整配置（刷新 masked） */
export async function saveImageSearchConfig(
  body: Partial<ImageSearchConfig>,
): Promise<ImageSearchConfig> {
  await client.put<{ success: boolean; updated?: Record<string, unknown>; message?: string }>(
    '/api/config/image-search',
    body,
  );
  const data = await getImageSearchConfig();
  // 写入型密钥 GET 不回传，归一为空串（与视图 load 包装器一致）
  return { ...data, api_key: '' };
}

/** POST /api/config/test-image-search — 测试图片搜索连接（无请求体）
 * 未配置时 {success:false, status:'未配置', message}；成功返回 body_preview 前 300 字。 */
export interface ImageSearchTestResult {
  success: boolean;
  status: string;
  message?: string;
  body_preview?: string;
}
export function testImageSearch(): Promise<ImageSearchTestResult> {
  return client.post<ImageSearchTestResult>('/api/config/test-image-search');
}
