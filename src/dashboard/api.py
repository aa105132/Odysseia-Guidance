# -*- coding: utf-8 -*-
"""
Dashboard API - FastAPI 后端
提供配置管理、状态监控等功能

当与Bot在同一进程中运行时，可以直接访问Bot的服务实例。
"""

import os
import re
import json
import logging
import uuid
import aiohttp
from typing import Any, Dict, List, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from psycopg2.extras import DictCursor

from src.chat.config import chat_config
from src.chat.config import emoji_config
from src.chat.features.admin_panel.services.db_services import get_parade_db_connection
from src.dashboard.service_registry import service_registry

log = logging.getLogger(__name__)

# --- FastAPI 应用 ---
app = FastAPI(
    title="月月 Dashboard API",
    description="管理面板后端 API",
    version="1.0.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 安全认证
security = HTTPBearer(auto_error=False)
DASHBOARD_SECRET = os.getenv("DASHBOARD_SECRET", "your-secret-key-change-in-production")


# --- Pydantic 模型 ---
class ConfigUpdate(BaseModel):
    """配置更新请求"""
    section: str
    key: str
    value: Any


class ImagenConfigUpdate(BaseModel):
    """Imagen 配置更新"""
    enabled: Optional[bool] = None
    api_url: Optional[str] = None
    model: Optional[str] = None
    edit_model: Optional[str] = None  # 图生图模型（默认分辨率）
    default_images: Optional[int] = None
    api_key: Optional[str] = None
    # API 格式:
    # - 'gemini': Gemini 原生 generateImages 接口（官方 API）
    # - 'gemini_chat': Gemini 多模态聊天接口（支持图像生成的代理）
    # - 'openai': OpenAI 兼容的 chat/completions 接口
    api_format: Optional[str] = None
    # 月光币成本配置
    generation_cost: Optional[int] = None  # 文生图成本
    edit_cost: Optional[int] = None  # 图生图成本
    max_images: Optional[int] = None  # 单次最大图片数量
    # 分辨率模型配置
    model_2k: Optional[str] = None  # 2K 分辨率绘图模型
    model_4k: Optional[str] = None  # 4K 分辨率绘图模型
    edit_model_2k: Optional[str] = None  # 2K 分辨率图生图模型
    edit_model_4k: Optional[str] = None  # 4K 分辨率图生图模型
    # 流式请求配置
    streaming_enabled: Optional[bool] = None  # 是否启用流式请求
    # 图片响应格式: 'auto', 'base64', 'url'
    image_response_format: Optional[str] = None
    # 内容分级模型配置 (SFW/NSFW) - 完整的模型矩阵
    # SFW 模型
    sfw_model: Optional[str] = None  # SFW 默认文生图
    sfw_edit_model: Optional[str] = None  # SFW 默认图生图
    sfw_model_2k: Optional[str] = None  # SFW 2K文生图
    sfw_edit_model_2k: Optional[str] = None  # SFW 2K图生图
    sfw_model_4k: Optional[str] = None  # SFW 4K文生图
    sfw_edit_model_4k: Optional[str] = None  # SFW 4K图生图
    # NSFW 模型
    nsfw_model: Optional[str] = None  # NSFW 默认文生图
    nsfw_edit_model: Optional[str] = None  # NSFW 默认图生图
    nsfw_model_2k: Optional[str] = None  # NSFW 2K文生图
    nsfw_edit_model_2k: Optional[str] = None  # NSFW 2K图生图
    nsfw_model_4k: Optional[str] = None  # NSFW 4K文生图
    nsfw_edit_model_4k: Optional[str] = None  # NSFW 4K图生图


class VideoConfigUpdate(BaseModel):
    """视频生成配置更新"""
    enabled: Optional[bool] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    i2v_model: Optional[str] = None  # 图生视频专用模型
    api_format: Optional[str] = None
    video_format: Optional[str] = None  # 'url' 或 'html'
    generation_cost: Optional[int] = None
    max_duration: Optional[int] = None


class EmbeddingConfigUpdate(BaseModel):
    """向量嵌入配置更新"""
    enabled: Optional[bool] = None
    provider: Optional[str] = None  # 'gemini', 'openai', 'siliconflow'
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    dimensions: Optional[int] = None


class AIConfigUpdate(BaseModel):
    """AI 配置更新"""
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    api_format: Optional[str] = None  # 'gemini' 或 'openai'
    summary_model: Optional[str] = None  # 摘要模型
    query_model: Optional[str] = None  # 查询重写模型


class ModelListRequest(BaseModel):
    """获取模型列表请求"""
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    api_format: str = "gemini"  # 'gemini' 或 'openai'
    model_type: str = "chat"  # 'chat' 或 'imagen'


class ShopItemUpdate(BaseModel):
    """商店物品更新"""
    item_id: str
    name: Optional[str] = None
    price: Optional[int] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


class CoinConfigUpdate(BaseModel):
    """货币配置更新"""
    daily_reward: Optional[int] = None
    chat_reward: Optional[int] = None
    max_loan: Optional[int] = None


class ModerationConfigUpdate(BaseModel):
    """管理配置更新（警告与拉黑设置）"""
    warning_threshold: Optional[int] = None  # 警告次数阈值
    ban_duration_min: Optional[int] = None  # 拉黑时长最小值（分钟）
    ban_duration_max: Optional[int] = None  # 拉黑时长最大值（分钟）


class EmojiMapping(BaseModel):
    """单个表情映射"""
    placeholder: str  # 如 <微笑>
    discord_emojis: List[str]  # Discord 表情列表


class EmojiMappingUpdate(BaseModel):
    """表情映射更新"""
    mappings: List[EmojiMapping]


class KnowledgeDocumentCreate(BaseModel):
    """创建知识文档"""
    title: str
    content: str
    category: Optional[str] = None


class KnowledgeDocumentUpdate(BaseModel):
    """更新知识文档"""
    title: Optional[str] = None
    content: Optional[str] = None


# --- 认证依赖 ---
async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """验证 API Token"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌"
        )
    if credentials.credentials != DASHBOARD_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证令牌"
        )
    return credentials.credentials


# --- API 端点 ---

@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/config/all")
async def get_all_config(token: str = Depends(verify_token)):
    """获取所有配置"""
    # 获取当前 API URL 和 Key（部分隐藏）
    ai_api_url = os.getenv("GEMINI_API_BASE_URL", "")
    ai_api_key = os.getenv("GEMINI_API_KEYS", "")
    imagen_api_key = os.getenv("GEMINI_IMAGEN_API_KEY", "") or ai_api_key
    
    # 隐藏敏感信息
    ai_masked_key = ai_api_key[:10] + "..." + ai_api_key[-4:] if len(ai_api_key) > 14 else ("***" if ai_api_key else "")
    imagen_masked_key = imagen_api_key[:10] + "..." + imagen_api_key[-4:] if len(imagen_api_key) > 14 else ("***" if imagen_api_key else "")
    
    return {
        "ai": {
            "model": chat_config.PROMPT_CONFIG.get("model") or chat_config.GEMINI_MODEL,
            "temperature": chat_config.PROMPT_CONFIG.get("temperature", 1.0),
            "max_tokens": chat_config.PROMPT_CONFIG.get("max_output_tokens", 8192),
            "summary_model": chat_config.SUMMARY_MODEL,
            "query_model": chat_config.QUERY_REWRITING_MODEL,
            "persona_name": "月月",
            "api_url": ai_api_url,
            "api_key_masked": ai_masked_key,
            "has_api_key": bool(ai_api_key),
            "available_models": [
                "gemini-3-flash-custom",
                "gemini-3-pro-preview-custom",
                "gemini-2.5-flash-custom",
                "gemini-2.5-pro-custom",
                "gemini-2.5-flash-lite",
            ]
        },
        "imagen": {
            "enabled": chat_config.GEMINI_IMAGEN_CONFIG.get("ENABLED", False),
            "api_url": chat_config.GEMINI_IMAGEN_CONFIG.get("BASE_URL", "") or chat_config.GEMINI_IMAGEN_CONFIG.get("API_URL", ""),
            "model": chat_config.GEMINI_IMAGEN_CONFIG.get("MODEL_NAME", "agy-gemini-3-pro-image"),
            "edit_model": chat_config.GEMINI_IMAGEN_CONFIG.get("EDIT_MODEL_NAME", ""),
            "default_images": chat_config.GEMINI_IMAGEN_CONFIG.get("DEFAULT_NUMBER_OF_IMAGES", 1),
            "aspect_ratios": chat_config.GEMINI_IMAGEN_CONFIG.get("ASPECT_RATIOS", {}),
            "api_key_masked": imagen_masked_key,
            "has_api_key": bool(imagen_api_key),
            "api_format": chat_config.GEMINI_IMAGEN_CONFIG.get("API_FORMAT", "gemini"),
            # 分辨率模型配置
            "model_2k": chat_config.GEMINI_IMAGEN_CONFIG.get("MODEL_NAME_2K", "agy-gemini-3-pro-image-2k"),
            "model_4k": chat_config.GEMINI_IMAGEN_CONFIG.get("MODEL_NAME_4K", "agy-gemini-3-pro-image-4k"),
            "edit_model_2k": chat_config.GEMINI_IMAGEN_CONFIG.get("EDIT_MODEL_NAME_2K", "agy-gemini-3-pro-image-2k"),
            "edit_model_4k": chat_config.GEMINI_IMAGEN_CONFIG.get("EDIT_MODEL_NAME_4K", "agy-gemini-3-pro-image-4k"),
            "image_response_format": chat_config.GEMINI_IMAGEN_CONFIG.get("IMAGE_RESPONSE_FORMAT", "auto"),
        },
        "coin": {
            "daily_reward": chat_config.COIN_CONFIG.get("DAILY_CHECKIN_REWARD", 50),
            "chat_reward": chat_config.COIN_CONFIG.get("DAILY_CHAT_REWARD", 10),
            "max_loan": chat_config.COIN_CONFIG.get("MAX_LOAN_AMOUNT", 1000),
            "currency_name": "月光币",
        },
        "moderation": {
            "warning_threshold": chat_config.BLACKLIST_WARNING_THRESHOLD,
            "ban_duration_min": chat_config.BLACKLIST_BAN_DURATION_MINUTES[0],
            "ban_duration_max": chat_config.BLACKLIST_BAN_DURATION_MINUTES[1],
        },
        "shop": {
            "items": chat_config.SHOP_ITEMS if hasattr(chat_config, 'SHOP_ITEMS') else [],
        }
    }


@app.get("/api/config/ai")
async def get_ai_config(token: str = Depends(verify_token)):
    """获取 AI 配置 - 优先从数据库读取持久化设置"""
    from src.chat.utils.database import chat_db_manager
    
    # 从数据库读取持久化设置（Dashboard 保存的配置）
    db_model = await chat_db_manager.get_global_setting("ai_model")
    db_temperature = await chat_db_manager.get_global_setting("ai_temperature")
    db_max_tokens = await chat_db_manager.get_global_setting("ai_max_tokens")
    db_summary_model = await chat_db_manager.get_global_setting("summary_model")
    db_query_model = await chat_db_manager.get_global_setting("query_model")
    db_api_url = await chat_db_manager.get_global_setting("gemini_api_url")
    db_api_key = await chat_db_manager.get_global_setting("gemini_api_key")
    db_api_format = await chat_db_manager.get_global_setting("ai_api_format")
    
    # 优先使用数据库值，否则回退到环境变量/内存配置
    model = db_model or chat_config.PROMPT_CONFIG.get("model") or chat_config.GEMINI_MODEL
    temperature = float(db_temperature) if db_temperature else chat_config.PROMPT_CONFIG.get("temperature", 1.0)
    max_tokens = int(db_max_tokens) if db_max_tokens else chat_config.PROMPT_CONFIG.get("max_output_tokens", 8192)
    summary_model = db_summary_model or chat_config.SUMMARY_MODEL
    query_model = db_query_model or getattr(chat_config, 'QUERY_REWRITING_MODEL', 'gemini-2.5-flash-lite')
    
    # API URL 和 Key：优先数据库，其次环境变量
    api_url = db_api_url or os.getenv("GEMINI_API_BASE_URL", "")
    api_key = db_api_key or os.getenv("GEMINI_API_KEYS", "")
    api_format = db_api_format or "gemini"
    
    # 隐藏敏感信息
    masked_url = api_url[:30] + "..." if len(api_url) > 30 else api_url
    masked_key = api_key[:10] + "..." + api_key[-4:] if len(api_key) > 14 else "***"
    
    # 构建可用模型列表，确保当前选择的模型在列表中
    available_models = [
        "codex-gpt-5.2",
        "gcli-gemini-3-flash-preview-nothinking",
        "gcli-gemini-3-flash-preview",
        "gemini-2.5-flash-lite",
    ]
    # 如果当前模型不在列表中，添加到开头
    if model and model not in available_models:
        available_models.insert(0, model)
    
    return {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "summary_model": summary_model,
        "query_model": query_model,
        "persona_name": "月月",
        "api_url": api_url,
        "api_url_masked": masked_url,
        "api_key_masked": masked_key,
        "has_api_key": bool(api_key),
        "api_format": api_format,
        "available_models": available_models
    }


@app.put("/api/config/ai")
async def update_ai_config(config: AIConfigUpdate, token: str = Depends(verify_token)):
    """更新 AI 配置 - 所有配置都写入数据库以持久化"""
    from src.chat.utils.database import chat_db_manager
    
    updated = {}
    env_updates = {}
    api_keys_changed = False
    
    if config.model is not None:
        chat_config.PROMPT_CONFIG["model"] = config.model
        chat_config.GEMINI_MODEL = config.model  # 同步更新全局变量
        os.environ["GEMINI_MODEL"] = config.model
        env_updates["GEMINI_MODEL"] = config.model
        updated["model"] = config.model
        
        # 同步更新 GeminiService 的默认模型
        if service_registry.is_initialized and service_registry.gemini_service:
            service_registry.gemini_service.default_model_name = config.model
            log.info(f"✅ GeminiService 默认模型已更新为: {config.model}")
        
        # 写入数据库持久化
        await chat_db_manager.set_global_setting("ai_model", config.model)
        log.info(f"✅ AI 模型已写入数据库: {config.model}")
    
    if config.temperature is not None:
        if not 0.0 <= config.temperature <= 2.0:
            raise HTTPException(400, "温度必须在 0.0 到 2.0 之间")
        chat_config.PROMPT_CONFIG["temperature"] = config.temperature
        os.environ["GEMINI_TEMPERATURE"] = str(config.temperature)
        env_updates["GEMINI_TEMPERATURE"] = str(config.temperature)
        updated["temperature"] = config.temperature
        # 写入数据库
        await chat_db_manager.set_global_setting("ai_temperature", str(config.temperature))
    
    if config.max_tokens is not None:
        if not 1 <= config.max_tokens <= 65536:
            raise HTTPException(400, "最大令牌数必须在 1 到 65536 之间")
        chat_config.PROMPT_CONFIG["max_output_tokens"] = config.max_tokens
        os.environ["GEMINI_MAX_TOKENS"] = str(config.max_tokens)
        env_updates["GEMINI_MAX_TOKENS"] = str(config.max_tokens)
        updated["max_tokens"] = config.max_tokens
        # 写入数据库
        await chat_db_manager.set_global_setting("ai_max_tokens", str(config.max_tokens))
    
    if config.api_url is not None:
        os.environ["GEMINI_API_BASE_URL"] = config.api_url
        env_updates["GEMINI_API_BASE_URL"] = config.api_url
        updated["api_url"] = config.api_url[:30] + "..." if len(config.api_url) > 30 else config.api_url
        # 写入数据库
        await chat_db_manager.set_global_setting("gemini_api_url", config.api_url)
        # 同时设置内存变量供 GeminiService 使用
        chat_config._db_api_url = config.api_url
        log.info(f"✅ API URL 已保存到内存: {config.api_url[:30]}...")
    
    if config.api_key is not None:
        os.environ["GEMINI_API_KEYS"] = config.api_key
        env_updates["GEMINI_API_KEYS"] = config.api_key
        updated["api_key"] = "已更新"
        api_keys_changed = True
        # 写入数据库
        await chat_db_manager.set_global_setting("gemini_api_key", config.api_key)
        # 同时设置内存变量供 GeminiService 使用
        chat_config._db_api_key = config.api_key
    
    if config.summary_model is not None:
        chat_config.SUMMARY_MODEL = config.summary_model
        os.environ["GEMINI_SUMMARY_MODEL"] = config.summary_model
        env_updates["GEMINI_SUMMARY_MODEL"] = config.summary_model
        updated["summary_model"] = config.summary_model
        # 写入数据库
        await chat_db_manager.set_global_setting("summary_model", config.summary_model)
    
    if config.query_model is not None:
        chat_config.QUERY_REWRITING_MODEL = config.query_model
        os.environ["GEMINI_QUERY_MODEL"] = config.query_model
        env_updates["GEMINI_QUERY_MODEL"] = config.query_model
        updated["query_model"] = config.query_model
        # 写入数据库
        await chat_db_manager.set_global_setting("query_model", config.query_model)
    
    if config.api_format is not None:
        if config.api_format not in ["gemini", "openai"]:
            raise HTTPException(400, "API 格式必须是 'gemini' 或 'openai'")
        chat_config._db_api_format = config.api_format
        # 写入数据库
        await chat_db_manager.set_global_setting("ai_api_format", config.api_format)
        updated["api_format"] = config.api_format
        log.info(f"✅ API 格式已保存: {config.api_format}")
    
    # 如果有环境变量更新，尝试写入 .env 文件（作为备份）
    if env_updates:
        try:
            update_env_file(env_updates)
            log.info(f"环境变量已写入 .env 文件")
        except Exception as e:
            log.warning(f"无法写入 .env 文件: {e}")
    
    # 如果 API 密钥有变化，尝试热更新 GeminiService
    if api_keys_changed and service_registry.is_initialized:
        try:
            reload_result = service_registry.gemini_service.reload_api_keys(config.api_key)
            if reload_result.get("success"):
                updated["api_keys_reloaded"] = True
                log.info(f"✅ API 密钥已热更新到 GeminiService: {reload_result.get('message')}")
            else:
                log.warning(f"API 密钥热更新失败: {reload_result.get('error')}")
                updated["api_keys_reload_error"] = reload_result.get("error")
        except Exception as e:
            log.error(f"调用 GeminiService.reload_api_keys 失败: {e}")
            updated["api_keys_reload_error"] = str(e)
    elif api_keys_changed:
        log.warning("GeminiService 未初始化，无法热更新 API 密钥。密钥将在下次重启时生效。")
        updated["api_keys_pending_restart"] = True
    
    log.info(f"AI 配置已更新并持久化到数据库: {updated}")
    return {"success": True, "updated": updated}


def update_env_file(updates: Dict[str, str]):
    """更新 .env 文件中的环境变量"""
    # 优先使用工作目录，其次使用相对路径
    if os.path.exists("/app/.env"):
        env_path = "/app/.env"
    else:
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
    
    log.info(f"尝试更新 .env 文件: {env_path}")
    
    if not os.path.exists(env_path):
        log.warning(f".env 文件不存在: {env_path}，尝试创建")
        # 如果不存在，创建一个新文件
        try:
            with open(env_path, "w", encoding="utf-8") as f:
                for key, value in updates.items():
                    f.write(f'{key}="{value}"\n')
            log.info(f"已创建新的 .env 文件并写入配置")
            return
        except Exception as e:
            log.error(f"创建 .env 文件失败: {e}")
            return
    
    # 读取现有内容
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    # 更新或添加变量
    updated_keys = set()
    new_lines = []
    
    for line in lines:
        key_match = re.match(r'^([A-Z_][A-Z0-9_]*)=', line.strip())
        if key_match:
            key = key_match.group(1)
            if key in updates:
                new_lines.append(f'{key}="{updates[key]}"\n')
                updated_keys.add(key)
                continue
        new_lines.append(line)
    
    # 添加新的变量
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f'{key}="{value}"\n')
    
    # 写入文件
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


@app.get("/api/config/imagen")
async def get_imagen_config(token: str = Depends(verify_token)):
    """获取 Imagen 配置 - 优先从数据库读取持久化设置"""
    from src.chat.utils.database import chat_db_manager
    
    # 从数据库读取持久化设置
    db_enabled = await chat_db_manager.get_global_setting("imagen_enabled")
    db_api_url = await chat_db_manager.get_global_setting("imagen_api_url")
    db_api_key = await chat_db_manager.get_global_setting("imagen_api_key")
    db_model = await chat_db_manager.get_global_setting("imagen_model")
    db_edit_model = await chat_db_manager.get_global_setting("imagen_edit_model")
    db_api_format = await chat_db_manager.get_global_setting("imagen_api_format")
    db_generation_cost = await chat_db_manager.get_global_setting("imagen_generation_cost")
    db_edit_cost = await chat_db_manager.get_global_setting("imagen_edit_cost")
    db_max_images = await chat_db_manager.get_global_setting("imagen_max_images")
    db_streaming_enabled = await chat_db_manager.get_global_setting("imagen_streaming_enabled")
    db_image_response_format = await chat_db_manager.get_global_setting("imagen_image_response_format")
    # SFW/NSFW 模型配置 - 完整矩阵
    db_sfw_model = await chat_db_manager.get_global_setting("imagen_sfw_model")
    db_sfw_edit_model = await chat_db_manager.get_global_setting("imagen_sfw_edit_model")
    db_sfw_model_2k = await chat_db_manager.get_global_setting("imagen_sfw_model_2k")
    db_sfw_edit_model_2k = await chat_db_manager.get_global_setting("imagen_sfw_edit_model_2k")
    db_sfw_model_4k = await chat_db_manager.get_global_setting("imagen_sfw_model_4k")
    db_sfw_edit_model_4k = await chat_db_manager.get_global_setting("imagen_sfw_edit_model_4k")
    db_nsfw_model = await chat_db_manager.get_global_setting("imagen_nsfw_model")
    db_nsfw_edit_model = await chat_db_manager.get_global_setting("imagen_nsfw_edit_model")
    db_nsfw_model_2k = await chat_db_manager.get_global_setting("imagen_nsfw_model_2k")
    db_nsfw_edit_model_2k = await chat_db_manager.get_global_setting("imagen_nsfw_edit_model_2k")
    db_nsfw_model_4k = await chat_db_manager.get_global_setting("imagen_nsfw_model_4k")
    db_nsfw_edit_model_4k = await chat_db_manager.get_global_setting("imagen_nsfw_edit_model_4k")
    
    # 内存配置作为回退
    config = chat_config.GEMINI_IMAGEN_CONFIG
    
    # 优先使用数据库值
    enabled = db_enabled == "true" if db_enabled else config.get("ENABLED", False)
    api_url = db_api_url or config.get("BASE_URL", "") or config.get("API_URL", "")
    api_key = db_api_key or config.get("API_KEY", "")
    model = db_model or config.get("MODEL_NAME") or config.get("MODEL") or "imagen-3.0-generate-002"
    edit_model = db_edit_model or config.get("EDIT_MODEL_NAME") or ""
    api_format = db_api_format or config.get("API_FORMAT", "gemini")
    generation_cost = int(db_generation_cost) if db_generation_cost else config.get("IMAGE_GENERATION_COST", 1)
    edit_cost = int(db_edit_cost) if db_edit_cost else config.get("IMAGE_EDIT_COST", 1)
    max_images = int(db_max_images) if db_max_images else config.get("MAX_IMAGES_PER_REQUEST", 20)
    streaming_enabled = db_streaming_enabled == "true" if db_streaming_enabled else config.get("STREAMING_ENABLED", False)
    image_response_format = db_image_response_format or config.get("IMAGE_RESPONSE_FORMAT", "auto")
    # SFW/NSFW 模型 - 完整矩阵
    sfw_model = db_sfw_model or config.get("SFW_MODEL_NAME", "")
    sfw_edit_model = db_sfw_edit_model or config.get("SFW_EDIT_MODEL_NAME", "")
    sfw_model_2k = db_sfw_model_2k or config.get("SFW_MODEL_NAME_2K", "")
    sfw_edit_model_2k = db_sfw_edit_model_2k or config.get("SFW_EDIT_MODEL_NAME_2K", "")
    sfw_model_4k = db_sfw_model_4k or config.get("SFW_MODEL_NAME_4K", "")
    sfw_edit_model_4k = db_sfw_edit_model_4k or config.get("SFW_EDIT_MODEL_NAME_4K", "")
    nsfw_model = db_nsfw_model or config.get("NSFW_MODEL_NAME", "")
    nsfw_edit_model = db_nsfw_edit_model or config.get("NSFW_EDIT_MODEL_NAME", "")
    nsfw_model_2k = db_nsfw_model_2k or config.get("NSFW_MODEL_NAME_2K", "")
    nsfw_edit_model_2k = db_nsfw_edit_model_2k or config.get("NSFW_EDIT_MODEL_NAME_2K", "")
    nsfw_model_4k = db_nsfw_model_4k or config.get("NSFW_MODEL_NAME_4K", "")
    nsfw_edit_model_4k = db_nsfw_edit_model_4k or config.get("NSFW_EDIT_MODEL_NAME_4K", "")
    
    # 隐藏部分信息
    masked_url = ""
    if api_url and len(api_url) > 30:
        masked_url = api_url[:20] + "..." + api_url[-10:]
    elif api_url:
        masked_url = api_url
    
    masked_key = ""
    if api_key and len(api_key) > 14:
        masked_key = api_key[:10] + "..." + api_key[-4:]
    elif api_key:
        masked_key = "***"
    
    # 检查服务是否可用（安全导入，不阻塞）
    service_available = False
    if enabled:
        try:
            from src.chat.features.image_generation.services.gemini_imagen_service import gemini_imagen_service
            service_available = gemini_imagen_service.is_available()
        except ImportError:
            log.debug("Imagen 服务模块未安装或不可用")
        except Exception as e:
            log.warning(f"检查 Imagen 服务状态时出错: {e}")
    
    return {
        "enabled": enabled,
        "api_url": api_url,
        "api_url_masked": masked_url,
        "api_key_masked": masked_key,
        "has_api_key": bool(api_key),
        "model": model,
        "edit_model": edit_model,
        "default_images": config.get("DEFAULT_NUMBER_OF_IMAGES", 1),
        "aspect_ratios": config.get("ASPECT_RATIOS", {}),
        "api_format": api_format,
        "service_available": service_available,
        "generation_cost": generation_cost,
        "edit_cost": edit_cost,
        "max_images": max_images,
        "streaming_enabled": streaming_enabled,
        "image_response_format": image_response_format,
        # SFW/NSFW 模型配置 - 完整矩阵
        "sfw_model": sfw_model,
        "sfw_edit_model": sfw_edit_model,
        "sfw_model_2k": sfw_model_2k,
        "sfw_edit_model_2k": sfw_edit_model_2k,
        "sfw_model_4k": sfw_model_4k,
        "sfw_edit_model_4k": sfw_edit_model_4k,
        "nsfw_model": nsfw_model,
        "nsfw_edit_model": nsfw_edit_model,
        "nsfw_model_2k": nsfw_model_2k,
        "nsfw_edit_model_2k": nsfw_edit_model_2k,
        "nsfw_model_4k": nsfw_model_4k,
        "nsfw_edit_model_4k": nsfw_edit_model_4k,
        "available_models": [
            "imagen-3.0-generate-002",
            "imagen-3.0-fast-generate-001",
            "dall-e-3",
            "dall-e-2",
        ]
    }


@app.put("/api/config/imagen")
async def update_imagen_config(config: ImagenConfigUpdate, token: str = Depends(verify_token)):
    """更新 Imagen 配置并热重载服务 - 所有配置写入数据库持久化"""
    from src.chat.utils.database import chat_db_manager
    
    updated = {}
    env_updates = {}
    
    if config.enabled is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["ENABLED"] = config.enabled
        os.environ["GEMINI_IMAGEN_ENABLED"] = str(config.enabled).lower()
        env_updates["GEMINI_IMAGEN_ENABLED"] = str(config.enabled).lower()
        updated["enabled"] = config.enabled
        # 写入数据库
        await chat_db_manager.set_global_setting("imagen_enabled", str(config.enabled).lower())
    
    if config.api_url is not None:
        if config.api_url and not (config.api_url.startswith("http://") or config.api_url.startswith("https://")):
            raise HTTPException(400, "API URL 必须以 http:// 或 https:// 开头")
        chat_config.GEMINI_IMAGEN_CONFIG["BASE_URL"] = config.api_url
        os.environ["GEMINI_IMAGEN_BASE_URL"] = config.api_url
        env_updates["GEMINI_IMAGEN_BASE_URL"] = config.api_url
        updated["api_url"] = config.api_url[:30] + "..." if len(config.api_url) > 30 else config.api_url
        # 写入数据库
        await chat_db_manager.set_global_setting("imagen_api_url", config.api_url)
    
    if config.model is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["MODEL_NAME"] = config.model
        os.environ["GEMINI_IMAGEN_MODEL"] = config.model
        env_updates["GEMINI_IMAGEN_MODEL"] = config.model
        updated["model"] = config.model
        # 写入数据库
        await chat_db_manager.set_global_setting("imagen_model", config.model)
    
    if config.edit_model is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["EDIT_MODEL_NAME"] = config.edit_model if config.edit_model else None
        os.environ["GEMINI_IMAGEN_EDIT_MODEL"] = config.edit_model
        env_updates["GEMINI_IMAGEN_EDIT_MODEL"] = config.edit_model
        updated["edit_model"] = config.edit_model
        # 写入数据库
        await chat_db_manager.set_global_setting("imagen_edit_model", config.edit_model)
    
    if config.default_images is not None:
        if not 1 <= config.default_images <= 4:
            raise HTTPException(400, "默认图片数量必须在 1 到 4 之间")
        chat_config.GEMINI_IMAGEN_CONFIG["DEFAULT_NUMBER_OF_IMAGES"] = config.default_images
        updated["default_images"] = config.default_images
    
    if config.api_key is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["API_KEY"] = config.api_key
        os.environ["GEMINI_IMAGEN_API_KEY"] = config.api_key
        env_updates["GEMINI_IMAGEN_API_KEY"] = config.api_key
        updated["api_key"] = "已更新"
        # 写入数据库
        await chat_db_manager.set_global_setting("imagen_api_key", config.api_key)
    
    if config.api_format is not None:
        if config.api_format not in ["gemini", "gemini_chat", "openai"]:
            raise HTTPException(400, "API 格式必须是 'gemini', 'gemini_chat' 或 'openai'")
        chat_config.GEMINI_IMAGEN_CONFIG["API_FORMAT"] = config.api_format
        os.environ["GEMINI_IMAGEN_API_FORMAT"] = config.api_format
        env_updates["GEMINI_IMAGEN_API_FORMAT"] = config.api_format
        updated["api_format"] = config.api_format
        # 写入数据库
        await chat_db_manager.set_global_setting("imagen_api_format", config.api_format)
    
    # 月光币成本配置
    if config.generation_cost is not None:
        if config.generation_cost < 0:
            raise HTTPException(400, "文生图成本不能为负数")
        chat_config.GEMINI_IMAGEN_CONFIG["IMAGE_GENERATION_COST"] = config.generation_cost
        updated["generation_cost"] = config.generation_cost
        await chat_db_manager.set_global_setting("imagen_generation_cost", str(config.generation_cost))
    
    if config.edit_cost is not None:
        if config.edit_cost < 0:
            raise HTTPException(400, "图生图成本不能为负数")
        chat_config.GEMINI_IMAGEN_CONFIG["IMAGE_EDIT_COST"] = config.edit_cost
        updated["edit_cost"] = config.edit_cost
        await chat_db_manager.set_global_setting("imagen_edit_cost", str(config.edit_cost))
    
    if config.max_images is not None:
        if not 1 <= config.max_images <= 50:
            raise HTTPException(400, "单次最大图片数量必须在 1 到 50 之间")
        chat_config.GEMINI_IMAGEN_CONFIG["MAX_IMAGES_PER_REQUEST"] = config.max_images
        updated["max_images"] = config.max_images
        await chat_db_manager.set_global_setting("imagen_max_images", str(config.max_images))
    
    # 分辨率模型配置
    if config.model_2k is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["MODEL_NAME_2K"] = config.model_2k
        os.environ["GEMINI_IMAGEN_MODEL_2K"] = config.model_2k
        env_updates["GEMINI_IMAGEN_MODEL_2K"] = config.model_2k
        updated["model_2k"] = config.model_2k
        await chat_db_manager.set_global_setting("imagen_model_2k", config.model_2k)
    
    if config.model_4k is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["MODEL_NAME_4K"] = config.model_4k
        os.environ["GEMINI_IMAGEN_MODEL_4K"] = config.model_4k
        env_updates["GEMINI_IMAGEN_MODEL_4K"] = config.model_4k
        updated["model_4k"] = config.model_4k
        await chat_db_manager.set_global_setting("imagen_model_4k", config.model_4k)
    
    if config.edit_model_2k is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["EDIT_MODEL_NAME_2K"] = config.edit_model_2k
        os.environ["GEMINI_IMAGEN_EDIT_MODEL_2K"] = config.edit_model_2k
        env_updates["GEMINI_IMAGEN_EDIT_MODEL_2K"] = config.edit_model_2k
        updated["edit_model_2k"] = config.edit_model_2k
        await chat_db_manager.set_global_setting("imagen_edit_model_2k", config.edit_model_2k)
    
    if config.edit_model_4k is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["EDIT_MODEL_NAME_4K"] = config.edit_model_4k
        os.environ["GEMINI_IMAGEN_EDIT_MODEL_4K"] = config.edit_model_4k
        env_updates["GEMINI_IMAGEN_EDIT_MODEL_4K"] = config.edit_model_4k
        updated["edit_model_4k"] = config.edit_model_4k
        await chat_db_manager.set_global_setting("imagen_edit_model_4k", config.edit_model_4k)
    
    # 流式请求配置
    if config.streaming_enabled is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["STREAMING_ENABLED"] = config.streaming_enabled
        os.environ["GEMINI_IMAGEN_STREAMING"] = str(config.streaming_enabled).lower()
        env_updates["GEMINI_IMAGEN_STREAMING"] = str(config.streaming_enabled).lower()
        updated["streaming_enabled"] = config.streaming_enabled
        await chat_db_manager.set_global_setting("imagen_streaming_enabled", str(config.streaming_enabled).lower())
        log.info(f"✅ 流式请求已{'启用' if config.streaming_enabled else '禁用'}")
    
    # 图片响应格式配置
    if config.image_response_format is not None:
        if config.image_response_format not in ["auto", "base64", "url"]:
            raise HTTPException(400, "图片响应格式必须是 'auto', 'base64' 或 'url'")
        chat_config.GEMINI_IMAGEN_CONFIG["IMAGE_RESPONSE_FORMAT"] = config.image_response_format
        os.environ["GEMINI_IMAGEN_RESPONSE_FORMAT"] = config.image_response_format
        env_updates["GEMINI_IMAGEN_RESPONSE_FORMAT"] = config.image_response_format
        updated["image_response_format"] = config.image_response_format
        await chat_db_manager.set_global_setting("imagen_image_response_format", config.image_response_format)
        log.info(f"✅ 图片响应格式已设置为: {config.image_response_format}")
    
    # SFW/NSFW 模型配置 - 完整矩阵
    # SFW 模型
    if config.sfw_model is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["SFW_MODEL_NAME"] = config.sfw_model
        os.environ["GEMINI_IMAGEN_SFW_MODEL"] = config.sfw_model
        env_updates["GEMINI_IMAGEN_SFW_MODEL"] = config.sfw_model
        updated["sfw_model"] = config.sfw_model
        await chat_db_manager.set_global_setting("imagen_sfw_model", config.sfw_model)
        log.info(f"✅ SFW 文生图模型已设置为: {config.sfw_model or '(使用默认模型)'}")
    
    if config.sfw_edit_model is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["SFW_EDIT_MODEL_NAME"] = config.sfw_edit_model
        os.environ["GEMINI_IMAGEN_SFW_EDIT_MODEL"] = config.sfw_edit_model
        env_updates["GEMINI_IMAGEN_SFW_EDIT_MODEL"] = config.sfw_edit_model
        updated["sfw_edit_model"] = config.sfw_edit_model
        await chat_db_manager.set_global_setting("imagen_sfw_edit_model", config.sfw_edit_model)
        log.info(f"✅ SFW 图生图模型已设置为: {config.sfw_edit_model or '(使用默认模型)'}")
    
    if config.sfw_model_2k is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["SFW_MODEL_NAME_2K"] = config.sfw_model_2k
        os.environ["GEMINI_IMAGEN_SFW_MODEL_2K"] = config.sfw_model_2k
        env_updates["GEMINI_IMAGEN_SFW_MODEL_2K"] = config.sfw_model_2k
        updated["sfw_model_2k"] = config.sfw_model_2k
        await chat_db_manager.set_global_setting("imagen_sfw_model_2k", config.sfw_model_2k)
        log.info(f"✅ SFW 2K文生图模型已设置为: {config.sfw_model_2k or '(使用默认模型)'}")
    
    if config.sfw_edit_model_2k is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["SFW_EDIT_MODEL_NAME_2K"] = config.sfw_edit_model_2k
        os.environ["GEMINI_IMAGEN_SFW_EDIT_MODEL_2K"] = config.sfw_edit_model_2k
        env_updates["GEMINI_IMAGEN_SFW_EDIT_MODEL_2K"] = config.sfw_edit_model_2k
        updated["sfw_edit_model_2k"] = config.sfw_edit_model_2k
        await chat_db_manager.set_global_setting("imagen_sfw_edit_model_2k", config.sfw_edit_model_2k)
        log.info(f"✅ SFW 2K图生图模型已设置为: {config.sfw_edit_model_2k or '(使用默认模型)'}")
    
    if config.sfw_model_4k is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["SFW_MODEL_NAME_4K"] = config.sfw_model_4k
        os.environ["GEMINI_IMAGEN_SFW_MODEL_4K"] = config.sfw_model_4k
        env_updates["GEMINI_IMAGEN_SFW_MODEL_4K"] = config.sfw_model_4k
        updated["sfw_model_4k"] = config.sfw_model_4k
        await chat_db_manager.set_global_setting("imagen_sfw_model_4k", config.sfw_model_4k)
        log.info(f"✅ SFW 4K文生图模型已设置为: {config.sfw_model_4k or '(使用默认模型)'}")
    
    if config.sfw_edit_model_4k is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["SFW_EDIT_MODEL_NAME_4K"] = config.sfw_edit_model_4k
        os.environ["GEMINI_IMAGEN_SFW_EDIT_MODEL_4K"] = config.sfw_edit_model_4k
        env_updates["GEMINI_IMAGEN_SFW_EDIT_MODEL_4K"] = config.sfw_edit_model_4k
        updated["sfw_edit_model_4k"] = config.sfw_edit_model_4k
        await chat_db_manager.set_global_setting("imagen_sfw_edit_model_4k", config.sfw_edit_model_4k)
        log.info(f"✅ SFW 4K图生图模型已设置为: {config.sfw_edit_model_4k or '(使用默认模型)'}")
    
    # NSFW 模型
    if config.nsfw_model is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["NSFW_MODEL_NAME"] = config.nsfw_model
        os.environ["GEMINI_IMAGEN_NSFW_MODEL"] = config.nsfw_model
        env_updates["GEMINI_IMAGEN_NSFW_MODEL"] = config.nsfw_model
        updated["nsfw_model"] = config.nsfw_model
        await chat_db_manager.set_global_setting("imagen_nsfw_model", config.nsfw_model)
        log.info(f"✅ NSFW 文生图模型已设置为: {config.nsfw_model or '(使用默认模型)'}")
    
    if config.nsfw_edit_model is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["NSFW_EDIT_MODEL_NAME"] = config.nsfw_edit_model
        os.environ["GEMINI_IMAGEN_NSFW_EDIT_MODEL"] = config.nsfw_edit_model
        env_updates["GEMINI_IMAGEN_NSFW_EDIT_MODEL"] = config.nsfw_edit_model
        updated["nsfw_edit_model"] = config.nsfw_edit_model
        await chat_db_manager.set_global_setting("imagen_nsfw_edit_model", config.nsfw_edit_model)
        log.info(f"✅ NSFW 图生图模型已设置为: {config.nsfw_edit_model or '(使用默认模型)'}")
    
    if config.nsfw_model_2k is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["NSFW_MODEL_NAME_2K"] = config.nsfw_model_2k
        os.environ["GEMINI_IMAGEN_NSFW_MODEL_2K"] = config.nsfw_model_2k
        env_updates["GEMINI_IMAGEN_NSFW_MODEL_2K"] = config.nsfw_model_2k
        updated["nsfw_model_2k"] = config.nsfw_model_2k
        await chat_db_manager.set_global_setting("imagen_nsfw_model_2k", config.nsfw_model_2k)
        log.info(f"✅ NSFW 2K文生图模型已设置为: {config.nsfw_model_2k or '(使用默认模型)'}")
    
    if config.nsfw_edit_model_2k is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["NSFW_EDIT_MODEL_NAME_2K"] = config.nsfw_edit_model_2k
        os.environ["GEMINI_IMAGEN_NSFW_EDIT_MODEL_2K"] = config.nsfw_edit_model_2k
        env_updates["GEMINI_IMAGEN_NSFW_EDIT_MODEL_2K"] = config.nsfw_edit_model_2k
        updated["nsfw_edit_model_2k"] = config.nsfw_edit_model_2k
        await chat_db_manager.set_global_setting("imagen_nsfw_edit_model_2k", config.nsfw_edit_model_2k)
        log.info(f"✅ NSFW 2K图生图模型已设置为: {config.nsfw_edit_model_2k or '(使用默认模型)'}")
    
    if config.nsfw_model_4k is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["NSFW_MODEL_NAME_4K"] = config.nsfw_model_4k
        os.environ["GEMINI_IMAGEN_NSFW_MODEL_4K"] = config.nsfw_model_4k
        env_updates["GEMINI_IMAGEN_NSFW_MODEL_4K"] = config.nsfw_model_4k
        updated["nsfw_model_4k"] = config.nsfw_model_4k
        await chat_db_manager.set_global_setting("imagen_nsfw_model_4k", config.nsfw_model_4k)
        log.info(f"✅ NSFW 4K文生图模型已设置为: {config.nsfw_model_4k or '(使用默认模型)'}")
    
    if config.nsfw_edit_model_4k is not None:
        chat_config.GEMINI_IMAGEN_CONFIG["NSFW_EDIT_MODEL_NAME_4K"] = config.nsfw_edit_model_4k
        os.environ["GEMINI_IMAGEN_NSFW_EDIT_MODEL_4K"] = config.nsfw_edit_model_4k
        env_updates["GEMINI_IMAGEN_NSFW_EDIT_MODEL_4K"] = config.nsfw_edit_model_4k
        updated["nsfw_edit_model_4k"] = config.nsfw_edit_model_4k
        await chat_db_manager.set_global_setting("imagen_nsfw_edit_model_4k", config.nsfw_edit_model_4k)
        log.info(f"✅ NSFW 4K图生图模型已设置为: {config.nsfw_edit_model_4k or '(使用默认模型)'}")
    
    # 如果有环境变量更新，尝试写入 .env 文件
    if env_updates:
        try:
            update_env_file(env_updates)
            log.info(f"Imagen 环境变量已写入 .env 文件")
        except Exception as e:
            log.warning(f"无法写入 .env 文件: {e}")
    
    # 热重载 Imagen 服务（仅当启用时）
    if config.enabled is True:
        try:
            from src.chat.features.image_generation.services.gemini_imagen_service import gemini_imagen_service
            reload_result = gemini_imagen_service.update_config(
                enabled=config.enabled,
                api_key=config.api_key,
                base_url=config.api_url,
                model_name=config.model
            )
            updated["service_reloaded"] = reload_result.get("success", False)
            updated["service_available"] = reload_result.get("available", False)
            if not reload_result.get("success"):
                updated["reload_error"] = reload_result.get("error", reload_result.get("message"))
            log.info(f"Imagen 服务热重载结果: {reload_result}")
        except ImportError as e:
            log.warning(f"无法导入 Imagen 服务模块: {e}")
            updated["service_reload_error"] = "Imagen 服务模块不可用"
        except Exception as e:
            log.error(f"热重载 Imagen 服务失败: {e}", exc_info=True)
            updated["service_reload_error"] = str(e)
    else:
        # 禁用时直接更新配置，不需要热重载
        log.info("Imagen 服务已禁用，跳过热重载")
    
    log.info(f"Imagen 配置已更新: {updated}")
    return {"success": True, "updated": updated}


# --- 视频生成配置 API ---

@app.get("/api/config/video")
async def get_video_config(token: str = Depends(verify_token)):
    """获取视频生成配置"""
    from src.chat.utils.database import chat_db_manager
    
    config = chat_config.VIDEO_GEN_CONFIG
    
    # 从数据库读取持久化设置
    db_enabled = await chat_db_manager.get_global_setting("video_enabled")
    db_api_url = await chat_db_manager.get_global_setting("video_api_url")
    db_api_key = await chat_db_manager.get_global_setting("video_api_key")
    db_model = await chat_db_manager.get_global_setting("video_model")
    db_i2v_model = await chat_db_manager.get_global_setting("video_i2v_model")
    db_video_format = await chat_db_manager.get_global_setting("video_format")
    db_generation_cost = await chat_db_manager.get_global_setting("video_generation_cost")
    db_max_duration = await chat_db_manager.get_global_setting("video_max_duration")
    
    # 优先使用数据库值
    enabled = db_enabled == "true" if db_enabled else config.get("ENABLED", False)
    api_url = db_api_url or config.get("BASE_URL", "")
    api_key = db_api_key or config.get("API_KEY", "")
    model = db_model or config.get("MODEL_NAME", "veo-2.0-generate-001")
    i2v_model = db_i2v_model or config.get("I2V_MODEL_NAME", "")
    video_format = db_video_format or config.get("VIDEO_FORMAT", "url")
    generation_cost = int(db_generation_cost) if db_generation_cost else config.get("VIDEO_GENERATION_COST", 10)
    max_duration = int(db_max_duration) if db_max_duration else config.get("MAX_DURATION", 8)
    
    # 隐藏 API Key
    masked_key = ""
    if api_key and len(api_key) > 14:
        masked_key = api_key[:10] + "..." + api_key[-4:]
    elif api_key:
        masked_key = "***"
    
    # 检查服务状态
    service_available = False
    if enabled:
        try:
            from src.chat.features.video_generation.services.video_service import video_service as vs
            service_available = vs.is_available()
        except Exception:
            pass
    
    return {
        "enabled": enabled,
        "api_url": api_url,
        "api_key_masked": masked_key,
        "has_api_key": bool(api_key),
        "model": model,
        "i2v_model": i2v_model,
        "api_format": config.get("API_FORMAT", "openai"),
        "video_format": video_format,
        "generation_cost": generation_cost,
        "max_duration": max_duration,
        "service_available": service_available,
    }


@app.put("/api/config/video")
async def update_video_config(config: VideoConfigUpdate, token: str = Depends(verify_token)):
    """更新视频生成配置"""
    from src.chat.utils.database import chat_db_manager
    
    updated = {}
    env_updates = {}
    
    if config.enabled is not None:
        chat_config.VIDEO_GEN_CONFIG["ENABLED"] = config.enabled
        os.environ["VIDEO_GEN_ENABLED"] = str(config.enabled).lower()
        env_updates["VIDEO_GEN_ENABLED"] = str(config.enabled).lower()
        updated["enabled"] = config.enabled
        await chat_db_manager.set_global_setting("video_enabled", str(config.enabled).lower())
    
    if config.api_url is not None:
        if config.api_url and not (config.api_url.startswith("http://") or config.api_url.startswith("https://")):
            raise HTTPException(400, "API URL 必须以 http:// 或 https:// 开头")
        chat_config.VIDEO_GEN_CONFIG["BASE_URL"] = config.api_url
        os.environ["VIDEO_GEN_BASE_URL"] = config.api_url
        env_updates["VIDEO_GEN_BASE_URL"] = config.api_url
        updated["api_url"] = config.api_url[:30] + "..." if len(config.api_url) > 30 else config.api_url
        await chat_db_manager.set_global_setting("video_api_url", config.api_url)
    
    if config.api_key is not None:
        chat_config.VIDEO_GEN_CONFIG["API_KEY"] = config.api_key
        os.environ["VIDEO_GEN_API_KEY"] = config.api_key
        env_updates["VIDEO_GEN_API_KEY"] = config.api_key
        updated["api_key"] = "已更新"
        await chat_db_manager.set_global_setting("video_api_key", config.api_key)
    
    if config.model is not None:
        chat_config.VIDEO_GEN_CONFIG["MODEL_NAME"] = config.model
        os.environ["VIDEO_GEN_MODEL"] = config.model
        env_updates["VIDEO_GEN_MODEL"] = config.model
        updated["model"] = config.model
        await chat_db_manager.set_global_setting("video_model", config.model)
    
    if config.i2v_model is not None:
        chat_config.VIDEO_GEN_CONFIG["I2V_MODEL_NAME"] = config.i2v_model
        os.environ["VIDEO_GEN_I2V_MODEL"] = config.i2v_model
        env_updates["VIDEO_GEN_I2V_MODEL"] = config.i2v_model
        updated["i2v_model"] = config.i2v_model
        await chat_db_manager.set_global_setting("video_i2v_model", config.i2v_model)
    
    if config.video_format is not None:
        if config.video_format not in ["url", "html"]:
            raise HTTPException(400, "视频格式必须是 'url' 或 'html'")
        chat_config.VIDEO_GEN_CONFIG["VIDEO_FORMAT"] = config.video_format
        os.environ["VIDEO_GEN_FORMAT"] = config.video_format
        env_updates["VIDEO_GEN_FORMAT"] = config.video_format
        updated["video_format"] = config.video_format
        await chat_db_manager.set_global_setting("video_format", config.video_format)
    
    if config.generation_cost is not None:
        if config.generation_cost < 0:
            raise HTTPException(400, "视频生成成本不能为负数")
        chat_config.VIDEO_GEN_CONFIG["VIDEO_GENERATION_COST"] = config.generation_cost
        updated["generation_cost"] = config.generation_cost
        await chat_db_manager.set_global_setting("video_generation_cost", str(config.generation_cost))
    
    if config.max_duration is not None:
        if not 1 <= config.max_duration <= 60:
            raise HTTPException(400, "最大视频时长必须在 1 到 60 秒之间")
        chat_config.VIDEO_GEN_CONFIG["MAX_DURATION"] = config.max_duration
        updated["max_duration"] = config.max_duration
        await chat_db_manager.set_global_setting("video_max_duration", str(config.max_duration))
    
    # 写入 .env 文件
    if env_updates:
        try:
            update_env_file(env_updates)
        except Exception as e:
            log.warning(f"无法写入 .env 文件: {e}")
    
    # 热重载视频服务
    if config.enabled is True:
        try:
            from src.chat.features.video_generation.services.video_service import video_service as vs
            vs.reinitialize()
            updated["service_available"] = vs.is_available()
        except Exception as e:
            log.warning(f"热重载视频服务失败: {e}")
            updated["service_reload_error"] = str(e)
    
    log.info(f"视频生成配置已更新: {updated}")
    return {"success": True, "updated": updated}


# --- 向量嵌入配置 API ---

@app.get("/api/config/embedding")
async def get_embedding_config(token: str = Depends(verify_token)):
    """获取向量嵌入配置"""
    config = chat_config.EMBEDDING_CONFIG
    api_url = config.get("BASE_URL", "")
    api_key = config.get("API_KEY", "")
    
    # 隐藏部分信息
    masked_url = ""
    if api_url and len(api_url) > 30:
        masked_url = api_url[:20] + "..." + api_url[-10:]
    elif api_url:
        masked_url = api_url
    
    masked_key = ""
    if api_key and len(api_key) > 14:
        masked_key = api_key[:10] + "..." + api_key[-4:]
    elif api_key:
        masked_key = "***"
    
    return {
        "enabled": config.get("ENABLED", True),
        "provider": config.get("PROVIDER", "gemini"),
        "api_url": api_url,
        "api_url_masked": masked_url,
        "api_key_masked": masked_key,
        "has_api_key": bool(api_key),
        "model": config.get("MODEL_NAME", "gemini-embedding-001"),
        "dimensions": config.get("DIMENSIONS", 768),
        "available_providers": [
            {"id": "gemini", "name": "Google Gemini (官方)", "default_model": "gemini-embedding-001"},
            {"id": "openai", "name": "OpenAI 兼容", "default_model": "text-embedding-3-small"},
            {"id": "siliconflow", "name": "硅基流动", "default_model": "BAAI/bge-large-zh-v1.5"},
        ],
        "available_models": {
            "gemini": ["gemini-embedding-001", "embedding-001"],
            "openai": ["text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002"],
            "siliconflow": ["BAAI/bge-large-zh-v1.5", "BAAI/bge-m3", "Pro/BAAI/bge-m3"],
        }
    }


@app.put("/api/config/embedding")
async def update_embedding_config(config: EmbeddingConfigUpdate, token: str = Depends(verify_token)):
    """更新向量嵌入配置"""
    updated = {}
    env_updates = {}
    
    if config.enabled is not None:
        chat_config.EMBEDDING_CONFIG["ENABLED"] = config.enabled
        os.environ["EMBEDDING_ENABLED"] = str(config.enabled).lower()
        env_updates["EMBEDDING_ENABLED"] = str(config.enabled).lower()
        updated["enabled"] = config.enabled
    
    if config.provider is not None:
        if config.provider not in ["gemini", "openai", "siliconflow"]:
            raise HTTPException(400, "提供商必须是 'gemini', 'openai' 或 'siliconflow'")
        chat_config.EMBEDDING_CONFIG["PROVIDER"] = config.provider
        os.environ["EMBEDDING_PROVIDER"] = config.provider
        env_updates["EMBEDDING_PROVIDER"] = config.provider
        updated["provider"] = config.provider
    
    if config.api_url is not None:
        if config.api_url and not (config.api_url.startswith("http://") or config.api_url.startswith("https://")):
            raise HTTPException(400, "API URL 必须以 http:// 或 https:// 开头")
        chat_config.EMBEDDING_CONFIG["BASE_URL"] = config.api_url
        os.environ["EMBEDDING_BASE_URL"] = config.api_url
        env_updates["EMBEDDING_BASE_URL"] = config.api_url
        updated["api_url"] = config.api_url[:30] + "..." if len(config.api_url) > 30 else config.api_url
    
    if config.api_key is not None:
        chat_config.EMBEDDING_CONFIG["API_KEY"] = config.api_key
        os.environ["EMBEDDING_API_KEY"] = config.api_key
        env_updates["EMBEDDING_API_KEY"] = config.api_key
        updated["api_key"] = "已更新"
    
    if config.model is not None:
        chat_config.EMBEDDING_CONFIG["MODEL_NAME"] = config.model
        os.environ["EMBEDDING_MODEL"] = config.model
        env_updates["EMBEDDING_MODEL"] = config.model
        updated["model"] = config.model
    
    if config.dimensions is not None:
        if config.dimensions < 1 or config.dimensions > 4096:
            raise HTTPException(400, "向量维度必须在 1 到 4096 之间")
        chat_config.EMBEDDING_CONFIG["DIMENSIONS"] = config.dimensions
        os.environ["EMBEDDING_DIMENSIONS"] = str(config.dimensions)
        env_updates["EMBEDDING_DIMENSIONS"] = str(config.dimensions)
        updated["dimensions"] = config.dimensions
    
    # 如果有环境变量更新，尝试写入 .env 文件
    if env_updates:
        try:
            update_env_file(env_updates)
            log.info(f"向量嵌入环境变量已写入 .env 文件")
        except Exception as e:
            log.warning(f"无法写入 .env 文件: {e}")
    
    log.info(f"向量嵌入配置已更新: {updated}")
    return {"success": True, "updated": updated}


@app.get("/api/config/coin")
async def get_coin_config(token: str = Depends(verify_token)):
    """获取货币配置"""
    config = chat_config.COIN_CONFIG
    return {
        "daily_reward": config.get("DAILY_CHECKIN_REWARD", 50),
        "chat_reward": config.get("DAILY_CHAT_REWARD", 10),
        "max_loan": config.get("MAX_LOAN_AMOUNT", 1000),
        "currency_name": "月光币",
        "tax_rate": config.get("TRANSFER_TAX_RATE", 0.05),
    }


@app.put("/api/config/coin")
async def update_coin_config(config: CoinConfigUpdate, token: str = Depends(verify_token)):
    """更新货币配置"""
    updated = {}
    
    if config.daily_reward is not None:
        if config.daily_reward < 0:
            raise HTTPException(400, "每日奖励不能为负数")
        chat_config.COIN_CONFIG["DAILY_CHECKIN_REWARD"] = config.daily_reward
        updated["daily_reward"] = config.daily_reward
    
    if config.chat_reward is not None:
        if config.chat_reward < 0:
            raise HTTPException(400, "聊天奖励不能为负数")
        chat_config.COIN_CONFIG["DAILY_CHAT_REWARD"] = config.chat_reward
        updated["chat_reward"] = config.chat_reward
    
    if config.max_loan is not None:
        if config.max_loan < 0:
            raise HTTPException(400, "最大贷款额不能为负数")
        chat_config.COIN_CONFIG["MAX_LOAN_AMOUNT"] = config.max_loan
        updated["max_loan"] = config.max_loan
    
    log.info(f"货币配置已更新: {updated}")
    return {"success": True, "updated": updated}


@app.post("/api/models/list")
async def list_available_models(request: ModelListRequest, token: str = Depends(verify_token)):
    """从 API 获取可用模型列表"""
    try:
        # 使用请求中的 API Key 或环境变量中的
        api_key = request.api_key or os.getenv("GEMINI_API_KEYS", "")
        if not api_key:
            raise HTTPException(400, "未配置 API Key")
        
        models = []
        
        if request.api_format == "openai":
            # OpenAI 兼容格式
            api_url = request.api_url or "https://api.openai.com/v1"
            models_url = f"{api_url.rstrip('/')}/models"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    models_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        # OpenAI 格式返回 {data: [{id: "model-name", ...}, ...]}
                        if "data" in data:
                            models = [m.get("id") for m in data["data"] if m.get("id")]
                    else:
                        error_text = await response.text()
                        log.warning(f"获取 OpenAI 模型列表失败: {response.status} - {error_text}")
                        raise HTTPException(response.status, f"API 请求失败: {error_text[:100]}")
        
        else:
            # Gemini 官方格式
            api_url = request.api_url or "https://generativelanguage.googleapis.com/v1beta"
            models_url = f"{api_url.rstrip('/')}/models?key={api_key}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    models_url,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Gemini 格式返回 {models: [{name: "models/gemini-pro", ...}, ...]}
                        if "models" in data:
                            for m in data["models"]:
                                name = m.get("name", "")
                                # 移除 "models/" 前缀
                                if name.startswith("models/"):
                                    name = name[7:]
                                if name:
                                    models.append(name)
                    else:
                        error_text = await response.text()
                        log.warning(f"获取 Gemini 模型列表失败: {response.status} - {error_text}")
                        raise HTTPException(response.status, f"API 请求失败: {error_text[:100]}")
        
        # 排序模型列表
        models.sort()
        
        log.info(f"成功获取 {len(models)} 个可用模型")
        return {"models": models, "count": len(models)}
    
    except aiohttp.ClientError as e:
        log.error(f"获取模型列表网络错误: {e}")
        raise HTTPException(500, f"网络请求失败: {str(e)}")
    except Exception as e:
        log.error(f"获取模型列表失败: {e}", exc_info=True)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(500, f"获取模型列表失败: {str(e)}")


@app.get("/api/status")
async def get_status(token: str = Depends(verify_token)):
    """获取系统状态"""
    # 从 ServiceRegistry 获取实际的 Bot 状态
    bot_info = service_registry.get_bot_status()
    
    return {
        "bot_status": bot_info.get("status", "unknown"),
        "bot_user": bot_info.get("user"),
        "bot_user_id": bot_info.get("user_id"),
        "guilds_count": bot_info.get("guilds", 0),
        "latency_ms": bot_info.get("latency_ms"),
        "gemini_service_available": service_registry.is_initialized,
        "config_loaded": True,
        "timestamp": datetime.utcnow().isoformat(),
        "integrated_mode": True,  # 标识Dashboard与Bot运行在同一进程
    }


@app.post("/api/config/reload-api-keys")
async def reload_api_keys(token: str = Depends(verify_token)):
    """
    热重载 API 密钥（从环境变量重新加载）
    这个端点可以在不重启Bot的情况下更新API密钥
    """
    if not service_registry.is_initialized:
        raise HTTPException(
            status_code=503,
            detail="GeminiService 尚未初始化，请稍后再试"
        )
    
    try:
        result = service_registry.gemini_service.reload_api_keys()
        if result.get("success"):
            log.info(f"✅ API 密钥热重载成功: {result.get('message')}")
            return {
                "success": True,
                "message": result.get("message"),
                "key_count": result.get("count", 0)
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "热重载失败")
            )
    except Exception as e:
        log.error(f"API 密钥热重载失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"热重载失败: {str(e)}"
        )


@app.get("/api/config/moderation")
async def get_moderation_config(token: str = Depends(verify_token)):
    """获取管理配置（警告与拉黑设置）- 优先从数据库读取"""
    from src.chat.utils.database import chat_db_manager
    
    # 从数据库读取持久化设置
    db_warning_threshold = await chat_db_manager.get_global_setting("warning_threshold")
    db_ban_duration_min = await chat_db_manager.get_global_setting("ban_duration_min")
    db_ban_duration_max = await chat_db_manager.get_global_setting("ban_duration_max")
    
    # 优先使用数据库值，否则回退到内存配置
    warning_threshold = int(db_warning_threshold) if db_warning_threshold else chat_config.BLACKLIST_WARNING_THRESHOLD
    ban_duration_min = int(db_ban_duration_min) if db_ban_duration_min else chat_config.BLACKLIST_BAN_DURATION_MINUTES[0]
    ban_duration_max = int(db_ban_duration_max) if db_ban_duration_max else chat_config.BLACKLIST_BAN_DURATION_MINUTES[1]
    
    return {
        "warning_threshold": warning_threshold,
        "ban_duration_min": ban_duration_min,
        "ban_duration_max": ban_duration_max,
    }


@app.put("/api/config/moderation")
async def update_moderation_config(config: ModerationConfigUpdate, token: str = Depends(verify_token)):
    """更新管理配置（警告与拉黑设置）- 持久化到数据库"""
    from src.chat.utils.database import chat_db_manager
    
    updated = {}
    
    if config.warning_threshold is not None:
        if not 1 <= config.warning_threshold <= 100:
            raise HTTPException(400, "警告阈值必须在 1 到 100 之间")
        chat_config.BLACKLIST_WARNING_THRESHOLD = config.warning_threshold
        await chat_db_manager.set_global_setting("warning_threshold", str(config.warning_threshold))
        updated["warning_threshold"] = config.warning_threshold
        log.info(f"✅ 警告阈值已更新为: {config.warning_threshold}")
    
    if config.ban_duration_min is not None or config.ban_duration_max is not None:
        current_min = chat_config.BLACKLIST_BAN_DURATION_MINUTES[0]
        current_max = chat_config.BLACKLIST_BAN_DURATION_MINUTES[1]
        
        new_min = config.ban_duration_min if config.ban_duration_min is not None else current_min
        new_max = config.ban_duration_max if config.ban_duration_max is not None else current_max
        
        if not 1 <= new_min <= 1440:
            raise HTTPException(400, "拉黑时长最小值必须在 1 到 1440 分钟之间")
        if not 1 <= new_max <= 1440:
            raise HTTPException(400, "拉黑时长最大值必须在 1 到 1440 分钟之间")
        if new_min > new_max:
            raise HTTPException(400, "拉黑时长最小值不能大于最大值")
        
        chat_config.BLACKLIST_BAN_DURATION_MINUTES = (new_min, new_max)
        
        if config.ban_duration_min is not None:
            await chat_db_manager.set_global_setting("ban_duration_min", str(new_min))
            updated["ban_duration_min"] = new_min
        
        if config.ban_duration_max is not None:
            await chat_db_manager.set_global_setting("ban_duration_max", str(new_max))
            updated["ban_duration_max"] = new_max
        
        log.info(f"✅ 拉黑时长已更新为: ({new_min}, {new_max}) 分钟")
    
    log.info(f"管理配置已更新: {updated}")
    return {"success": True, "updated": updated}


@app.get("/api/config/emoji")
async def get_emoji_config(token: str = Depends(verify_token)):
    """获取表情配置"""
    # 解析当前的表情映射
    mappings = []
    for pattern, emojis in emoji_config.EMOJI_MAPPINGS:
        # 从正则表达式中提取占位符
        placeholder = pattern.pattern.replace("\\<", "<").replace("\\>", ">")
        mappings.append({
            "placeholder": placeholder,
            "discord_emojis": emojis,
            "preview": emojis[0] if emojis else ""
        })
    
    # 获取活动表情
    faction_mappings = {}
    for event_id, factions in emoji_config.FACTION_EMOJI_MAPPINGS.items():
        faction_mappings[event_id] = {}
        for faction_id, faction_emojis in factions.items():
            faction_list = []
            for pattern, emojis in faction_emojis:
                placeholder = pattern.pattern.replace("\\<", "<").replace("\\>", ">")
                faction_list.append({
                    "placeholder": placeholder,
                    "discord_emojis": emojis,
                    "preview": emojis[0] if emojis else ""
                })
            faction_mappings[event_id][faction_id] = faction_list
    
    return {
        "default_mappings": mappings,
        "faction_mappings": faction_mappings,
        "available_placeholders": [
            # 基础表情
            "<得意>", "<比心>", "<达咩>", "<护食>", "<流汗>", "<媚眼>", "<抓狂>", "<自闭>",
            "<乖巧>", "<傲娇>", "<没眼看>", "<暴怒>", "<吃瓜>", "<鄙视>", "<求夸>", "<大哭>",
            "<慌张>", "<哈欠>", "<被窝>", "<祈祷>", "<干饭>", "<数钱>", "<读信>", "<投币>",
            "<黑客>", "<疑惑>", "<生闷气>", "<比耶>", "<画圈圈>",
            # 生活/动作
            "<爆炸>", "<不要捡>", "<看戏>", "<打游戏>", "<害怕>", "<黑脸>", "<喝奶茶>", "<红牌>",
            "<举牌>", "<酷>", "<落叶>", "<懵>", "<魔法>", "<OK>", "<秋天>", "<撒钱>",
            "<睡觉>", "<我好了>", "<休息>", "<学废>", "<问号>", "<应援>", "<正坐>"
        ]
    }


@app.put("/api/config/emoji")
async def update_emoji_config(config: EmojiMappingUpdate, token: str = Depends(verify_token)):
    """更新表情映射（运行时）"""
    updated = []
    
    for mapping in config.mappings:
        placeholder = mapping.placeholder
        discord_emojis = mapping.discord_emojis
        
        # 验证 Discord 表情格式
        for emoji in discord_emojis:
            if not re.match(r'^<a?:\w+:\d+>$', emoji):
                raise HTTPException(400, f"无效的 Discord 表情格式: {emoji}")
        
        # 查找并更新现有映射
        found = False
        escaped_placeholder = placeholder.replace("<", "\\<").replace(">", "\\>")
        for i, (pattern, _) in enumerate(emoji_config.EMOJI_MAPPINGS):
            if pattern.pattern == escaped_placeholder:
                emoji_config.EMOJI_MAPPINGS[i] = (pattern, discord_emojis)
                found = True
                updated.append(placeholder)
                break
        
        # 如果不存在，添加新映射
        if not found:
            new_pattern = re.compile(escaped_placeholder)
            emoji_config.EMOJI_MAPPINGS.append((new_pattern, discord_emojis))
            updated.append(placeholder)
    
    log.info(f"表情映射已更新: {updated}")
    return {"success": True, "updated": updated}


@app.post("/api/config/emoji/add")
async def add_emoji_mapping(mapping: EmojiMapping, token: str = Depends(verify_token)):
    """添加新的表情映射"""
    placeholder = mapping.placeholder
    discord_emojis = mapping.discord_emojis
    
    # 验证占位符格式
    if not re.match(r'^<\S+>$', placeholder):
        raise HTTPException(400, "占位符格式必须为 <名称>")
    
    # 验证 Discord 表情格式
    for emoji in discord_emojis:
        if not re.match(r'^<a?:\w+:\d+>$', emoji):
            raise HTTPException(400, f"无效的 Discord 表情格式: {emoji}")
    
    # 检查是否已存在
    escaped_placeholder = placeholder.replace("<", "\\<").replace(">", "\\>")
    for pattern, _ in emoji_config.EMOJI_MAPPINGS:
        if pattern.pattern == escaped_placeholder:
            raise HTTPException(400, f"占位符 {placeholder} 已存在")
    
    # 添加新映射
    new_pattern = re.compile(escaped_placeholder)
    emoji_config.EMOJI_MAPPINGS.append((new_pattern, discord_emojis))
    
    log.info(f"新增表情映射: {placeholder} -> {discord_emojis}")
    return {"success": True, "message": f"已添加 {placeholder}"}


@app.delete("/api/config/emoji/{placeholder}")
async def delete_emoji_mapping(placeholder: str, token: str = Depends(verify_token)):
    """删除表情映射"""
    escaped_placeholder = placeholder.replace("<", "\\<").replace(">", "\\>")
    
    for i, (pattern, _) in enumerate(emoji_config.EMOJI_MAPPINGS):
        if pattern.pattern == escaped_placeholder:
            del emoji_config.EMOJI_MAPPINGS[i]
            log.info(f"删除表情映射: {placeholder}")
            return {"success": True, "message": f"已删除 {placeholder}"}
    
    raise HTTPException(404, f"找不到占位符 {placeholder}")


@app.post("/api/config/test-imagen")
async def test_imagen_connection(token: str = Depends(verify_token)):
    """测试 Imagen API 连接"""
    try:
        from src.chat.features.image_generation.services.gemini_imagen_service import (
            gemini_imagen_service
        )
        
        result = await gemini_imagen_service.generate_single_image(
            prompt="A simple test image of a white circle on black background",
            aspect_ratio="1:1"
        )
        
        if result.get("success"):
            return {"success": True, "message": "连接测试成功"}
        else:
            return {"success": False, "error": result.get("error", "未知错误")}
    except Exception as e:
        log.error(f"Imagen API 测试失败: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


# --- 知识库管理 API ---

@app.get("/api/knowledge/documents")
async def list_knowledge_documents(
    token: str = Depends(verify_token),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None
):
    """获取知识库文档列表"""
    conn = get_parade_db_connection()
    if not conn:
        raise HTTPException(500, "无法连接到数据库")
    
    try:
        cursor = conn.cursor(cursor_factory=DictCursor)
        offset = (page - 1) * page_size
        
        # 构建查询
        if search:
            count_query = """
                SELECT COUNT(*) FROM general_knowledge.knowledge_documents
                WHERE title ILIKE %s OR full_text ILIKE %s
            """
            search_pattern = f"%{search}%"
            cursor.execute(count_query, (search_pattern, search_pattern))
        else:
            count_query = "SELECT COUNT(*) FROM general_knowledge.knowledge_documents"
            cursor.execute(count_query)
        
        total = cursor.fetchone()[0]
        
        if search:
            query = """
                SELECT id, external_id, title,
                       LEFT(full_text, 200) as preview,
                       source_metadata,
                       created_at, updated_at
                FROM general_knowledge.knowledge_documents
                WHERE title ILIKE %s OR full_text ILIKE %s
                ORDER BY updated_at DESC
                LIMIT %s OFFSET %s
            """
            cursor.execute(query, (search_pattern, search_pattern, page_size, offset))
        else:
            query = """
                SELECT id, external_id, title,
                       LEFT(full_text, 200) as preview,
                       source_metadata,
                       created_at, updated_at
                FROM general_knowledge.knowledge_documents
                ORDER BY updated_at DESC
                LIMIT %s OFFSET %s
            """
            cursor.execute(query, (page_size, offset))
        
        documents = []
        for row in cursor.fetchall():
            doc = {
                "id": row["id"],
                "external_id": row["external_id"],
                "title": row["title"] or "无标题",
                "preview": row["preview"] + "..." if row["preview"] and len(row["preview"]) >= 200 else row["preview"],
                "category": row["source_metadata"].get("category") if row["source_metadata"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
            documents.append(doc)
        
        # 获取分块统计
        cursor.execute("SELECT COUNT(*) FROM general_knowledge.knowledge_chunks")
        total_chunks = cursor.fetchone()[0]
        
        return {
            "documents": documents,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "total_chunks": total_chunks
        }
    except Exception as e:
        log.error(f"获取知识库列表失败: {e}", exc_info=True)
        raise HTTPException(500, f"获取列表失败: {str(e)}")
    finally:
        conn.close()


@app.get("/api/knowledge/documents/{doc_id}")
async def get_knowledge_document(doc_id: int, token: str = Depends(verify_token)):
    """获取单个知识文档详情"""
    conn = get_parade_db_connection()
    if not conn:
        raise HTTPException(500, "无法连接到数据库")
    
    try:
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("""
            SELECT id, external_id, title, full_text, source_metadata, created_at, updated_at
            FROM general_knowledge.knowledge_documents
            WHERE id = %s
        """, (doc_id,))
        
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        
        # 获取分块数量
        cursor.execute("""
            SELECT COUNT(*) FROM general_knowledge.knowledge_chunks WHERE document_id = %s
        """, (doc_id,))
        chunk_count = cursor.fetchone()[0]
        
        return {
            "id": row["id"],
            "external_id": row["external_id"],
            "title": row["title"],
            "content": row["full_text"],
            "metadata": row["source_metadata"],
            "chunk_count": chunk_count,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"获取文档详情失败: {e}", exc_info=True)
        raise HTTPException(500, f"获取文档失败: {str(e)}")
    finally:
        conn.close()


@app.post("/api/knowledge/documents")
async def create_knowledge_document(
    doc: KnowledgeDocumentCreate,
    token: str = Depends(verify_token)
):
    """创建新的知识文档"""
    conn = get_parade_db_connection()
    if not conn:
        raise HTTPException(500, "无法连接到数据库")
    
    try:
        cursor = conn.cursor(cursor_factory=DictCursor)
        
        # 生成唯一的 external_id
        external_id = f"dashboard_{uuid.uuid4().hex[:12]}"
        
        # 准备元数据
        metadata = {
            "source": "dashboard",
            "category": doc.category,
            "created_via": "web_dashboard"
        }
        
        cursor.execute("""
            INSERT INTO general_knowledge.knowledge_documents
            (external_id, title, full_text, source_metadata)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (external_id, doc.title, doc.content, json.dumps(metadata)))
        
        new_id = cursor.fetchone()[0]
        conn.commit()
        
        log.info(f"通过 Dashboard 创建知识文档: id={new_id}, title={doc.title}")
        
        return {
            "success": True,
            "id": new_id,
            "external_id": external_id,
            "message": "文档创建成功。注意：需要运行嵌入脚本来生成向量分块才能用于 RAG 搜索。"
        }
    except Exception as e:
        conn.rollback()
        log.error(f"创建知识文档失败: {e}", exc_info=True)
        raise HTTPException(500, f"创建失败: {str(e)}")
    finally:
        conn.close()


@app.put("/api/knowledge/documents/{doc_id}")
async def update_knowledge_document(
    doc_id: int,
    doc: KnowledgeDocumentUpdate,
    token: str = Depends(verify_token)
):
    """更新知识文档"""
    conn = get_parade_db_connection()
    if not conn:
        raise HTTPException(500, "无法连接到数据库")
    
    try:
        cursor = conn.cursor(cursor_factory=DictCursor)
        
        # 检查文档是否存在
        cursor.execute("SELECT id FROM general_knowledge.knowledge_documents WHERE id = %s", (doc_id,))
        if not cursor.fetchone():
            raise HTTPException(404, "文档不存在")
        
        updates = []
        params = []
        
        if doc.title is not None:
            updates.append("title = %s")
            params.append(doc.title)
        
        if doc.content is not None:
            updates.append("full_text = %s")
            params.append(doc.content)
        
        if not updates:
            raise HTTPException(400, "没有要更新的字段")
        
        updates.append("updated_at = NOW()")
        params.append(doc_id)
        
        query = f"""
            UPDATE general_knowledge.knowledge_documents
            SET {', '.join(updates)}
            WHERE id = %s
        """
        cursor.execute(query, params)
        conn.commit()
        
        log.info(f"通过 Dashboard 更新知识文档: id={doc_id}")
        
        return {
            "success": True,
            "message": "文档更新成功。如果内容有改动，建议重新运行嵌入脚本更新向量。"
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        log.error(f"更新知识文档失败: {e}", exc_info=True)
        raise HTTPException(500, f"更新失败: {str(e)}")
    finally:
        conn.close()


@app.delete("/api/knowledge/documents/{doc_id}")
async def delete_knowledge_document(doc_id: int, token: str = Depends(verify_token)):
    """删除知识文档及其所有分块"""
    conn = get_parade_db_connection()
    if not conn:
        raise HTTPException(500, "无法连接到数据库")
    
    try:
        cursor = conn.cursor(cursor_factory=DictCursor)
        
        # 检查文档是否存在
        cursor.execute("SELECT title FROM general_knowledge.knowledge_documents WHERE id = %s", (doc_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        
        title = row["title"]
        
        # 删除分块 (级联删除应该自动处理，但手动确保)
        cursor.execute("DELETE FROM general_knowledge.knowledge_chunks WHERE document_id = %s", (doc_id,))
        deleted_chunks = cursor.rowcount
        
        # 删除文档
        cursor.execute("DELETE FROM general_knowledge.knowledge_documents WHERE id = %s", (doc_id,))
        conn.commit()
        
        log.info(f"通过 Dashboard 删除知识文档: id={doc_id}, title={title}, chunks={deleted_chunks}")
        
        return {
            "success": True,
            "message": f"已删除文档「{title}」及其 {deleted_chunks} 个分块"
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        log.error(f"删除知识文档失败: {e}", exc_info=True)
        raise HTTPException(500, f"删除失败: {str(e)}")
    finally:
        conn.close()


@app.get("/api/knowledge/stats")
async def get_knowledge_stats(token: str = Depends(verify_token)):
    """获取知识库统计信息"""
    conn = get_parade_db_connection()
    if not conn:
        raise HTTPException(500, "无法连接到数据库")
    
    try:
        cursor = conn.cursor(cursor_factory=DictCursor)
        
        # 文档统计
        cursor.execute("SELECT COUNT(*) FROM general_knowledge.knowledge_documents")
        total_docs = cursor.fetchone()[0]
        
        # 分块统计
        cursor.execute("SELECT COUNT(*) FROM general_knowledge.knowledge_chunks")
        total_chunks = cursor.fetchone()[0]
        
        # 按来源分类统计
        cursor.execute("""
            SELECT
                COALESCE(source_metadata->>'source', 'unknown') as source,
                COUNT(*) as count
            FROM general_knowledge.knowledge_documents
            GROUP BY source_metadata->>'source'
        """)
        by_source = {row["source"]: row["count"] for row in cursor.fetchall()}
        
        # 最近添加的文档
        cursor.execute("""
            SELECT title, created_at
            FROM general_knowledge.knowledge_documents
            ORDER BY created_at DESC
            LIMIT 5
        """)
        recent = [{"title": row["title"], "created_at": row["created_at"].isoformat()} for row in cursor.fetchall()]
        
        return {
            "total_documents": total_docs,
            "total_chunks": total_chunks,
            "by_source": by_source,
            "recent_documents": recent
        }
    except Exception as e:
        log.error(f"获取知识库统计失败: {e}", exc_info=True)
        raise HTTPException(500, f"获取统计失败: {str(e)}")
    finally:
        conn.close()


# --- 静态文件服务 ---
# 前端构建后的静态文件将从这里提供
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def serve_frontend():
    """提供前端页面"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Dashboard API 正在运行。前端尚未构建。"}


def run_dashboard(host: str = "0.0.0.0", port: int = 8080):
    """启动 Dashboard 服务器"""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_dashboard()