/* novelai.ts — NovelAI 配置领域 API
 * 对应 GET/PUT /api/config/novelai、POST /api/config/test-novelai、
 * admin-presets CRUD（GET 列表 / POST upsert-by-name / PUT update-by-id / DELETE）、
 * user-presets（GET 列表 / DELETE，无创建/更新端点）。
 * 错误统一抛 ApiError（见 client.ts）。 */
import { client } from '../client';
import type {
  NovelAIConfig,
  NovelAISaveResponse,
  NovelAITestResponse,
  NovelAIAdminPresetUpsert,
  NovelAIPresetListResponse,
  NovelAIUserPresetListResponse,
  NovelAIPresetMutationResponse,
} from '../models';

/** GET /api/config/novelai — 读取 NovelAI 配置（含 prompt model 路由与可用模型/采样器列表） */
export function getNovelAIConfig(): Promise<NovelAIConfig> {
  return client.get<NovelAIConfig>('/api/config/novelai');
}

/** PUT /api/config/novelai — 更新 NovelAI 配置并热重载服务。
 * 注意：返回的是 {success, updated}，不是完整 NovelAIConfig；保存后需重新 GET 刷新 masked 字段。 */
export function saveNovelAIConfig(body: Partial<NovelAIConfig>): Promise<NovelAISaveResponse> {
  return client.put<NovelAISaveResponse>('/api/config/novelai', body);
}

/** POST /api/config/test-novelai — 测试 NovelAI API 连通性（无请求体；成功返回订阅等级与 Anlas） */
export function testNovelAI(): Promise<NovelAITestResponse> {
  return client.post<NovelAITestResponse>('/api/config/test-novelai');
}

// --- 管理员画师串预设 ---

/** GET /api/config/novelai/admin-presets — 管理员预设列表（{presets, total}，无分页/搜索参数） */
export function listAdminPresets(): Promise<NovelAIPresetListResponse> {
  return client.get<NovelAIPresetListResponse>('/api/config/novelai/admin-presets');
}

/** POST /api/config/novelai/admin-presets — 新增或覆盖管理员预设（upsert by name，无 id）。
 * 同名预设会被整条覆盖（database.py ON CONFLICT(name) DO UPDATE）。 */
export function createAdminPreset(
  body: NovelAIAdminPresetUpsert,
): Promise<NovelAIPresetMutationResponse> {
  return client.post<NovelAIPresetMutationResponse>('/api/config/novelai/admin-presets', body);
}

/** PUT /api/config/novelai/admin-presets/{preset_id} — 按 id 更新管理员预设 */
export function updateAdminPreset(
  presetId: number,
  body: NovelAIAdminPresetUpsert,
): Promise<NovelAIPresetMutationResponse> {
  return client.put<NovelAIPresetMutationResponse>(
    `/api/config/novelai/admin-presets/${presetId}`,
    body,
  );
}

/** DELETE /api/config/novelai/admin-presets/{preset_id} — 删除管理员预设 */
export function deleteAdminPreset(presetId: number): Promise<NovelAIPresetMutationResponse> {
  return client.delete<NovelAIPresetMutationResponse>(
    `/api/config/novelai/admin-presets/${presetId}`,
  );
}

// --- 用户预设（只读列表 + 删除，用户只能在 Discord 端保存，管理面板不可创建/更新） ---

/** GET /api/config/novelai/presets — 用户预设列表（preset 含 user_id） */
export function listUserPresets(): Promise<NovelAIUserPresetListResponse> {
  return client.get<NovelAIUserPresetListResponse>('/api/config/novelai/presets');
}

/** DELETE /api/config/novelai/presets/{preset_id} — 删除用户预设 */
export function deleteUserPreset(presetId: number): Promise<NovelAIPresetMutationResponse> {
  return client.delete<NovelAIPresetMutationResponse>(`/api/config/novelai/presets/${presetId}`);
}
