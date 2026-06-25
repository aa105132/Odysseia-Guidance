/* springFestival.ts — 新春活动配置领域 API
 * 对应 GET/PUT /api/config/spring-festival。GET 单 section getter 已在 config.ts
 * 声明（getSpringFestivalConfig），此处 re-export 保持单一来源，仅补充 save。
 * PUT 回 {success, updated}（updated 为已变更字段的部分字典，非完整配置），
 *  故视图 save 包装器在 await 后须重新 GET 刷新 form/original（同 ai.ts 模式）。
 *  min/max_reward 后端联合校验 max≥min；PUT 同时写 .env（api.py L6356-6360）。
 * 错误统一抛 ApiError（见 client.ts）。 */
import { client } from '../client';
import { getSpringFestivalConfig } from '../config';
import type { SpringFestivalConfig } from '../models';

// 单 section getter 复用 config.ts，避免重复声明
export { getSpringFestivalConfig };
export type { SpringFestivalConfig } from '../models';

/** PUT /api/config/spring-festival 响应：updated 为已变更字段的部分字典（非完整配置） */
export interface SpringFestivalSaveResponse {
  success: boolean;
  updated?: Record<string, unknown>;
}

/** PUT 新春活动配置（数据库持久化 + 写 .env 备份）。
 *  body 为脏字段子集（PATCH 语义）；保存后视图需重新 GET 刷新。
 *  min_reward / max_reward 需 >0 且 max≥min；文案字段需非空。 */
export function saveSpringFestivalConfig(
  body: Partial<SpringFestivalConfig>,
): Promise<SpringFestivalSaveResponse> {
  return client.put<SpringFestivalSaveResponse>('/api/config/spring-festival', body);
}
