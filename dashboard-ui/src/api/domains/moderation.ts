/* moderation.ts — 管理配置领域 API
 * 对应 GET/PUT /api/config/moderation。GET 端点正常（返回 ModerationConfig，
 * ban_ladder 为 number[]），故 getModerationConfig 直接复用 config.ts 的 getter。
 * 错误统一抛 ApiError（见 client.ts）。 */
import { client } from '../client';
import { getModerationConfig as getConfig } from '../config';
import type { ModerationConfig } from '../models';

/** GET /api/config/moderation — 读取管理配置（警告阈值、拉黑时长、图片负反馈封禁）。
 *  ban_ladder_minutes 回传 number[]，视图数组编辑直接绑定。 */
export function getModerationConfig(): Promise<ModerationConfig> {
  return getConfig();
}

/** PUT /api/config/moderation 响应：仅回 {success, updated}，不返回完整配置。
 *  保存后需重新 GET 刷新，故视图 save 包装器在 await 后再调 getModerationConfig。 */
export interface ModerationConfigSaveResponse {
  success: boolean;
  updated?: Record<string, unknown>;
}

/** PUT 管理配置（持久化到数据库）。
 *  warning_threshold 1–100；ban_duration_min/max 1–1440 且 min≤max；
 *  image_feedback_ban_trigger_count 1–20；repeat_window 1–10080；
 *  ban_ladder 需为正整数非空数组，单项 ≤ 43200。 */
export function saveModerationConfig(body: Partial<ModerationConfig>): Promise<ModerationConfigSaveResponse> {
  return client.put<ModerationConfigSaveResponse>('/api/config/moderation', body);
}
