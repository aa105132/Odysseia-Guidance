/* embedding.ts — 向量嵌入配置领域 API
 * 对应 GET/PUT /api/config/embedding。
 * 后端 PUT 只回 {success, updated}，不返回完整配置；保存后重新 GET 刷新 masked 字段。
 * api_key 为写入型字段，视图 load 时置空，用户填写才会随脏字段提交。 */
import { client } from '../client';
import { getEmbeddingConfig } from '../config';
import type { EmbeddingConfig } from '../models';

export { getEmbeddingConfig };
export type { EmbeddingConfig, ProviderOption } from '../models';

export interface EmbeddingSaveResponse {
  success: boolean;
  updated?: Record<string, unknown>;
}

export async function saveEmbeddingConfig(body: Partial<EmbeddingConfig>): Promise<EmbeddingConfig> {
  await client.put<EmbeddingSaveResponse>('/api/config/embedding', body);
  return getEmbeddingConfig();
}
