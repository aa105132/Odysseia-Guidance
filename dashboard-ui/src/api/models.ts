/* models.ts — 后端 Pydantic 模型的 TS 镜像
 * 字段全可选，对应后端 PATCH/PUT 语义（Update 模型均为 Optional）。
 * 响应里多出的只读字段（*_masked / has_* / service_available / available_*）一并保留，
 * 便于前端直接绑定表单与状态徽标，无需二次映射。
 * TODO: 后续用 gen:types (openapi-typescript http://localhost:8080/openapi.json -o src/api/schema.ts) 自动生成替换手写镜像。 */
// 顶层类型由各 domain 文件复用；SystemCurrent/SystemInfoResponse 已在 domains/system.ts 定义，
// 此处仅 re-export 统一入口，避免重复声明。
export type { SystemCurrent, SystemInfoResponse } from './domains/system';

// --- 通用子结构 ---

/** 供应商选项（voice/embedding 的 available_providers） */
export interface ProviderOption {
  id?: string;
  name?: string;
  default_model?: string;
}

/** 表情映射条目（emoji 的 default_mappings / faction_mappings 内层） */
export interface EmojiMapping {
  placeholder?: string;
  discord_emojis?: string[];
  preview?: string;
}

// --- AI 配置（GET/PUT /api/config/ai） ---

export interface AIConfig {
  model?: string;
  temperature?: number;
  max_tokens?: number;
  summary_model?: string;
  query_model?: string;
  persona_name?: string;
  api_url?: string;
  api_url_masked?: string;
  api_key?: string;
  api_key_masked?: string;
  has_api_key?: boolean;
  api_format?: string;
  available_models?: string[];
  channel_history_limit?: number;
  newspaper_brief_threshold?: number;
  long_reply_in_dm_enabled?: boolean;
  max_attempts_per_key?: number;
  retry_delay_seconds?: number;
  max_key_rotation_retries?: number;
}

// --- Imagen 配置（GET/PUT /api/config/imagen） ---

export interface ImagenConfig {
  enabled?: boolean;
  api_url?: string;
  api_url_masked?: string;
  api_key?: string;
  api_key_masked?: string;
  has_api_key?: boolean;
  model?: string;
  edit_model?: string;
  default_images?: number;
  aspect_ratios?: Record<string, unknown>;
  api_format?: string;
  service_available?: boolean;
  generation_cost?: number;
  edit_cost?: number;
  max_images?: number;
  streaming_enabled?: boolean;
  image_response_format?: string;
  // 分辨率模型
  model_2k?: string;
  model_4k?: string;
  edit_model_2k?: string;
  edit_model_4k?: string;
  // SFW/NSFW 完整矩阵
  sfw_model?: string;
  sfw_edit_model?: string;
  sfw_model_2k?: string;
  sfw_edit_model_2k?: string;
  sfw_model_4k?: string;
  sfw_edit_model_4k?: string;
  nsfw_model?: string;
  nsfw_edit_model?: string;
  nsfw_model_2k?: string;
  nsfw_edit_model_2k?: string;
  nsfw_model_4k?: string;
  nsfw_edit_model_4k?: string;
  // 运行参数
  empty_result_max_retries?: number;
  request_timeout?: number;
  streaming_timeout?: number;
  connect_timeout?: number;
  transient_max_retries?: number;
  // NovelAI 开关
  novelai_enabled?: boolean;
  available_models?: string[];
}

// --- 语音合成配置（GET/PUT /api/config/voice） ---

export interface VoiceConfig {
  enabled?: boolean;
  provider?: string;
  base_url?: string;
  api_key?: string;
  api_key_masked?: string;
  has_api_key?: boolean;
  model_name?: string;
  app_id?: string;
  access_token?: string;
  access_token_masked?: string;
  has_access_token?: boolean;
  app_pool?: string[];
  app_pool_count?: number;
  app_default_voice_types?: Record<string, string>;
  clone_voice_app_bindings?: Record<string, string>;
  cluster?: string;
  clone_cluster?: string;
  clone_resource_id?: string;
  voice_type?: string;
  available_voice_types?: string[];
  voice_type_hints?: Record<string, string>;
  extra_body?: Record<string, unknown>;
  siliconflow_references?: unknown[];
  audio_format?: string;
  speed_ratio?: number;
  volume_ratio?: number;
  pitch_ratio?: number;
  emotion?: string;
  enable_emotion?: boolean;
  emotion_scale?: number;
  generation_cost?: number;
  max_text_length?: number;
  request_timeout_seconds?: number;
  service_available?: boolean;
  available_providers?: ProviderOption[];
}

// --- 视频生成配置（GET/PUT /api/config/video） ---

export interface VideoConfig {
  enabled?: boolean;
  api_url?: string;
  api_key_masked?: string;
  has_api_key?: boolean;
  model?: string;
  i2v_model?: string;
  api_format?: string;
  video_format?: string;
  generation_cost?: number;
  max_duration?: number;
  default_videos?: number;
  max_concurrent_tasks?: number;
  empty_result_max_retries?: number;
  service_available?: boolean;
}

// --- NovelAI 配置（GET/PUT /api/config/novelai） ---

export interface NovelAIConfig {
  enabled?: boolean;
  /** 写入型：NovelAI 账户 API Token（GET 不回传明文，仅 masked）；PUT 时填入才生效 */
  api_token?: string;
  api_token_masked?: string;
  has_api_token?: boolean;
  model?: string;
  default_width?: number;
  default_height?: number;
  default_steps?: number;
  default_scale?: number;
  default_sampler?: string;
  quality_toggle?: boolean;
  uc_preset?: number;
  cfg_rescale?: number;
  noise_schedule?: string;
  smea?: boolean;
  smea_dyn?: boolean;
  generation_cost?: number;
  default_negative_prompt?: string;
  default_artist_string?: string;
  prompt_model?: string;
  effective_prompt_model?: string;
  prompt_api_url?: string;
  /** 写入型：提示词生成专用 API Key（GET 不回传明文，仅 masked）；PUT 时填入才生效 */
  prompt_api_key?: string;
  prompt_api_key_masked?: string;
  has_prompt_api_key?: boolean;
  use_prompt_model_in_chat_tool?: boolean;
  max_retries?: number;
  empty_result_max_retries?: number;
  service_available?: boolean;
  available_models?: string[];
  available_samplers?: string[];
  available_noise_schedules?: string[];
}

/** PUT /api/config/novelai 响应：仅回 {success, updated}，保存后需重新 GET 刷新 masked 字段 */
export interface NovelAISaveResponse {
  success: boolean;
  updated?: Record<string, unknown>;
}

/** POST /api/config/test-novelai 响应（service.test_connection：成功含订阅等级与 Anlas） */
export interface NovelAITestResponse {
  success: boolean;
  message?: string;
  error?: string;
}

// --- NovelAI 预设（/api/config/novelai/admin-presets、/api/config/novelai/presets） ---

/** 管理员画师串预设（GET admin-presets 列表项；database.py L2804） */
export interface NovelAIAdminPreset {
  id: number;
  name: string;
  artist_string: string;
  negative_prompt?: string;
  created_at?: string;
}

/** 用户预设（GET presets 列表项），在管理员预设基础上多 user_id（database.py L2774） */
export interface NovelAIUserPreset extends NovelAIAdminPreset {
  user_id?: string | number;
}

/** 管理员预设 upsert 请求体（POST upsert-by-name / PUT update-by-id；api.py L274） */
export interface NovelAIAdminPresetUpsert {
  name: string;
  artist_string: string;
  negative_prompt?: string;
}

/** 管理员预设列表响应（{presets, total}，无分页） */
export interface NovelAIPresetListResponse {
  presets: NovelAIAdminPreset[];
  total: number;
}

/** 用户预设列表响应（presets 含 user_id） */
export interface NovelAIUserPresetListResponse {
  presets: NovelAIUserPreset[];
  total: number;
}

/** 预设增删改响应（POST/PUT 回 {success, name/id}，DELETE 回 {success}） */
export interface NovelAIPresetMutationResponse {
  success: boolean;
  name?: string;
  id?: number;
}

// --- ComfyUI 配置（GET/PUT /api/config/comfyui） ---

/** 节点映射：key=参数键，value=[node_id, field_name]（后端 Dict[str, list[str]]） */
export type ComfyUINodeMapping = Record<string, [string, string]>;

export interface ComfyUIConfig {
  enabled?: boolean;
  enable_slash_command?: boolean;
  server_address?: string;
  workflow_path?: string;
  default_realistic_workflow_path?: string;
  default_anime_workflow_path?: string;
  workflow_exists?: boolean;
  available_workflow_paths?: string[];
  image_output_node_id?: string;
  generation_cost?: number;
  default_width?: number;
  default_height?: number;
  default_steps?: number;
  default_cfg?: number;
  default_sampler?: string;
  default_scheduler?: string;
  default_seed?: number;
  default_model_name?: string;
  default_realistic_model_name?: string;
  default_anime_model_name?: string;
  default_lora?: string;
  default_lora_strength?: number;
  max_user_lora_uploads?: number;
  fixed_positive_prompt?: string;
  fixed_negative_prompt?: string;
  request_timeout_seconds?: number;
  poll_interval_seconds?: number;
  placeholder_mapping?: Record<string, string>;
  node_mapping?: ComfyUINodeMapping;
  service_available?: boolean;
  available_model_names?: string[];
  available_lora_names?: string[];
  /** 写入型：工作流导入时携带的 JSON 字符串，GET 不回传；保存后清空 */
  workflow_json?: string;
  /** 写入型：导入工作流时的目标文件名，GET 不回传 */
  workflow_filename?: string;
  /** 写入型：导入工作流时是否同时自动识别节点映射 */
  auto_detect_node_mapping?: boolean;
}

/** PUT /api/config/comfyui 响应：仅回 {success, updated}，updated 含已变更字段 + 热重载结果 */
export interface ComfyUIConfigSaveResponse {
  success: boolean;
  updated?: Record<string, unknown>;
  message?: string;
}

/** GET /api/config/comfyui/workflow-content 响应 */
export interface ComfyUIWorkflowContentResponse {
  success: boolean;
  workflow_path: string;
  workflow_name: string;
  workflow_json: string;
}

/** DELETE /api/config/comfyui/workflow 响应 */
export interface ComfyUIDeleteWorkflowResponse {
  success: boolean;
  deleted_workflow_path: string;
  deleted_workflow_name: string;
  cleared_config_keys: string[];
  service_reloaded: boolean;
  service_reload_error?: string;
}

/** POST /api/config/test-comfyui 响应 */
export interface ComfyUITestResponse {
  success: boolean;
  message?: string;
  error?: string;
  url?: string;
  workflow_loaded?: boolean;
  workflow_path?: string;
  available_model_names?: string[];
  available_lora_names?: string[];
  assets_error?: string;
}

/** POST /api/config/comfyui/auto-node-mapping 请求 */
export interface ComfyUIAutoNodeMappingRequest {
  workflow_json?: string;
  workflow_path?: string;
}

/** POST /api/config/comfyui/auto-node-mapping 响应 */
export interface ComfyUIAutoNodeMappingResponse {
  success: boolean;
  node_mapping: ComfyUINodeMapping;
  mapped_keys: string[];
}

/** POST /api/config/comfyui/auto-parameterize-workflow 请求 */
export interface ComfyUIAutoParameterizeRequest {
  workflow_json?: string;
  workflow_path?: string;
  placeholder_mapping?: Record<string, string>;
  mode?: 'all' | 'prompt_only' | 'prompt-only' | 'prompt';
}

/** POST /api/config/comfyui/auto-parameterize-workflow 响应 */
export interface ComfyUIAutoParameterizeResponse {
  success: boolean;
  workflow_json: string;
  placeholder_mapping: Record<string, string>;
  node_mapping: ComfyUINodeMapping;
  mapped_keys: string[];
  replaced_keys: string[];
  skipped_keys: string[];
  prompt_only_mode: boolean;
}

/** POST /api/config/comfyui/download-lora 请求 */
export interface ComfyUILoraDownloadRequest {
  url: string;
  filename?: string;
  save_path?: string;
}

/** POST /api/config/comfyui/download-lora 响应（成功有两种模式：Manager 队列 / 回退直链） */
export interface ComfyUILoraDownloadResponse {
  success: boolean;
  message?: string;
  error?: string;
  install_result?: Record<string, unknown>;
  queue_start_status?: Record<string, unknown>;
  queue_start_result?: Record<string, unknown>;
  queue_start_warning?: string;
  endpoint?: string;
  whitelist_matched?: boolean;
  saved_filename?: string;
  saved_path?: string;
  fallback_mode?: string;
}

// --- 网络搜索配置（GET/PUT /api/config/web-search） ---

export interface WebSearchConfig {
  grok_api_url?: string;
  /** 写入型：GET 不回传明文（仅 masked），表单占位用，用户填入才进 dirty payload */
  grok_api_key?: string;
  grok_api_key_masked?: string;
  has_grok_api_key?: boolean;
  grok_model?: string;
  tavily_api_url?: string;
  /** 写入型：同 grok_api_key */
  tavily_api_key?: string;
  tavily_api_key_masked?: string;
  has_tavily_api_key?: boolean;
  grok_configured?: boolean;
  tavily_configured?: boolean;
  search_history_fallback_fetch_limit?: number;
  show_sources?: boolean;
}

// --- 图片搜索配置（GET/PUT /api/config/image-search） ---

export interface ImageSearchConfig {
  api_url?: string;
  /** 写入型：GET 不回传明文（仅 masked），表单占位用，用户填入才进 dirty payload */
  api_key?: string;
  api_key_masked?: string;
  has_api_key?: boolean;
  model?: string;
  max_results?: number;
  timeout_seconds?: number;
  extra_body?: Record<string, unknown>;
  configured?: boolean;
}

// --- 货币配置（GET/PUT /api/config/coin） ---
// ghost_card_image_urls 为动态键（后端 **resolved_ghost_card_image_urls 展开），故放宽索引。

export interface CoinConfig {
  daily_reward?: number;
  chat_reward?: number;
  max_loan?: number;
  feeding_response_image_url?: string;
  confession_response_image_url?: string;
  loan_thumbnail_url?: string;
  feeding_imagen_enabled?: boolean;
  summary_imagen_enabled?: boolean;
  summary_imagen_resolution?: string;
  summary_imagen_model?: string;
  feeding_cooldown_seconds?: number;
  feeding_daily_limit?: number;
  currency_name?: string;
  tax_rate?: number;
  [key: string]: unknown; // 动态 ghost card 图片 URL
}

// --- 管理配置（GET/PUT /api/config/moderation） ---

export interface ModerationConfig {
  warning_threshold?: number;
  ban_duration_min?: number;
  ban_duration_max?: number;
  image_feedback_enabled?: boolean;
  image_feedback_ban_trigger_count?: number;
  image_feedback_repeat_window_minutes?: number;
  image_feedback_ban_ladder_minutes?: number[];
}

// --- 表情配置（GET/PUT /api/config/emoji） ---

export interface EmojiConfig {
  default_mappings?: EmojiMapping[];
  faction_mappings?: Record<string, Record<string, EmojiMapping[]>>;
  available_placeholders?: string[];
}

// --- 向量嵌入配置（GET/PUT /api/config/embedding） ---

export interface EmbeddingConfig {
  enabled?: boolean;
  provider?: string;
  api_url?: string;
  api_url_masked?: string;
  api_key_masked?: string;
  has_api_key?: boolean;
  model?: string;
  dimensions?: number;
  available_providers?: ProviderOption[];
  available_models?: Record<string, string[]>;
}

// --- 帖子自动发言配置（GET/PUT /api/config/thread-auto-speaker） ---

export interface ThreadAutoSpeakerConfig {
  enabled?: boolean;
  thread_ids?: string[];
  check_interval_seconds?: number;
  message_interval_seconds?: number;
  idle_trigger_seconds?: number;
  idle_reminder_seconds?: number;
  context_message_limit?: number;
  new_thread_comment_enabled?: boolean;
  new_thread_comment_delay_seconds?: number;
  new_thread_reply_mode?: string;
  new_thread_style_focus?: string;
  new_thread_include_question_answer?: boolean;
  new_thread_reply_max_chars?: number;
  new_thread_rag_enabled?: boolean;
  new_thread_rag_n_results?: number;
}

// --- 新春活动配置（GET/PUT /api/config/spring-festival） ---

export interface SpringFestivalConfig {
  enabled?: boolean;
  daily_limit_enabled?: boolean;
  min_reward?: number;
  max_reward?: number;
  dm_title?: string;
  dm_description?: string;
  button_label?: string;
  claimed_label?: string;
  reward_reason?: string;
}

// --- 年度总结配置（GET/PUT /api/config/summary） ---

export interface SummaryConfigStats {
  total_generated?: number;
  unique_users?: number;
}

export interface SummaryConfig {
  enabled?: boolean;
  year?: number;
  generation_limit?: number;
  tier2_threshold?: number;
  stats?: SummaryConfigStats;
}

// --- 每日换装配置（GET/PUT /api/config/daily-outfit） ---

export interface DailyOutfitCurrent {
  name?: string;
  description?: string;
  tags?: string;
  last_change_time?: string;
}

export interface DailyOutfitConfig {
  enabled?: boolean;
  schedule_hour?: number;
  schedule_minute?: number;
  designer_api_url?: string;
  designer_api_key_masked?: string;
  designer_model?: string;
  style_preference?: string;
  custom_prompt?: string;
  notification_channel_id?: number;
  designer_system_prompt?: string;
  designer_user_template?: string;
  designer_system_prompt_is_default?: boolean;
  designer_user_template_is_default?: boolean;
  current_outfit?: DailyOutfitCurrent;
}

// --- 知识库文档（/api/knowledge/documents） ---

/** 列表项（GET /api/knowledge/documents） */
export interface KnowledgeDoc {
  id?: number;
  external_id?: string;
  title?: string;
  preview?: string;
  category?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** 详情（GET /api/knowledge/documents/{id}），在列表项基础上补全正文与分块数 */
export interface KnowledgeDocDetail extends KnowledgeDoc {
  content?: string;
  metadata?: Record<string, unknown> | null;
  chunk_count?: number;
}

export interface KnowledgeDocListResponse {
  documents?: KnowledgeDoc[];
  total?: number;
  page?: number;
  page_size?: number;
  total_pages?: number;
  total_chunks?: number;
}

export interface KnowledgeDocCreate {
  title: string;
  content: string;
  category?: string | null;
}

export interface KnowledgeDocUpdate {
  title?: string;
  content?: string;
}

export interface KnowledgeDocMutationResponse {
  success?: boolean;
  id?: number;
  external_id?: string;
  message?: string;
}

export interface KnowledgeStatsResponse {
  total_documents?: number;
  total_chunks?: number;
  by_source?: Record<string, number>;
  recent_documents?: Array<{ title?: string; created_at?: string }>;
}

// --- 系统运维 ---

/** /api/bot/restart 与 /api/bot/shutdown 的统一响应 */
export interface BotActionResponse {
  success: boolean;
  message: string;
}

// --- 全量配置快照（GET /api/config/all） ---
// 后端 payload 仅含下列 section；novelai/comfyui/embedding/summary 等需走各自单端点。

export interface ConfigSnapshot {
  ai?: AIConfig;
  imagen?: ImagenConfig;
  voice?: VoiceConfig;
  coin?: CoinConfig;
  moderation?: ModerationConfig;
  spring_festival?: SpringFestivalConfig;
  shop?: { items?: unknown[] };
  web_search?: WebSearchConfig;
  image_search?: ImageSearchConfig;
  thread_auto_speaker?: ThreadAutoSpeakerConfig;
}
