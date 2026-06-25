/* imagen.ts — Imagen 图片生成配置领域 API
 * 对应 GET/PUT /api/config/imagen、POST /api/config/test-imagen、POST /api/models/list(imagen)。
 * 错误统一抛 ApiError（见 client.ts）。 */
import { client } from '../client';
import type { ImagenConfig } from '../models';

/** GET /api/config/imagen — 读取 Imagen 配置（含 SFW/NSFW 完整模型矩阵 + 运行参数） */
export function getImagenConfig(): Promise<ImagenConfig> {
  return client.get<ImagenConfig>('/api/config/imagen');
}

/** PUT /api/config/imagen 响应：仅回 {success, updated}，不返回完整配置（api.py 行 2074）。
 * updated 内含本次写入字段 + service_reloaded/service_available/reload_error 等热重载结果。
 * 保存后需重新 GET 刷新 masked 字段与服务状态。 */
export interface ImagenSaveResponse {
  success: boolean;
  updated?: Record<string, unknown>;
  message?: string;
}

/** PUT /api/config/imagen — 更新 Imagen 配置并热重载服务。
 * 注意：返回的是 {success, updated}，不是完整 ImagenConfig。 */
export function saveImagenConfig(body: Partial<ImagenConfig>): Promise<ImagenSaveResponse> {
  return client.put<ImagenSaveResponse>('/api/config/imagen', body);
}

/** POST /api/config/test-imagen 响应：连通性校验，后端用固定 prompt 生成一张图，
 * 仅验证连通性，不返回图片 URL/base64（api.py 行 5133-5152）。 */
export interface ImagenTestResult {
  success: boolean;
  message?: string;
  error?: string;
}

/** POST /api/config/test-imagen — 测试 Imagen API 连通性（无请求体） */
export function testImagen(): Promise<ImagenTestResult> {
  return client.post<ImagenTestResult>('/api/config/test-imagen');
}

/** /api/models/list 请求体（ModelListRequest，api.py 行 597-602） */
export interface ModelListRequest {
  api_url?: string | null;
  api_key?: string | null;
  /** 'gemini' | 'openai'；gemini_chat 归入 Gemini 分支，默认 'gemini' */
  api_format?: string;
  /** 'chat' | 'imagen'，默认 'chat' */
  model_type?: string;
}

/** /api/models/list 响应（api.py 行 4692） */
export interface ModelListResponse {
  models: string[];
  count: number;
}

/** POST /api/models/list — 拉取 imagen 可用模型列表（model_type='imagen'）。
 * api_format 为 'openai' 时走 OpenAI 分支，其余（gemini/gemini_chat）归入 Gemini 分支。
 * api_key 未提供（null）时后端回退环境变量 GEMINI_API_KEYS。 */
export function fetchImagenModels(req: ModelListRequest): Promise<string[]> {
  const apiFormat = req.api_format === 'openai' ? 'openai' : 'gemini';
  const body: ModelListRequest = {
    api_url: req.api_url ?? null,
    api_key: req.api_key ?? null,
    api_format: apiFormat,
    model_type: 'imagen',
  };
  return client.post<ModelListResponse>('/api/models/list', body).then((r) => r.models ?? []);
}
