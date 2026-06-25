/* comfyui.ts — ComfyUI 配置领域 API
 * GET/PUT /api/config/comfyui（配置读写 + 工作流导入）+ workflow-content/delete +
 * test-comfyui + auto-node-mapping + auto-parameterize-workflow + download-lora。
 * 错误统一抛 ApiError（见 client.ts）。 */
import { client } from '../client';
import type {
  ComfyUIConfig,
  ComfyUIConfigSaveResponse,
  ComfyUIWorkflowContentResponse,
  ComfyUIDeleteWorkflowResponse,
  ComfyUITestResponse,
  ComfyUIAutoNodeMappingRequest,
  ComfyUIAutoNodeMappingResponse,
  ComfyUIAutoParameterizeRequest,
  ComfyUIAutoParameterizeResponse,
  ComfyUILoraDownloadRequest,
  ComfyUILoraDownloadResponse,
} from '../models';

/** GET /api/config/comfyui — 读取 ComfyUI 配置（含可用 workflow/model/lora 列表） */
export function getComfyUIConfig(): Promise<ComfyUIConfig> {
  return client.get<ComfyUIConfig>('/api/config/comfyui');
}

/** PUT /api/config/comfyui — 更新 ComfyUI 配置（含工作流导入）。
 * 注意：返回 {success, updated}，不是完整 ComfyUIConfig；保存后需重新 GET 刷新。 */
export function saveComfyUIConfig(
  body: Partial<ComfyUIConfig>,
): Promise<ComfyUIConfigSaveResponse> {
  return client.put<ComfyUIConfigSaveResponse>('/api/config/comfyui', body);
}

/** GET /api/config/comfyui/workflow-content — 读取指定工作流 JSON 内容（仅限已发现列表中的 .json） */
export function getWorkflowContent(workflowPath: string): Promise<ComfyUIWorkflowContentResponse> {
  const qs = new URLSearchParams({ workflow_path: workflowPath });
  return client.get<ComfyUIWorkflowContentResponse>(
    `/api/config/comfyui/workflow-content?${qs.toString()}`,
  );
}

/** DELETE /api/config/comfyui/workflow — 删除指定工作流文件并清理关联配置（workflow_path/realistic/anime 之一命中则清空） */
export function deleteWorkflow(workflowPath: string): Promise<ComfyUIDeleteWorkflowResponse> {
  const qs = new URLSearchParams({ workflow_path: workflowPath });
  return client.delete<ComfyUIDeleteWorkflowResponse>(
    `/api/config/comfyui/workflow?${qs.toString()}`,
  );
}

/** POST /api/config/test-comfyui — 测试 ComfyUI 连接（无请求体，后端热探测模型/LoRA 列表） */
export function testComfyUI(): Promise<ComfyUITestResponse> {
  return client.post<ComfyUITestResponse>('/api/config/test-comfyui');
}

/** POST /api/config/comfyui/auto-node-mapping — 自动识别工作流节点映射（workflow_json 或 workflow_path 二选一） */
export function autoNodeMapping(
  body: ComfyUIAutoNodeMappingRequest,
): Promise<ComfyUIAutoNodeMappingResponse> {
  return client.post<ComfyUIAutoNodeMappingResponse>(
    '/api/config/comfyui/auto-node-mapping',
    body,
  );
}

/** POST /api/config/comfyui/auto-parameterize-workflow — 自动参数化工作流（替换占位符 + 可选节点映射）。
 * mode='all' 改占位符与映射；'prompt_only' 仅改提示词占位符且 node_mapping 置空。 */
export function autoParameterizeWorkflow(
  body: ComfyUIAutoParameterizeRequest,
): Promise<ComfyUIAutoParameterizeResponse> {
  return client.post<ComfyUIAutoParameterizeResponse>(
    '/api/config/comfyui/auto-parameterize-workflow',
    body,
  );
}

/** POST /api/config/comfyui/download-lora — 通过 ComfyUI-Manager 下载 LoRA（队列安装，非即时；需刷新 config 始见） */
export function downloadLora(
  body: ComfyUILoraDownloadRequest,
): Promise<ComfyUILoraDownloadResponse> {
  return client.post<ComfyUILoraDownloadResponse>(
    '/api/config/comfyui/download-lora',
    body,
  );
}
