/* video.ts — 视频生成配置领域 API
 * 对应 GET/PUT /api/config/video。
 * 后端 PUT 只回 {success, updated}，不返回完整配置；保存后重新 GET 刷新 masked 字段与服务状态。
 * api_key 为写入型字段，视图 load 时置空，用户填写才会随脏字段提交。 */
import { client } from '../client';
import { getVideoConfig } from '../config';
import type { VideoConfig } from '../models';

export { getVideoConfig };
export type { VideoConfig } from '../models';

export interface VideoSaveResponse {
  success: boolean;
  updated?: Record<string, unknown>;
}

export async function saveVideoConfig(body: Partial<VideoConfig>): Promise<VideoConfig> {
  await client.put<VideoSaveResponse>('/api/config/video', body);
  return getVideoConfig();
}
