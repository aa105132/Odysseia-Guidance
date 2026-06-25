/* coin.ts — 货币配置领域 API
 * GET /api/config/coin 后端存在 NameError（get_coin_config 引用未声明的
 * db_summary_imagen_enabled，且响应缺 summary_imagen_resolution / model），
 * 故 getCoinConfig 暂走 fetchAllConfig().coin 子集（/api/config/all 的 coin 段，
 * 含全部可编辑字段 + 9 ghost 键 + currency_name）。待后端修复后可切回单端点。
 * PUT /api/config/coin 端点正常，saveCoinConfig 直接打 PUT。 */
import { client } from '../client';
import { fetchAllConfig } from '../config';
import type { CoinConfig } from '../models';

/** 9 个 ghost card 动态图片 URL 键（后端 get_ghost_card_image_urls / apply_ghost_card_image_urls）。
 *  旧 SPA 仅暴露前 8 个，此处补齐全部 9 个。 */
export const GHOST_CARD_URL_KEYS = [
  'ghost_emotion_happy_url',
  'ghost_emotion_sad_url',
  'ghost_emotion_neutral_url',
  'ghost_emotion_super_win_url',
  'ghost_ai_thumbnail_low_url',
  'ghost_ai_thumbnail_medium_url',
  'ghost_ai_thumbnail_high_url',
  'ghost_ai_thumbnail_super_url',
  'ghost_ai_win_thumbnail_url',
] as const;

/** ghost 键 → 中文标签（与后端 ghost_url_field_labels 对齐，api.py 行 4602-4612） */
export const GHOST_CARD_LABELS: Record<string, string> = {
  ghost_emotion_happy_url: '抽鬼牌·高兴情绪图',
  ghost_emotion_sad_url: '抽鬼牌·难过情绪图',
  ghost_emotion_neutral_url: '抽鬼牌·中性情绪图',
  ghost_emotion_super_win_url: '抽鬼牌·超级胜利图',
  ghost_ai_thumbnail_low_url: '抽鬼牌·低级策略缩略图',
  ghost_ai_thumbnail_medium_url: '抽鬼牌·中级策略缩略图',
  ghost_ai_thumbnail_high_url: '抽鬼牌·高级策略缩略图',
  ghost_ai_thumbnail_super_url: '抽鬼牌·超级策略缩略图',
  ghost_ai_win_thumbnail_url: '抽鬼牌·AI 胜利缩略图',
};

/** GET 货币配置（绕开 /api/config/coin 的 NameError，走 /api/config/all 的 coin 子集）。
 *  该子集含全部可编辑字段 + 9 ghost 键 + currency_name，仅缺只读 tax_rate（视图不展示）。 */
export async function getCoinConfig(): Promise<CoinConfig> {
  const all = await fetchAllConfig();
  return (all.coin ?? {}) as CoinConfig;
}

/** PUT /api/config/coin 响应：仅回 {success, updated}，不返回完整配置。
 *  保存后需重新 GET 刷新，故视图 save 包装器在 await 后再调 getCoinConfig。 */
export interface CoinConfigSaveResponse {
  success: boolean;
  updated?: Record<string, unknown>;
}

/** PUT 货币配置（写数据库持久化 + 同步内存 + 写 .env 备份）。
 *  URL 字段经后端 _normalize_url 校验：非空必须 http:// 或 https:// 开头。
 *  数值字段需 ≥ 0；summary_imagen_resolution 仅 default / 2k / 4k。 */
export function saveCoinConfig(body: Partial<CoinConfig>): Promise<CoinConfigSaveResponse> {
  return client.put<CoinConfigSaveResponse>('/api/config/coin', body);
}
