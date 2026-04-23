# -*- coding: utf-8 -*-

import logging
from typing import Any, Dict, List

from src.chat.config import chat_config
from src.chat.features.tools.tool_metadata import get_tool_metadata, tool_metadata

log = logging.getLogger(__name__)


def _normalize_topic(topic: str) -> str:
    normalized = str(topic or "all").strip().lower()
    alias_map = {
        "all": "all",
        "全部": "all",
        "所有": "all",
        "工具": "all",
        "image": "image",
        "images": "image",
        "draw": "image",
        "绘图": "image",
        "生图": "image",
        "画图": "image",
        "comfyui": "image",
        "novelai": "image",
        "voice": "voice",
        "audio": "voice",
        "tts": "voice",
        "语音": "voice",
        "搜索": "search",
        "search": "search",
        "教程": "search",
        "论坛": "search",
        "profile": "profile",
        "资料": "profile",
        "名片": "profile",
        "用户": "profile",
    }
    return alias_map.get(normalized, "all")


def _match_topic(tool_name: str, topic: str) -> bool:
    if topic == "all":
        return True

    image_keywords = (
        "image",
        "video",
        "avatar",
        "gif",
        "novelai",
        "comfyui",
    )
    search_keywords = (
        "search",
        "query_tutorial",
        "summarize_channel",
        "web_fetch",
        "web_search",
    )
    profile_keywords = ("profile", "user_", "yearly_summary")

    if topic == "image":
        return any(keyword in tool_name for keyword in image_keywords)
    if topic == "voice":
        return "voice" in tool_name
    if topic == "search":
        return any(keyword in tool_name for keyword in search_keywords)
    if topic == "profile":
        return any(keyword in tool_name for keyword in profile_keywords)
    return True


def _preview_list(items: List[str], limit: int = 20) -> List[str]:
    preview = []
    seen = set()
    for raw_item in items or []:
        item = str(raw_item or "").strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        preview.append(item)
        if len(preview) >= limit:
            break
    return preview


def _normalize_default_image_engine() -> str:
    engine = str(chat_config.DEFAULT_IMAGE_ENGINE or "novelai").strip().lower()
    if engine not in {"novelai", "imagen", "comfyui"}:
        engine = "novelai"
    return engine


@tool_metadata(
    name="工具指南",
    description="按需查询当前可用工具、绘图/语音的实时规则与关键参数。",
    emoji="🧭",
    category="系统",
)
async def get_tool_usage_guide(topic: str = "all", **kwargs) -> Dict[str, Any]:
    """
    当你不确定该选哪个工具、有哪些当前可用工具、或某个工具的实时参数/预设/音色/底模时，先调用我。
    `topic` 可用值：all、image、voice、search、profile。
    """
    normalized_topic = _normalize_topic(topic)
    user_id_raw = kwargs.get("user_id")
    user_id = None
    if user_id_raw is not None:
        try:
            user_id = int(str(user_id_raw))
        except (TypeError, ValueError):
            user_id = None

    from src.chat.services.gemini_service import gemini_service

    visible_tools = await gemini_service.tool_service.get_dynamic_tools_for_context(
        str(user_id) if user_id is not None else None
    )
    tool_overview = []
    for func in visible_tools:
        tool_name = func.__name__
        if tool_name == "get_tool_usage_guide":
            continue
        if not _match_topic(tool_name, normalized_topic):
            continue
        meta = get_tool_metadata(tool_name) or {}
        tool_overview.append(
            {
                "tool_name": tool_name,
                "display_name": meta.get("name", tool_name),
                "description": meta.get("description", ""),
                "category": meta.get("category", "未分类"),
            }
        )

    default_image_engine = _normalize_default_image_engine()
    default_new_image_tool_map = {
        "novelai": "generate_image_novelai",
        "imagen": "generate_image / generate_images_batch",
        "comfyui": "generate_image_comfyui",
    }
    default_new_image_tool = default_new_image_tool_map[default_image_engine]

    guide: Dict[str, Any] = {
        "topic": normalized_topic,
        "tool_overview": tool_overview,
        "high_level_rules": [
            "不确定可用工具或实时参数时，先查工具指南，再调用真正工具。",
            "明确改原图/图生图时才用 edit_image；画新图默认跟随当前默认引擎。",
            "搜索、总结、教程、结构化结论场景优先文字，不要发语音。",
        ],
    }

    if normalized_topic in {"all", "image"}:
        image_guide: Dict[str, Any] = {
            "default_image_engine": default_image_engine,
            "default_new_image_tool": default_new_image_tool,
            "routing_rules": [
                "画新图默认优先当前默认引擎对应的新图工具。",
                "明确修改原图、基于参考图编辑时使用 edit_image。",
                "如果是画某个成员 / @某人，先调用 get_user_profile 查询 display_name + bio。",
                "名片里的外貌/人设描述为最高优先级；只有名片没有明确外貌时，才用头像兜底。",
                "用户明确指定 Imagen / NovelAI / ComfyUI 时，再覆盖默认引擎选择。",
            ],
        }

        if user_id is not None:
            try:
                preset_context = await gemini_service._load_novelai_preset_context(user_id)
            except Exception as e:
                log.warning(f"读取 NovelAI 预设上下文失败: {e}")
                preset_context = {}

            try:
                comfyui_context = await gemini_service._load_comfyui_choice_context(user_id)
            except Exception as e:
                log.warning(f"读取 ComfyUI 上下文失败: {e}")
                comfyui_context = {}

            if preset_context:
                image_guide["novelai_presets"] = {
                    "user_presets": _preview_list(
                        preset_context.get("user_preset_names") or [], limit=25
                    ),
                    "admin_presets": _preview_list(
                        preset_context.get("admin_preset_names") or [], limit=25
                    ),
                    "rule": "只有命中这些实时预设名时才传 preset_name；不确定时不要编造。",
                }

            if comfyui_context:
                image_guide["comfyui_choices"] = {
                    "model_names": _preview_list(
                        comfyui_context.get("available_model_names") or [], limit=30
                    ),
                    "vae_names": _preview_list(
                        comfyui_context.get("available_vae_names") or [], limit=20
                    ),
                    "clip_names": _preview_list(
                        comfyui_context.get("available_clip_names") or [], limit=20
                    ),
                    "lora_names": _preview_list(
                        comfyui_context.get("available_lora_names") or [], limit=30
                    ),
                    "rule": "若用户点名具体底模/VAE/CLIP/LoRA，只有命中这些实时列表时才传参。",
                }

        guide["image"] = image_guide

    if normalized_topic in {"all", "voice"}:
        available_voice_types = _preview_list(
            [
                str(name).strip()
                for name in (chat_config.VOICE_CONFIG.get("AVAILABLE_VOICE_TYPES") or [])
                if str(name).strip()
            ],
            limit=25,
        )
        voice_type_hints_raw = chat_config.VOICE_CONFIG.get("VOICE_TYPE_HINTS") or {}
        voice_type_hints = []
        if isinstance(voice_type_hints_raw, dict):
            voice_type_hints = [
                f"{str(key).strip()} => {str(value).strip()}"
                for key, value in voice_type_hints_raw.items()
                if str(key).strip() and str(value).strip()
            ][:20]

        guide["voice"] = {
            "provider": str(chat_config.VOICE_CONFIG.get("PROVIDER", "") or "").strip()
            or "unknown",
            "model_name": str(chat_config.VOICE_CONFIG.get("MODEL_NAME", "") or "").strip()
            or "unknown",
            "default_voice_type": str(
                chat_config.VOICE_CONFIG.get("VOICE_TYPE", "") or ""
            ).strip()
            or "default",
            "rules": [
                "搜索、总结、教程、结构化结果场景一律优先文字。",
                "若用户明确要求发语音，或你确定语音比文字更合适，再调用 generate_voice。",
                "不确定音色、emotion、实时可用枚举时，先查本工具返回的数据，不要编造。",
            ],
            "available_voice_types": available_voice_types,
            "voice_type_hints": voice_type_hints,
        }

    if normalized_topic in {"all", "search"}:
        guide["search"] = {
            "rules": [
                "社区教程问题优先 query_tutorial_knowledge_base。",
                "需要联网检索时优先 web_search / web_fetch。",
                "频道历史回顾用 summarize_channel；需要翻历史细节时用 search_channel_history。",
            ]
        }

    if normalized_topic in {"all", "profile"}:
        guide["profile"] = {
            "rules": [
                "查余额、头像、角色等资料可用 get_user_profile。",
                "画某个成员 / @某人时，优先查 get_user_profile 的 display_name + bio 再写绘图提示。",
                "需要年度总结时用 get_yearly_summary。",
                "涉及个人名片、长期记忆时，优先结合已注入的人物背景和世界书信息回答。",
            ]
        }

    return guide
