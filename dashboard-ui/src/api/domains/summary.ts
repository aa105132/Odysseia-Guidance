/* summary.ts — 年度总结配置领域 API
 * 对应 GET/PUT /api/config/summary + DELETE /api/config/summary/logs（清日志）。
 * GET 单 section getter 已在 config.ts 声明（getSummaryConfig），此处 re-export 保持单一来源。
 * GET 响应含只读 stats: {total_generated, unique_users}（按 year 查 yearly_summary_log）。
 * PUT 回 {success, updated}（非完整配置），视图 save 包装器在 await 后须重新 GET 刷新（含 stats）。
 * DELETE 清日志：传 year 清该年，不传清所有；响应 {success, message}。
 * 错误统一抛 ApiError（见 client.ts）。 */
import { client } from '../client';
import { getSummaryConfig } from '../config';
import type { SummaryConfig } from '../models';

// 单 section getter 复用 config.ts，避免重复声明
export { getSummaryConfig };
export type { SummaryConfig, SummaryConfigStats } from '../models';

/** PUT /api/config/summary 响应：updated 为已变更字段的部分字典（非完整配置） */
export interface SummarySaveResponse {
  success: boolean;
  updated?: Record<string, unknown>;
}

/** PUT 年度总结配置（数据库持久化）。
 *  body 为脏字段子集（PATCH 语义）；year 2020–2099；generation_limit 1–100；tier2_threshold 0–1000。
 *  保存后视图需重新 GET 刷新 form/original 与 stats。 */
export function saveSummaryConfig(body: Partial<SummaryConfig>): Promise<SummarySaveResponse> {
  return client.put<SummarySaveResponse>('/api/config/summary', body);
}

/** DELETE /api/config/summary/logs 响应 */
export interface ClearSummaryLogsResponse {
  success: boolean;
  message: string;
}

/** DELETE /api/config/summary/logs — 清除年度总结日志。
 *  传 year 清除指定年份日志；不传则清除所有年份日志（不可撤销）。
 *  成功返回 {success, message}；失败 500。 */
export function clearSummaryLogs(year?: number): Promise<ClearSummaryLogsResponse> {
  const path =
    typeof year === 'number'
      ? `/api/config/summary/logs?year=${encodeURIComponent(year)}`
      : '/api/config/summary/logs';
  return client.delete<ClearSummaryLogsResponse>(path);
}
