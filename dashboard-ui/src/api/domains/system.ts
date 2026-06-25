/* system.ts — 系统监控领域 API
 * 对应后端 GET /api/system/info（需 Bearer），返回实时快照 current + 24h 历史 history。 */
import { client } from '@/api/client';
import type { BotActionResponse } from '../models';

export interface SystemCurrent {
  cpu: number;
  mem_used: number;
  mem_total: number;
  mem_percent: number;
  disk_used: number;
  disk_total: number;
  disk_percent: number;
  net_sent: number;
  net_recv: number;
}

export interface SystemInfoResponse {
  current: SystemCurrent;
  history: Array<Record<string, unknown>>;
}

export function getSystemInfo(): Promise<SystemInfoResponse> {
  return client.get<SystemInfoResponse>('/api/system/info');
}

/** POST /api/bot/restart — 重启 Bot 容器（Dashboard 同容器，连接会断开约 10-30s） */
export function restartBot(): Promise<BotActionResponse> {
  return client.post<BotActionResponse>('/api/bot/restart');
}

/** POST /api/bot/shutdown — 停止 Bot 容器（unless-stopped，需手动 docker start 才能恢复） */
export function shutdownBot(): Promise<BotActionResponse> {
  return client.post<BotActionResponse>('/api/bot/shutdown');
}
