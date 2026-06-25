/* dailyOutfit.ts — 每日换装配置领域 API
 * 对应 GET/PUT /api/config/daily-outfit + POST .../trigger + POST .../revert。
 * GET 单 section getter 已在 config.ts 声明（getDailyOutfitConfig），此处 re-export 保持单一来源。
 *
 * ⚠️ 三处响应非标（均用 {status, message}，非 {success, updated}）：
 *  - PUT 回 {status:'ok', message:'每日换装配置已更新。'}，不含完整配置 →
 *    视图 save 包装器必须 await saveDailyOutfitConfig(body) 后再 GET 回填：
 *    async (body) => { await saveDailyOutfitConfig(body); return await getDailyOutfitConfig(); }
 *  - trigger 回 {status:'ok', outfit:{name,description,tags,...}}
 *  - revert 回 {status:'ok', message:'已恢复为默认服装。'}
 *
 * designer_api_key 为写入型敏感字段：GET 仅回 designer_api_key_masked（无明文）。
 *  视图 load 包装器须把 form/original 的 designer_api_key 同为空，使其不进 dirty；
 *  用户填入才送出（useConfigForm 文档 L7-9 明示此模式）。
 *  notification_channel_id 留空时视图送 0（旧 SPA HEAD L6429）。
 * 错误统一抛 ApiError（见 client.ts）。 */
import { client } from '../client';
import { getDailyOutfitConfig } from '../config';
import type { DailyOutfitConfig } from '../models';

// 单 section getter 复用 config.ts，避免重复声明
export { getDailyOutfitConfig };
export type { DailyOutfitConfig, DailyOutfitCurrent } from '../models';

/** PUT 请求体：DailyOutfitConfigUpdate（api.py L671-683）。
 *  含写入型 designer_api_key（GET 不回明文），其余为可编辑字段；
 *  不含 masked / is_default / current_outfit（只读）。 */
export interface DailyOutfitUpdateBody {
  enabled?: boolean;
  schedule_hour?: number;
  schedule_minute?: number;
  designer_api_url?: string;
  /** 写入型：GET 不回明文，填入才生效 */
  designer_api_key?: string;
  designer_model?: string;
  style_preference?: string;
  custom_prompt?: string;
  notification_channel_id?: number;
  designer_system_prompt?: string;
  designer_user_template?: string;
}

/** 表单类型：DailyOutfitConfig 扩展写入型 designer_api_key（load 后清空，不入 dirty） */
export type DailyOutfitForm = DailyOutfitConfig & {
  designer_api_key?: string;
};

/** PUT /api/config/daily-outfit 响应（非标：{status, message}，无 success/updated） */
export interface DailyOutfitSaveResponse {
  status: string;
  message: string;
}

/** trigger 返回的换装结果（outfit 由 outfit_service.design_new_outfit() 生成） */
export interface DailyOutfitTriggerOutfit {
  name?: string;
  description?: string;
  tags?: string;
  [key: string]: unknown;
}

/** POST /api/config/daily-outfit/trigger 响应（非标：{status, outfit}） */
export interface DailyOutfitTriggerResponse {
  status: string;
  outfit: DailyOutfitTriggerOutfit;
}

/** POST /api/config/daily-outfit/revert 响应（非标：{status, message}） */
export interface DailyOutfitRevertResponse {
  status: string;
  message: string;
}

/** PUT 每日换装配置（写数据库 + .env 备份）。
 *  body 为脏字段子集（PATCH 语义）；保存后视图需重新 GET 刷新 form/original/masked。 */
export function saveDailyOutfitConfig(
  body: Partial<DailyOutfitUpdateBody>,
): Promise<DailyOutfitSaveResponse> {
  return client.put<DailyOutfitSaveResponse>('/api/config/daily-outfit', body);
}

/** POST /api/config/daily-outfit/trigger — 立即触发今日换装。
 *  成功返回 {status:'ok', outfit:{name,description,tags,...}}；失败 500。 */
export function triggerDailyOutfit(): Promise<DailyOutfitTriggerResponse> {
  return client.post<DailyOutfitTriggerResponse>('/api/config/daily-outfit/trigger');
}

/** POST /api/config/daily-outfit/revert — 恢复默认服装。
 *  成功返回 {status:'ok', message:'已恢复为默认服装。'}。视图成功后应重新 GET 刷新 current_outfit。 */
export function revertDailyOutfit(): Promise<DailyOutfitRevertResponse> {
  return client.post<DailyOutfitRevertResponse>('/api/config/daily-outfit/revert');
}
