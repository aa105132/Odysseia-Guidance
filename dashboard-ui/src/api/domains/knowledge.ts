/* knowledge.ts — 知识库文档领域 API
 * 对应 /api/knowledge/documents 与 /api/knowledge/stats。
 * 列表分页参数走 query string；create/update body 见 KnowledgeDocCreate/Update。 */
import { client } from '../client';
import type {
  KnowledgeDocDetail,
  KnowledgeDocListResponse,
  KnowledgeDocCreate,
  KnowledgeDocUpdate,
  KnowledgeDocMutationResponse,
  KnowledgeStatsResponse,
} from '../models';

export interface ListDocumentsParams {
  page?: number;
  page_size?: number;
  search?: string;
}

/** GET /api/knowledge/documents — 分页列表（支持 title/full_text 模糊搜索） */
export function listDocuments(params: ListDocumentsParams = {}): Promise<KnowledgeDocListResponse> {
  const qs = new URLSearchParams();
  if (params.page != null) qs.set('page', String(params.page));
  if (params.page_size != null) qs.set('page_size', String(params.page_size));
  if (params.search) qs.set('search', params.search);
  const query = qs.toString();
  return client.get<KnowledgeDocListResponse>(
    query ? `/api/knowledge/documents?${query}` : '/api/knowledge/documents',
  );
}

/** GET /api/knowledge/documents/{id} — 单文档详情（含正文与分块数） */
export function getDocument(id: number): Promise<KnowledgeDocDetail> {
  return client.get<KnowledgeDocDetail>(`/api/knowledge/documents/${id}`);
}

/** POST /api/knowledge/documents — 创建文档（需后续运行嵌入脚本生成向量分块） */
export function createDocument(body: KnowledgeDocCreate): Promise<KnowledgeDocMutationResponse> {
  return client.post<KnowledgeDocMutationResponse>('/api/knowledge/documents', body);
}

/** PUT /api/knowledge/documents/{id} — 更新文档（title/content 任选） */
export function updateDocument(
  id: number,
  body: KnowledgeDocUpdate,
): Promise<KnowledgeDocMutationResponse> {
  return client.put<KnowledgeDocMutationResponse>(`/api/knowledge/documents/${id}`, body);
}

/** DELETE /api/knowledge/documents/{id} — 删除文档及其所有分块 */
export function deleteDocument(id: number): Promise<KnowledgeDocMutationResponse> {
  return client.delete<KnowledgeDocMutationResponse>(`/api/knowledge/documents/${id}`);
}

/** GET /api/knowledge/stats — 知识库统计（文档/分块总数、按来源分类、最近文档） */
export function getStats(): Promise<KnowledgeStatsResponse> {
  return client.get<KnowledgeStatsResponse>('/api/knowledge/stats');
}

// 阶段3侦察确认：无独立 search 端点（搜索即 listDocuments 的 search 参数），
// 无 rebuild-embeddings 端点（创建/更新后须手动运行 scripts/re_embed_knowledge.py）。
// 故本域端点已齐，无需扩充。
