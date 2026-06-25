/* ai.ts — AI 对话配置领域 API
 * 对应 GET/PUT /api/config/ai + POST /api/config/reload-api-keys + POST /api/models/list。
 * 错误统一抛 ApiError（见 client.ts）。 */
import { client } from '../client';
import type { AIConfig } from '../models';

/** GET /api/config/ai — 读取 AI 配置（数据库优先，回退环境变量/内存） */
export function getAIConfig(): Promise<AIConfig> {
  return client.get<AIConfig>('/api/config/ai');
}

/** PUT /api/config/ai 响应：只回 {success, updated}，不返回完整配置。
 *  updated 内可能含 api_keys_reloaded / api_keys_reload_error / api_keys_pending_restart。 */
export interface AIConfigSaveResponse {
  success: boolean;
  updated?: Record<string, unknown>;
}

/** PUT /api/config/ai — 更新 AI 配置（写数据库持久化 + 同步内存 + 写 .env 备份 + 热重载）。
 *  保存后需重新 GET 刷新掩码字段，故视图 save 包装器在 await 后再调 getAIConfig。 */
export function saveAIConfig(body: Partial<AIConfig>): Promise<AIConfigSaveResponse> {
  return client.put<AIConfigSaveResponse>('/api/config/ai', body);
}

/** /api/models/list 请求体（对应后端 ModelListRequest，api.py 行 597-602） */
export interface ModelListRequest {
  /** 上游 API 基址；未传则后端按 format 取默认 */
  api_url?: string | null;
  /** 表单当前输入的密钥；未输入传 null 走环境变量 GEMINI_API_KEYS */
  api_key?: string | null;
  /** 'gemini' | 'openai'，默认 'gemini' */
  api_format?: string;
  /** 'chat' | 'imagen'，默认 'chat' */
  model_type?: string;
}

/** /api/models/list 响应（api.py 行 4692） */
export interface ModelListResponse {
  models: string[];
  count: number;
}

/** POST /api/models/list — 从上游 API 拉取可用模型列表（已排序）。
 *  ModelFetcherSelect 由父视图封装为 fetchModels: () => Promise<string[]> 注入。 */
export function listModels(req: ModelListRequest): Promise<ModelListResponse> {
  return client.post<ModelListResponse>('/api/models/list', req);
}

/** POST /api/config/reload-api-keys 响应（api.py 行 4806-4810） */
export interface ReloadApiKeysResponse {
  success: boolean;
  message?: string;
  key_count?: number;
}

/** POST /api/config/reload-api-keys — 从环境变量热重载 GeminiService 密钥（无请求体）。
 *  GeminiService 未初始化时后端 503。 */
export function reloadApiKeys(): Promise<ReloadApiKeysResponse> {
  return client.post<ReloadApiKeysResponse>('/api/config/reload-api-keys');
}
