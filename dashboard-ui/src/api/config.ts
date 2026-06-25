/* config.ts — 配置类端点聚合
 * 替代旧 SPA syncForms() 的 config→form 拆分映射，集中此处。
 * fetchAllConfig 拉全量快照（/api/config/all，仅含 ai/imagen/voice/coin/moderation/
 * spring_festival/shop/web_search/image_search/thread_auto_speaker 各 section）；
 * 其余 section（novelai/comfyui/embedding/summary/daily-outfit/video/emoji 等）走各自单端点 getter。
 * 错误统一抛 ApiError（见 client.ts），调用处用 try/catch 捕获 e.status/e.message。 */
import { client } from './client';
import type {
  AIConfig,
  CoinConfig,
  ComfyUIConfig,
  ConfigSnapshot,
  DailyOutfitConfig,
  EmbeddingConfig,
  EmojiConfig,
  ImagenConfig,
  ModerationConfig,
  NovelAIConfig,
  SpringFestivalConfig,
  SummaryConfig,
  ThreadAutoSpeakerConfig,
  VideoConfig,
  VoiceConfig,
} from './models';

/** GET /api/config/all — 全量配置快照（保存后刷新表单的来源） */
export async function fetchAllConfig(): Promise<ConfigSnapshot> {
  return client.get<ConfigSnapshot>('/api/config/all');
}

// --- 单 section getter，各自调对应 GET 端点 ---

export function getAIConfig(): Promise<AIConfig> {
  return client.get<AIConfig>('/api/config/ai');
}

export function getImagenConfig(): Promise<ImagenConfig> {
  return client.get<ImagenConfig>('/api/config/imagen');
}

export function getVoiceConfig(): Promise<VoiceConfig> {
  return client.get<VoiceConfig>('/api/config/voice');
}

export function getNovelAIConfig(): Promise<NovelAIConfig> {
  return client.get<NovelAIConfig>('/api/config/novelai');
}

export function getComfyUIConfig(): Promise<ComfyUIConfig> {
  return client.get<ComfyUIConfig>('/api/config/comfyui');
}

// web_search / image_search 的 get/save/test 已迁至独立 domains 文件：
//   @/api/domains/webSearch、@/api/domains/imageSearch（唯一来源，避免重复声明）。

export function getVideoConfig(): Promise<VideoConfig> {
  return client.get<VideoConfig>('/api/config/video');
}

export function getCoinConfig(): Promise<CoinConfig> {
  return client.get<CoinConfig>('/api/config/coin');
}

export function getModerationConfig(): Promise<ModerationConfig> {
  return client.get<ModerationConfig>('/api/config/moderation');
}

export function getEmojiConfig(): Promise<EmojiConfig> {
  return client.get<EmojiConfig>('/api/config/emoji');
}

export function getEmbeddingConfig(): Promise<EmbeddingConfig> {
  return client.get<EmbeddingConfig>('/api/config/embedding');
}

export function getThreadAutoSpeakerConfig(): Promise<ThreadAutoSpeakerConfig> {
  return client.get<ThreadAutoSpeakerConfig>('/api/config/thread-auto-speaker');
}

export function getSpringFestivalConfig(): Promise<SpringFestivalConfig> {
  return client.get<SpringFestivalConfig>('/api/config/spring-festival');
}

export function getSummaryConfig(): Promise<SummaryConfig> {
  return client.get<SummaryConfig>('/api/config/summary');
}

export function getDailyOutfitConfig(): Promise<DailyOutfitConfig> {
  return client.get<DailyOutfitConfig>('/api/config/daily-outfit');
}
