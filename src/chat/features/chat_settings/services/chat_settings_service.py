import discord
import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta, timezone
import re
from src.chat.utils.database import chat_db_manager
from src.chat.services.event_service import event_service
from src import config
from src.chat.config import chat_config
from src.chat.features.games.config.text_config import (
    apply_ghost_card_image_urls,
    get_ghost_card_image_urls,
)


import logging

log = logging.getLogger(__name__)


def _parse_id_set_from_text(raw_value: Optional[str]) -> set[int]:
    """将逗号/空白分隔的 ID 字符串解析为整数集合。"""
    if not raw_value:
        return set()

    parsed_ids: set[int] = set()
    for token in re.split(r"[\s,]+", raw_value.strip()):
        if not token:
            continue
        try:
            parsed_ids.add(int(token))
        except ValueError:
            continue
    return parsed_ids


class ChatSettingsService:
    """封装聊天设置相关的所有业务逻辑。"""

    def __init__(self):
        self.db_manager = chat_db_manager
    
    async def load_config_from_database(self):
        """
        启动时从数据库加载持久化的配置到内存中。
        这确保 Dashboard 保存的配置在重启后生效。
        """
        log.info("正在从数据库加载持久化配置...")
        
        # --- AI 配置 ---
        db_model = await self.db_manager.get_global_setting("ai_model")
        if db_model:
            chat_config.GEMINI_MODEL = db_model
            chat_config.PROMPT_CONFIG["model"] = db_model
            log.info(f"  ✅ AI 模型: {db_model}")
        
        db_temperature = await self.db_manager.get_global_setting("ai_temperature")
        if db_temperature:
            chat_config.PROMPT_CONFIG["temperature"] = float(db_temperature)
            log.info(f"  ✅ Temperature: {db_temperature}")
        
        db_max_tokens = await self.db_manager.get_global_setting("ai_max_tokens")
        if db_max_tokens:
            chat_config.PROMPT_CONFIG["max_output_tokens"] = int(db_max_tokens)
            log.info(f"  ✅ Max Tokens: {db_max_tokens}")
        
        db_summary_model = await self.db_manager.get_global_setting("summary_model")
        if db_summary_model:
            chat_config.SUMMARY_MODEL = db_summary_model
            log.info(f"  ✅ 摘要模型: {db_summary_model}")
        
        db_query_model = await self.db_manager.get_global_setting("query_model")
        if db_query_model:
            chat_config.QUERY_REWRITING_MODEL = db_query_model
            log.info(f"  ✅ 查询重写模型: {db_query_model}")
        
        # API URL 和 Key（用于自定义端点）
        db_api_url = await self.db_manager.get_global_setting("gemini_api_url")
        if db_api_url:
            chat_config._db_api_url = db_api_url
            log.info(f"  ✅ API URL: {db_api_url[:30]}...")
        
        db_api_key = await self.db_manager.get_global_setting("gemini_api_key")
        if db_api_key:
            chat_config._db_api_key = db_api_key
            log.info(f"  ✅ API Key: 已加载")
        
        db_api_format = await self.db_manager.get_global_setting("ai_api_format")
        if db_api_format:
            chat_config._db_api_format = db_api_format
            log.info(f"  ✅ API 格式: {db_api_format}")

        db_max_attempts_per_key = await self.db_manager.get_global_setting(
            "ai_max_attempts_per_key"
        )
        if db_max_attempts_per_key is not None:
            try:
                parsed_attempts = int(db_max_attempts_per_key)
                if parsed_attempts >= 1:
                    chat_config.API_RETRY_CONFIG["MAX_ATTEMPTS_PER_KEY"] = parsed_attempts
                    log.info(f"  ✅ 主聊天单密钥重试次数: {parsed_attempts}")
            except (TypeError, ValueError):
                log.warning(f"主聊天单密钥重试次数解析失败: {db_max_attempts_per_key}")

        db_retry_delay_seconds = await self.db_manager.get_global_setting(
            "ai_retry_delay_seconds"
        )
        if db_retry_delay_seconds is not None:
            try:
                parsed_delay = int(db_retry_delay_seconds)
                if parsed_delay >= 0:
                    chat_config.API_RETRY_CONFIG["RETRY_DELAY_SECONDS"] = parsed_delay
                    log.info(f"  ✅ 主聊天重试延迟: {parsed_delay}s")
            except (TypeError, ValueError):
                log.warning(f"主聊天重试延迟解析失败: {db_retry_delay_seconds}")

        db_max_key_rotation_retries = await self.db_manager.get_global_setting(
            "ai_max_key_rotation_retries"
        )
        if db_max_key_rotation_retries is not None:
            try:
                parsed_rotation = int(db_max_key_rotation_retries)
                if parsed_rotation >= 1:
                    chat_config.API_RETRY_CONFIG[
                        "MAX_KEY_ROTATION_RETRIES"
                    ] = parsed_rotation
                    log.info(f"  ✅ 主聊天轮换重试次数: {parsed_rotation}")
            except (TypeError, ValueError):
                log.warning(f"主聊天轮换重试次数解析失败: {db_max_key_rotation_retries}")
        
        # --- Imagen 配置 ---
        db_imagen_enabled = await self.db_manager.get_global_setting("imagen_enabled")
        if db_imagen_enabled:
            chat_config.GEMINI_IMAGEN_CONFIG["ENABLED"] = db_imagen_enabled == "true"
            log.info(f"  ✅ Imagen 启用状态: {db_imagen_enabled}")
        
        db_imagen_url = await self.db_manager.get_global_setting("imagen_api_url")
        if db_imagen_url:
            chat_config.GEMINI_IMAGEN_CONFIG["BASE_URL"] = db_imagen_url
            log.info(f"  ✅ Imagen API URL: {db_imagen_url[:30]}...")
        
        db_imagen_key = await self.db_manager.get_global_setting("imagen_api_key")
        if db_imagen_key:
            chat_config.GEMINI_IMAGEN_CONFIG["API_KEY"] = db_imagen_key
            log.info(f"  ✅ Imagen API Key: 已加载")
        
        db_imagen_model = await self.db_manager.get_global_setting("imagen_model")
        if db_imagen_model:
            chat_config.GEMINI_IMAGEN_CONFIG["MODEL_NAME"] = db_imagen_model
            log.info(f"  ✅ Imagen 模型 (T2I): {db_imagen_model}")
        
        db_imagen_edit_model = await self.db_manager.get_global_setting("imagen_edit_model")
        if db_imagen_edit_model:
            chat_config.GEMINI_IMAGEN_CONFIG["EDIT_MODEL_NAME"] = db_imagen_edit_model
            log.info(f"  ✅ Imagen 模型 (I2I): {db_imagen_edit_model}")
        
        db_imagen_format = await self.db_manager.get_global_setting("imagen_api_format")
        if db_imagen_format:
            chat_config.GEMINI_IMAGEN_CONFIG["API_FORMAT"] = db_imagen_format
            log.info(f"  ✅ Imagen API 格式: {db_imagen_format}")

        db_imagen_default_images = await self.db_manager.get_global_setting("imagen_default_images")
        if db_imagen_default_images:
            try:
                chat_config.GEMINI_IMAGEN_CONFIG["DEFAULT_NUMBER_OF_IMAGES"] = int(db_imagen_default_images)
                log.info(f"  ✅ Imagen 默认图片数量: {db_imagen_default_images}")
            except (TypeError, ValueError):
                log.warning(f"Imagen 默认图片数量解析失败: {db_imagen_default_images}")
        
        db_imagen_response_format = await self.db_manager.get_global_setting("imagen_image_response_format")
        if db_imagen_response_format:
            chat_config.GEMINI_IMAGEN_CONFIG["IMAGE_RESPONSE_FORMAT"] = db_imagen_response_format
            log.info(f"  ✅ Imagen 图片响应格式: {db_imagen_response_format}")
        
        db_default_image_engine = await self.db_manager.get_global_setting('default_image_engine')
        if db_default_image_engine:
            normalized_image_engine = str(db_default_image_engine).strip().lower()
            if normalized_image_engine in {'imagen', 'novelai', 'comfyui'}:
                chat_config.DEFAULT_IMAGE_ENGINE = normalized_image_engine
                log.info(f'  ✅ 默认绘图引擎: {normalized_image_engine}')
            else:
                log.warning(f'默认绘图引擎配置无效，已忽略: {db_default_image_engine}')

        # --- ComfyUI 配置 ---
        db_comfy_enabled = await self.db_manager.get_global_setting('comfyui_enabled')
        if db_comfy_enabled is not None:
            chat_config.COMFYUI_CONFIG['ENABLED'] = db_comfy_enabled.lower() == 'true'
            log.info(f'  ✅ ComfyUI 启用状态: {db_comfy_enabled}')

        db_comfy_slash_enabled = await self.db_manager.get_global_setting('comfyui_enable_slash_command')
        if db_comfy_slash_enabled is not None:
            chat_config.COMFYUI_CONFIG['ENABLE_SLASH_COMMAND'] = (
                db_comfy_slash_enabled.lower() == 'true'
            )
            log.info(f'  ✅ ComfyUI 斜杠命令开关: {db_comfy_slash_enabled}')

        db_comfy_server = await self.db_manager.get_global_setting('comfyui_server_address')
        if db_comfy_server:
            chat_config.COMFYUI_CONFIG['SERVER_ADDRESS'] = db_comfy_server.strip()

        db_comfy_workflow_path = await self.db_manager.get_global_setting('comfyui_workflow_path')
        if db_comfy_workflow_path is not None:
            chat_config.COMFYUI_CONFIG['WORKFLOW_PATH'] = str(db_comfy_workflow_path).strip()

        db_comfy_default_realistic_workflow_path = await self.db_manager.get_global_setting(
            'comfyui_default_realistic_workflow_path'
        )
        if db_comfy_default_realistic_workflow_path is not None:
            chat_config.COMFYUI_CONFIG['DEFAULT_REALISTIC_WORKFLOW_PATH'] = (
                str(db_comfy_default_realistic_workflow_path).strip()
            )

        db_comfy_default_anime_workflow_path = await self.db_manager.get_global_setting(
            'comfyui_default_anime_workflow_path'
        )
        if db_comfy_default_anime_workflow_path is not None:
            chat_config.COMFYUI_CONFIG['DEFAULT_ANIME_WORKFLOW_PATH'] = (
                str(db_comfy_default_anime_workflow_path).strip()
            )

        db_comfy_output_node = await self.db_manager.get_global_setting('comfyui_image_output_node_id')
        if db_comfy_output_node is not None:
            chat_config.COMFYUI_CONFIG['IMAGE_OUTPUT_NODE_ID'] = db_comfy_output_node.strip()

        db_comfy_cost = await self.db_manager.get_global_setting('comfyui_generation_cost')
        if db_comfy_cost:
            try:
                chat_config.COMFYUI_CONFIG['IMAGE_GENERATION_COST'] = int(db_comfy_cost)
            except (TypeError, ValueError):
                log.warning(f'ComfyUI 成本解析失败: {db_comfy_cost}')

        numeric_setting_map = [
            ('comfyui_default_width', 'DEFAULT_WIDTH', int),
            ('comfyui_default_height', 'DEFAULT_HEIGHT', int),
            ('comfyui_default_steps', 'DEFAULT_STEPS', int),
            ('comfyui_default_cfg', 'DEFAULT_CFG', float),
            ('comfyui_default_seed', 'DEFAULT_SEED', int),
            ('comfyui_default_lora_strength', 'DEFAULT_LORA_STRENGTH', float),
            ('comfyui_lora_download_max_mb', 'LORA_DOWNLOAD_MAX_MB', int),
            ('comfyui_request_timeout_seconds', 'REQUEST_TIMEOUT_SECONDS', int),
            ('comfyui_poll_interval_seconds', 'POLL_INTERVAL_SECONDS', float),
        ]

        for db_key, config_key, caster in numeric_setting_map:
            raw_value = await self.db_manager.get_global_setting(db_key)
            if raw_value in (None, ''):
                continue
            try:
                chat_config.COMFYUI_CONFIG[config_key] = caster(raw_value)
            except (TypeError, ValueError):
                log.warning(f'ComfyUI 数值配置解析失败: {db_key}={raw_value}')

        string_setting_map = [
            ('comfyui_default_sampler', 'DEFAULT_SAMPLER'),
            ('comfyui_default_scheduler', 'DEFAULT_SCHEDULER'),
            ('comfyui_default_model_name', 'DEFAULT_MODEL_NAME'),
            ('comfyui_default_realistic_model_name', 'DEFAULT_REALISTIC_MODEL_NAME'),
            ('comfyui_default_anime_model_name', 'DEFAULT_ANIME_MODEL_NAME'),
            ('comfyui_default_vae_name', 'DEFAULT_VAE_NAME'),
            ('comfyui_default_clip_name', 'DEFAULT_CLIP_NAME'),
            ('comfyui_default_lora', 'DEFAULT_LORA'),
            ('comfyui_shared_lora_dir', 'SHARED_LORA_DIR'),
        ]

        for db_key, config_key in string_setting_map:
            raw_value = await self.db_manager.get_global_setting(db_key)
            if raw_value is not None:
                chat_config.COMFYUI_CONFIG[config_key] = str(raw_value).strip()

        db_comfy_placeholder_mapping = await self.db_manager.get_global_setting('comfyui_placeholder_mapping')
        if db_comfy_placeholder_mapping:
            try:
                parsed_placeholder_mapping = json.loads(db_comfy_placeholder_mapping)
                if isinstance(parsed_placeholder_mapping, dict):
                    normalized_placeholder_mapping = {}
                    for key, value in parsed_placeholder_mapping.items():
                        key_text = str(key).strip()
                        value_text = str(value).strip()
                        if key_text and value_text:
                            normalized_placeholder_mapping[key_text] = value_text
                    if normalized_placeholder_mapping:
                        chat_config.COMFYUI_CONFIG['PLACEHOLDER_MAPPING'] = normalized_placeholder_mapping
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                log.warning(f'ComfyUI 占位符映射解析失败: {error}')

        db_comfy_node_mapping = await self.db_manager.get_global_setting('comfyui_node_mapping')
        if db_comfy_node_mapping:
            try:
                parsed_node_mapping = json.loads(db_comfy_node_mapping)
                if isinstance(parsed_node_mapping, dict):
                    chat_config.COMFYUI_CONFIG['NODE_MAPPING'] = parsed_node_mapping
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                log.warning(f'ComfyUI 节点映射解析失败: {error}')

        # --- 视频生成配置 ---
        db_video_enabled = await self.db_manager.get_global_setting("video_enabled")
        if db_video_enabled:
            chat_config.VIDEO_GEN_CONFIG["ENABLED"] = db_video_enabled == "true"
            log.info(f"  ✅ 视频生成启用状态: {db_video_enabled}")
        
        db_video_url = await self.db_manager.get_global_setting("video_api_url")
        if db_video_url:
            chat_config.VIDEO_GEN_CONFIG["BASE_URL"] = db_video_url
            log.info(f"  ✅ 视频 API URL: {db_video_url[:30]}...")
        
        db_video_key = await self.db_manager.get_global_setting("video_api_key")
        if db_video_key:
            chat_config.VIDEO_GEN_CONFIG["API_KEY"] = db_video_key
            log.info(f"  ✅ 视频 API Key: 已加载")
        
        db_video_model = await self.db_manager.get_global_setting("video_model")
        if db_video_model:
            chat_config.VIDEO_GEN_CONFIG["MODEL_NAME"] = db_video_model
            log.info(f"  ✅ 视频模型 (T2V): {db_video_model}")
        
        db_video_i2v_model = await self.db_manager.get_global_setting("video_i2v_model")
        if db_video_i2v_model:
            chat_config.VIDEO_GEN_CONFIG["I2V_MODEL_NAME"] = db_video_i2v_model
            log.info(f"  ✅ 视频模型 (I2V): {db_video_i2v_model}")
        
        db_video_format = await self.db_manager.get_global_setting("video_format")
        if db_video_format:
            chat_config.VIDEO_GEN_CONFIG["VIDEO_FORMAT"] = db_video_format
            log.info(f"  ✅ 视频格式: {db_video_format}")
        
        db_video_cost = await self.db_manager.get_global_setting("video_generation_cost")
        if db_video_cost:
            chat_config.VIDEO_GEN_CONFIG["VIDEO_GENERATION_COST"] = int(db_video_cost)
            log.info(f"  ✅ 视频生成成本: {db_video_cost}")
        
        db_video_duration = await self.db_manager.get_global_setting("video_max_duration")
        if db_video_duration:
            chat_config.VIDEO_GEN_CONFIG["MAX_DURATION"] = int(db_video_duration)
            log.info(f"  ✅ 视频最大时长: {db_video_duration}s")

        db_video_default_videos = await self.db_manager.get_global_setting("video_default_videos")
        if db_video_default_videos:
            try:
                chat_config.VIDEO_GEN_CONFIG["DEFAULT_NUMBER_OF_VIDEOS"] = int(db_video_default_videos)
                log.info(f"  ✅ 视频默认生成数量: {db_video_default_videos}")
            except (TypeError, ValueError):
                log.warning(f"视频默认生成数量解析失败: {db_video_default_videos}")

        db_video_max_concurrent_tasks = await self.db_manager.get_global_setting("video_max_concurrent_tasks")
        if db_video_max_concurrent_tasks:
            try:
                chat_config.VIDEO_GEN_CONFIG["MAX_CONCURRENT_VIDEO_TASKS"] = int(db_video_max_concurrent_tasks)
                log.info(f"  ✅ 视频并发上限: {db_video_max_concurrent_tasks}")
            except (TypeError, ValueError):
                log.warning(f"视频并发上限解析失败: {db_video_max_concurrent_tasks}")

        db_history_fallback_limit = await self.db_manager.get_global_setting(
            "search_history_fallback_fetch_limit"
        )
        if db_history_fallback_limit is not None:
            try:
                parsed_history_fallback_limit = int(db_history_fallback_limit)
                if parsed_history_fallback_limit >= 0:
                    chat_config.SEARCH_HISTORY_CONFIG["FALLBACK_FETCH_LIMIT"] = (
                        parsed_history_fallback_limit
                    )
                    log.info(
                        "  ✅ 历史消息回退扫描条数: "
                        f"{parsed_history_fallback_limit} (0=自动)"
                    )
                else:
                    log.warning(
                        f"历史消息回退扫描条数小于 0，忽略: {db_history_fallback_limit}"
                    )
            except (TypeError, ValueError):
                log.warning(f"历史消息回退扫描条数解析失败: {db_history_fallback_limit}")

        # --- 投喂/忏悔/借贷图片配置 ---
        db_feeding_response_image_url = await self.db_manager.get_global_setting(
            "feeding_response_image_url"
        )
        if db_feeding_response_image_url is not None:
            chat_config.FEEDING_CONFIG["RESPONSE_IMAGE_URL"] = db_feeding_response_image_url
            log.info("  ✅ 投喂回应图片 URL: 已加载")

        db_feeding_imagen_enabled = await self.db_manager.get_global_setting(
            "feeding_imagen_enabled"
        )
        if db_feeding_imagen_enabled is not None:
            chat_config.FEEDING_CONFIG["IMAGEN_ENABLED"] = db_feeding_imagen_enabled == "true"
            log.info(f"  ✅ 投喂AI绘图: {'已启用' if chat_config.FEEDING_CONFIG['IMAGEN_ENABLED'] else '已禁用'}")

        db_summary_imagen_enabled = await self.db_manager.get_global_setting(
            "summary_imagen_enabled"
        )
        if db_summary_imagen_enabled is not None:
            chat_config.FEEDING_CONFIG["SUMMARY_IMAGEN_ENABLED"] = db_summary_imagen_enabled == "true"
            log.info(f"  ✅ 总结AI配图: {'已启用' if chat_config.FEEDING_CONFIG['SUMMARY_IMAGEN_ENABLED'] else '已禁用'}")

        db_summary_imagen_resolution = await self.db_manager.get_global_setting(
            "summary_imagen_resolution"
        )
        if db_summary_imagen_resolution is not None:
            chat_config.FEEDING_CONFIG["SUMMARY_IMAGEN_RESOLUTION"] = db_summary_imagen_resolution
            log.info(f"  ✅ 总结配图分辨率: {db_summary_imagen_resolution}")

        db_summary_imagen_model = await self.db_manager.get_global_setting(
            "summary_imagen_model"
        )
        if db_summary_imagen_model is not None:
            chat_config.FEEDING_CONFIG["SUMMARY_IMAGEN_MODEL"] = db_summary_imagen_model
            log.info(f"  ✅ 总结配图模型: {db_summary_imagen_model}")

        db_confession_response_image_url = await self.db_manager.get_global_setting(
            "confession_response_image_url"
        )
        if db_confession_response_image_url is not None:
            chat_config.CONFESSION_CONFIG["RESPONSE_IMAGE_URL"] = db_confession_response_image_url
            log.info("  ✅ 忏悔回应图片 URL: 已加载")

        db_loan_thumbnail_url = await self.db_manager.get_global_setting(
            "coin_loan_thumbnail_url"
        )
        if db_loan_thumbnail_url is not None:
            chat_config.COIN_CONFIG["LOAN_THUMBNAIL_URL"] = db_loan_thumbnail_url
            log.info("  ✅ 借贷中心缩略图 URL: 已加载")

        ghost_card_url_updates: Dict[str, str] = {}
        for setting_key, _current_value in get_ghost_card_image_urls().items():
            db_value = await self.db_manager.get_global_setting(setting_key)
            if db_value is not None:
                ghost_card_url_updates[setting_key] = db_value

        if ghost_card_url_updates:
            apply_ghost_card_image_urls(**ghost_card_url_updates)
            log.info(f"  ✅ 抽鬼牌图片 URL 配置: 已加载 {len(ghost_card_url_updates)} 项")

        # --- 帖子暖贴配置 ---
        db_new_thread_comment_enabled = await self.db_manager.get_global_setting(
            "thread_new_post_comment_enabled"
        )
        if db_new_thread_comment_enabled is not None:
            enabled = db_new_thread_comment_enabled.lower() == "true"
            chat_config.THREAD_COMMENTOR_CONFIG["NEW_THREAD_COMMENT_ENABLED"] = enabled
            log.info(f"  ✅ 新帖自动评价开关: {db_new_thread_comment_enabled}")

        db_new_thread_comment_delay = await self.db_manager.get_global_setting(
            "thread_new_post_comment_delay_seconds"
        )
        if db_new_thread_comment_delay:
            delay_seconds = int(db_new_thread_comment_delay)
            chat_config.THREAD_COMMENTOR_CONFIG[
                "NEW_THREAD_COMMENT_DELAY_SECONDS"
            ] = delay_seconds
            chat_config.THREAD_COMMENTOR_CONFIG["INITIAL_DELAY_SECONDS"] = delay_seconds

        db_new_thread_reply_mode = await self.db_manager.get_global_setting(
            "thread_new_post_reply_mode"
        )
        if db_new_thread_reply_mode is not None:
            chat_config.THREAD_COMMENTOR_CONFIG["NEW_THREAD_REPLY_MODE"] = (
                str(db_new_thread_reply_mode).strip().lower()
            )

        db_new_thread_style_focus = await self.db_manager.get_global_setting(
            "thread_new_post_style_focus"
        )
        if db_new_thread_style_focus is not None:
            chat_config.THREAD_COMMENTOR_CONFIG["NEW_THREAD_STYLE_FOCUS"] = (
                str(db_new_thread_style_focus).strip().lower()
            )

        db_new_thread_include_question_answer = await self.db_manager.get_global_setting(
            "thread_new_post_include_question_answer"
        )
        if db_new_thread_include_question_answer is not None:
            chat_config.THREAD_COMMENTOR_CONFIG["NEW_THREAD_INCLUDE_QUESTION_ANSWER"] = (
                db_new_thread_include_question_answer.lower() == "true"
            )

        db_new_thread_reply_max_chars = await self.db_manager.get_global_setting(
            "thread_new_post_reply_max_chars"
        )
        if db_new_thread_reply_max_chars:
            chat_config.THREAD_COMMENTOR_CONFIG["NEW_THREAD_REPLY_MAX_CHARS"] = int(
                db_new_thread_reply_max_chars
            )

        db_new_thread_rag_enabled = await self.db_manager.get_global_setting(
            "thread_new_post_rag_enabled"
        )
        if db_new_thread_rag_enabled is not None:
            chat_config.THREAD_COMMENTOR_CONFIG["NEW_THREAD_RAG_ENABLED"] = (
                db_new_thread_rag_enabled.lower() == "true"
            )

        db_new_thread_rag_n_results = await self.db_manager.get_global_setting(
            "thread_new_post_rag_n_results"
        )
        if db_new_thread_rag_n_results:
            chat_config.THREAD_COMMENTOR_CONFIG["NEW_THREAD_RAG_N_RESULTS"] = int(
                db_new_thread_rag_n_results
            )

        # --- 自动暖贴配置 ---
        db_auto_enabled = await self.db_manager.get_global_setting(
            "thread_auto_speaker_enabled"
        )
        if db_auto_enabled is not None:
            chat_config.THREAD_COMMENTOR_CONFIG["AUTO_CHAT_ENABLED"] = (
                db_auto_enabled.lower() == "true"
            )
            log.info(f"  ✅ 自动暖贴开关: {db_auto_enabled}")

        db_auto_thread_ids = await self.db_manager.get_global_setting(
            "thread_auto_speaker_thread_ids"
        )
        if db_auto_thread_ids is not None:
            parsed_ids = _parse_id_set_from_text(db_auto_thread_ids)
            chat_config.THREAD_COMMENTOR_CONFIG["AUTO_CHAT_THREAD_IDS"] = parsed_ids
            log.info(f"  ✅ 自动暖贴帖子数: {len(parsed_ids)}")

        db_auto_check_interval = await self.db_manager.get_global_setting(
            "thread_auto_speaker_check_interval_seconds"
        )
        if db_auto_check_interval:
            chat_config.THREAD_COMMENTOR_CONFIG["AUTO_CHAT_CHECK_INTERVAL_SECONDS"] = int(
                db_auto_check_interval
            )

        db_auto_message_interval = await self.db_manager.get_global_setting(
            "thread_auto_speaker_message_interval_seconds"
        )
        if db_auto_message_interval:
            chat_config.THREAD_COMMENTOR_CONFIG[
                "AUTO_CHAT_MESSAGE_INTERVAL_SECONDS"
            ] = int(db_auto_message_interval)

        db_auto_idle_trigger = await self.db_manager.get_global_setting(
            "thread_auto_speaker_idle_trigger_seconds"
        )
        if db_auto_idle_trigger:
            chat_config.THREAD_COMMENTOR_CONFIG["AUTO_CHAT_IDLE_TRIGGER_SECONDS"] = int(
                db_auto_idle_trigger
            )

        db_auto_idle_reminder = await self.db_manager.get_global_setting(
            "thread_auto_speaker_idle_reminder_seconds"
        )
        if db_auto_idle_reminder:
            chat_config.THREAD_COMMENTOR_CONFIG[
                "AUTO_CHAT_IDLE_REMINDER_SECONDS"
            ] = int(db_auto_idle_reminder)

        db_auto_context_limit = await self.db_manager.get_global_setting(
            "thread_auto_speaker_context_message_limit"
        )
        if db_auto_context_limit:
            chat_config.THREAD_COMMENTOR_CONFIG[
                "AUTO_CHAT_CONTEXT_MESSAGE_LIMIT"
            ] = int(db_auto_context_limit)

        # --- 频道消息上下文条数 ---
        db_channel_history_limit = await self.db_manager.get_global_setting(
            "channel_formatted_history_limit"
        )
        if db_channel_history_limit:
            chat_config.CHANNEL_MEMORY_CONFIG["formatted_history_limit"] = int(
                db_channel_history_limit
            )
            # raw_history_limit 与 formatted_history_limit 保持一致
            chat_config.CHANNEL_MEMORY_CONFIG["raw_history_limit"] = int(
                db_channel_history_limit
            )

        db_newspaper_brief_threshold = await self.db_manager.get_global_setting(
            "newspaper_brief_threshold"
        )
        if db_newspaper_brief_threshold:
            chat_config.MESSAGE_SETTINGS["NEWSPAPER_BRIEF_THRESHOLD"] = int(
                db_newspaper_brief_threshold
            )

        db_long_reply_in_dm_enabled = await self.db_manager.get_global_setting(
            "long_reply_in_dm_enabled"
        )
        if db_long_reply_in_dm_enabled is not None:
            chat_config.MESSAGE_SETTINGS["LONG_REPLY_IN_DM_ENABLED"] = (
                db_long_reply_in_dm_enabled.lower() == "true"
            )

        # --- 每日换装配置 ---
        outfit_keys = {
            "daily_outfit_enabled": ("ENABLED", lambda v: v.lower() == "true"),
            "daily_outfit_schedule_hour": ("SCHEDULE_HOUR", int),
            "daily_outfit_schedule_minute": ("SCHEDULE_MINUTE", int),
            "daily_outfit_designer_api_url": ("DESIGNER_API_URL", str),
            "daily_outfit_designer_api_key": ("DESIGNER_API_KEY", str),
            "daily_outfit_designer_model": ("DESIGNER_MODEL", str),
            "daily_outfit_style_preference": ("STYLE_PREFERENCE", str),
            "daily_outfit_custom_prompt": ("CUSTOM_PROMPT", str),
            "daily_outfit_notification_channel_id": ("NOTIFICATION_CHANNEL_ID", int),
            "daily_outfit_designer_system_prompt": ("DESIGNER_SYSTEM_PROMPT", str),
            "daily_outfit_designer_user_template": ("DESIGNER_USER_TEMPLATE", str),
            "daily_outfit_description": ("CURRENT_OUTFIT_DESCRIPTION", str),
            "daily_outfit_tags": ("CURRENT_OUTFIT_TAGS", str),
            "daily_outfit_name": ("CURRENT_OUTFIT_NAME", str),
            "daily_outfit_last_change": ("LAST_CHANGE_TIME", str),
        }
        for db_key, (config_key, converter) in outfit_keys.items():
            val = await self.db_manager.get_global_setting(db_key)
            if val is not None:
                try:
                    chat_config.DAILY_OUTFIT_CONFIG[config_key] = converter(val)
                except (TypeError, ValueError):
                    pass

        log.info("数据库配置加载完成。")

    async def set_entity_settings(
        self,
        guild_id: int,
        entity_id: int,
        entity_type: str,
        is_chat_enabled: Optional[bool],
        cooldown_seconds: Optional[int],
        cooldown_duration: Optional[int],
        cooldown_limit: Optional[int],
    ):
        """设置频道或分类的聊天配置，支持所有CD模式。"""
        await self.db_manager.update_channel_config(
            guild_id=guild_id,
            entity_id=entity_id,
            entity_type=entity_type,
            is_chat_enabled=is_chat_enabled,
            cooldown_seconds=cooldown_seconds,
            cooldown_duration=cooldown_duration,
            cooldown_limit=cooldown_limit,
        )

    async def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        """获取一个服务器的完整聊天设置，包括全局和所有特定频道的配置。"""
        global_config_row = await self.db_manager.get_global_chat_config(guild_id)
        channel_configs_rows = await self.db_manager.get_all_channel_configs_for_guild(
            guild_id
        )
        warm_up_channels = await self.db_manager.get_warm_up_channels(guild_id)

        settings = {
            "global": {
                "chat_enabled": global_config_row["chat_enabled"]
                if global_config_row
                else True,
                "warm_up_enabled": global_config_row["warm_up_enabled"]
                if global_config_row
                else True,
            },
            "channels": {
                config["entity_id"]: {
                    "entity_type": config["entity_type"],
                    "is_chat_enabled": config["is_chat_enabled"],
                    "cooldown_seconds": config["cooldown_seconds"],
                    "cooldown_duration": config["cooldown_duration"],
                    "cooldown_limit": config["cooldown_limit"],
                }
                for config in channel_configs_rows
            },
            "warm_up_channels": warm_up_channels,
        }
        return settings

    async def is_chat_globally_enabled(self, guild_id: int) -> bool:
        """检查聊天功能是否在服务器内全局开启。"""
        config = await self.db_manager.get_global_chat_config(guild_id)
        return config["chat_enabled"] if config else True

    async def is_warm_up_enabled(self, guild_id: int) -> bool:
        """检查暖贴功能是否开启。"""
        config = await self.db_manager.get_global_chat_config(guild_id)
        return config["warm_up_enabled"] if config else True

    async def get_effective_channel_config(
        self, channel: discord.abc.GuildChannel
    ) -> Dict[str, Any]:
        """
        获取频道的最终生效配置。
        优先级: 帖子主人设置 > 频道特定设置 > 分类设置 > 全局默认
        """
        guild_id = channel.guild.id
        channel_id = channel.id

        # 修正：对于帖子（Thread），应从其父频道获取分类ID
        if isinstance(channel, discord.Thread):
            channel_category_id = channel.parent.category_id if channel.parent else None
        else:
            channel_category_id = (
                channel.category_id if hasattr(channel, "category_id") else None
            )

        # 默认配置
        effective_config = {
            "is_chat_enabled": True,
            "cooldown_seconds": 0,
            "cooldown_duration": None,
            "cooldown_limit": None,
        }

        # 1. 获取分类配置
        category_config = None
        if channel_category_id:
            category_config = await self.db_manager.get_channel_config(
                guild_id, channel_category_id
            )

        if category_config:
            if category_config["is_chat_enabled"] is not None:
                effective_config["is_chat_enabled"] = category_config["is_chat_enabled"]
            if category_config["cooldown_seconds"] is not None:
                effective_config["cooldown_seconds"] = category_config[
                    "cooldown_seconds"
                ]
            if category_config["cooldown_duration"] is not None:
                effective_config["cooldown_duration"] = category_config[
                    "cooldown_duration"
                ]
            if category_config["cooldown_limit"] is not None:
                effective_config["cooldown_limit"] = category_config["cooldown_limit"]

        # 2. 获取频道特定配置，并覆盖分类配置
        channel_config = await self.db_manager.get_channel_config(guild_id, channel_id)
        if channel_config:
            if channel_config["is_chat_enabled"] is not None:
                effective_config["is_chat_enabled"] = channel_config["is_chat_enabled"]
            if channel_config["cooldown_seconds"] is not None:
                effective_config["cooldown_seconds"] = channel_config[
                    "cooldown_seconds"
                ]
            if channel_config["cooldown_duration"] is not None:
                effective_config["cooldown_duration"] = channel_config[
                    "cooldown_duration"
                ]
            if channel_config["cooldown_limit"] is not None:
                effective_config["cooldown_limit"] = channel_config["cooldown_limit"]

        # 3. 如果是帖子，获取并应用帖子主人的个人设置 (最高优先级)
        if isinstance(channel, discord.Thread) and channel.owner_id:
            owner_id = channel.owner_id
            query = "SELECT thread_cooldown_seconds, thread_cooldown_duration, thread_cooldown_limit FROM user_coins WHERE user_id = ?"
            owner_config_row = await self.db_manager._execute(
                self.db_manager._db_transaction, query, (owner_id,), fetch="one"
            )

            if owner_config_row:
                # 个人设置不包含 is_chat_enabled，只覆盖CD
                has_personal_fixed_cd = (
                    owner_config_row["thread_cooldown_seconds"] is not None
                )
                has_personal_freq_cd = (
                    owner_config_row["thread_cooldown_duration"] is not None
                    and owner_config_row["thread_cooldown_limit"] is not None
                )

                if has_personal_fixed_cd:
                    effective_config["cooldown_seconds"] = owner_config_row[
                        "thread_cooldown_seconds"
                    ]
                    effective_config["cooldown_duration"] = None
                    effective_config["cooldown_limit"] = None
                elif has_personal_freq_cd:
                    effective_config["cooldown_seconds"] = 0
                    effective_config["cooldown_duration"] = owner_config_row[
                        "thread_cooldown_duration"
                    ]
                    effective_config["cooldown_limit"] = owner_config_row[
                        "thread_cooldown_limit"
                    ]

        return effective_config

    async def is_user_on_cooldown(
        self, user_id: int, channel_id: int, config: Dict[str, Any]
    ) -> bool:
        """
        根据提供的配置，智能检查用户是否处于冷却状态。
        优先使用频率限制模式，否则回退到固定时长模式。
        """
        duration = config.get("cooldown_duration")
        limit = config.get("cooldown_limit")
        cooldown_seconds = config.get("cooldown_seconds")

        # --- 模式1: 频率限制 ---
        if duration is not None and limit is not None and duration > 0 and limit > 0:
            timestamps = await self.db_manager.get_user_timestamps_in_window(
                user_id, channel_id, duration
            )
            return len(timestamps) >= limit

        # --- 模式2: 固定时长 ---
        if cooldown_seconds is not None and cooldown_seconds > 0:
            last_message_row = await self.db_manager.get_user_cooldown(
                user_id, channel_id
            )
            if not last_message_row or not last_message_row["last_message_timestamp"]:
                return False

            last_message_time = datetime.fromisoformat(
                last_message_row["last_message_timestamp"]
            ).replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < last_message_time + timedelta(
                seconds=cooldown_seconds
            ):
                return True

        return False

    async def update_user_cooldown(
        self, user_id: int, channel_id: int, config: Dict[str, Any]
    ):
        """
        根据当前生效的CD模式，更新用户的冷却记录。
        """
        duration = config.get("cooldown_duration")
        limit = config.get("cooldown_limit")

        # 如果是频率限制模式，则添加时间戳
        if duration is not None and limit is not None and duration > 0 and limit > 0:
            await self.db_manager.add_user_timestamp(user_id, channel_id)

        # 总是更新固定CD的时间戳，以备模式切换或用于其他目的
        await self.db_manager.update_user_cooldown(user_id, channel_id)

    async def get_warm_up_channels(self, guild_id: int) -> List[int]:
        """获取服务器的所有暖贴频道ID。"""
        return await self.db_manager.get_warm_up_channels(guild_id)

    async def add_warm_up_channel(self, guild_id: int, channel_id: int):
        """添加一个暖贴频道。"""
        await self.db_manager.add_warm_up_channel(guild_id, channel_id)

    async def remove_warm_up_channel(self, guild_id: int, channel_id: int):
        """移除一个暖贴频道。"""
        await self.db_manager.remove_warm_up_channel(guild_id, channel_id)

    async def is_warm_up_channel(self, guild_id: int, channel_id: int) -> bool:
        """检查一个频道是否是暖贴频道。"""
        return await self.db_manager.is_warm_up_channel(guild_id, channel_id)

    # --- Event Faction Settings ---

    def get_event_factions(self) -> Optional[List[Dict[str, Any]]]:
        """获取当前活动的所有派系。"""
        return event_service.get_event_factions()

    def set_winning_faction(self, faction_id: Optional[str]):
        """设置当前活动的获胜派系。"""
        event_service.set_winning_faction(faction_id)

    def get_winning_faction(self) -> Optional[str]:
        """获取当前活动的获胜派系。"""
        return event_service.get_winning_faction()

    # --- AI Model Settings ---

    def get_available_ai_models(self) -> List[str]:
        """获取所有可用的AI模型。"""
        return config.AVAILABLE_AI_MODELS

    async def get_current_ai_model(self) -> str:
        """获取当前设置的全局AI模型。
        
        优先级：
        1. 数据库中保存的设置（Dashboard 更新的）
        2. .env 中的 GEMINI_MODEL 设置
        3. 可用模型列表的第一个
        """
        model = await self.db_manager.get_global_setting("ai_model")
        if model:
            return model
        # 回退到 .env 配置，而不是硬编码的可用模型列表
        return chat_config.GEMINI_MODEL or config.AVAILABLE_AI_MODELS[0]

    async def set_ai_model(self, model: str) -> None:
        """设置全局AI模型。"""
        await self.db_manager.set_global_setting("ai_model", model)

    # --- AI Model Usage ---

    async def increment_model_usage(self, model_name: str) -> None:
        """记录一次模型使用。"""
        if model_name:
            await self.db_manager.increment_model_usage(model_name)

    async def get_model_usage_counts(self) -> Dict[str, int]:
        """获取所有模型的使用计数。"""
        rows = await self.db_manager.get_model_usage_counts()
        return {row["model_name"]: row["usage_count"] for row in rows}


# 单例实例
chat_settings_service = ChatSettingsService()
