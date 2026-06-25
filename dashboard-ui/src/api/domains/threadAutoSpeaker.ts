/* threadAutoSpeaker.ts — 帖子自动发言配置领域 API
 * 对应 GET/PUT /api/config/thread-auto-speaker。
 * GET 单 section getter 已在 config.ts 声明（getThreadAutoSpeakerConfig），此处 re-export 保持单一来源，仅补充 save。
 * PUT 回 {success, updated, message}（updated 为已变更字段的部分字典，非完整配置），
 *  故视图 save 包装器在 await 后须重新 GET 刷新 form/original（同 ai.ts 模式）。
 *  thread_ids 后端 _normalize_thread_ids 解析为 int 去重（>0、≤100），_serialize_thread_ids 回传 string[]。
 * 错误统一抛 ApiError（见 client.ts）。 */
import { client } from '../client';
import { getThreadAutoSpeakerConfig } from '../config';
import type { ThreadAutoSpeakerConfig } from '../models';

// 单 section getter 复用 config.ts，避免重复声明
export { getThreadAutoSpeakerConfig };
export type { ThreadAutoSpeakerConfig } from '../models';

/** PUT /api/config/thread-auto-speaker 响应：updated 为已变更字段的部分字典（非完整配置） */
export interface ThreadAutoSpeakerSaveResponse {
  success: boolean;
  updated?: Record<string, unknown>;
  message?: string;
}

/** PUT /api/config/thread-auto-speaker — 更新自动发言配置（数据库持久化 + 运行时热更新 + 写 .env）。
 *  body 为脏字段子集（PATCH 语义）；保存后视图需重新 GET 刷新。 */
export function saveThreadAutoSpeakerConfig(
  body: Partial<ThreadAutoSpeakerConfig>,
): Promise<ThreadAutoSpeakerSaveResponse> {
  return client.put<ThreadAutoSpeakerSaveResponse>('/api/config/thread-auto-speaker', body);
}
