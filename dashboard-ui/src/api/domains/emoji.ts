/* emoji.ts — 表情映射配置领域 API
 * 对应 GET /api/config/emoji + PUT /api/config/emoji + POST /api/config/emoji/add + DELETE /api/config/emoji/{placeholder}。
 * GET 单 section getter 已在 config.ts 声明（getEmojiConfig），此处 re-export 保持单一来源，仅补充写端点。
 * faction_mappings 后端无增删改端点（仅 GET 暴露，只读），故本域 CRUD 仅针对 default_mappings。
 * 错误统一抛 ApiError（见 client.ts）。 */
import { client } from '../client';
import { getEmojiConfig } from '../config';
import type { EmojiMapping } from '../models';

// 单 section getter 复用 config.ts，避免重复声明
export { getEmojiConfig };
export type { EmojiConfig } from '../models';

/** POST/DELETE/PUT 通用响应：success + message（add/delete）或 updated（PUT 批量更新的占位符清单） */
export interface EmojiMutationResponse {
  success: boolean;
  message?: string;
  /** PUT 回传已更新/追加的占位符列表 */
  updated?: string[];
}

/** PUT /api/config/emoji — 批量更新或追加映射（按 placeholder 匹配：存在则覆盖，不存在则追加）。
 *  body 为 { mappings: EmojiMapping[] }（对应后端 EmojiMappingUpdate，api.py L711-713）。 */
export function saveEmojiMappings(mappings: EmojiMapping[]): Promise<EmojiMutationResponse> {
  return client.put<EmojiMutationResponse>('/api/config/emoji', { mappings });
}

/** POST /api/config/emoji/add — 新增单条映射；占位符已存在则 400（api.py L5104）。 */
export function addEmojiMapping(mapping: EmojiMapping): Promise<EmojiMutationResponse> {
  return client.post<EmojiMutationResponse>('/api/config/emoji/add', mapping);
}

/** DELETE /api/config/emoji/{placeholder} — 删除单条映射；找不到则 404（api.py L5130）。
 *  placeholder 需 encodeURIComponent（含 < >，旧 SPA L6566）。 */
export function deleteEmojiMapping(placeholder: string): Promise<EmojiMutationResponse> {
  return client.delete<EmojiMutationResponse>(
    `/api/config/emoji/${encodeURIComponent(placeholder)}`,
  );
}
