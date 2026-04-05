# -*- coding: utf-8 -*-

import os
import sys
import types

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

fake_image_module = types.ModuleType("PIL.Image")
fake_image_module.Image = object
fake_image_module.Resampling = types.SimpleNamespace(LANCZOS=0)

fake_image_draw_module = types.ModuleType("PIL.ImageDraw")
fake_image_draw_module.Draw = lambda *args, **kwargs: types.SimpleNamespace(
    text=lambda *a, **k: None
)

fake_pil_package = types.ModuleType("PIL")
fake_pil_package.Image = fake_image_module
fake_pil_package.ImageDraw = fake_image_draw_module

sys.modules.setdefault("PIL", fake_pil_package)
sys.modules.setdefault("PIL.Image", fake_image_module)
sys.modules.setdefault("PIL.ImageDraw", fake_image_draw_module)

fake_image_utils = types.ModuleType("src.chat.utils.image_utils")
fake_image_utils.extract_image_frames_for_ai = lambda *args, **kwargs: []
sys.modules.setdefault("src.chat.utils.image_utils", fake_image_utils)

fake_context_service_module = types.ModuleType("src.chat.services.context_service")
fake_context_service_module.context_service = types.SimpleNamespace(
    clean_message_content=lambda content, guild=None: content
)
sys.modules.setdefault("src.chat.services.context_service", fake_context_service_module)

from src.chat.config import chat_config
from src.chat.services.prompt_service import PromptService


def _collect_user_text(conversation: list[dict]) -> str:
    parts: list[str] = []
    for turn in conversation:
        if turn.get("role") != "user":
            continue
        for part in turn.get("parts", []):
            if isinstance(part, str):
                parts.append(part)
    return "\n".join(parts)


def test_build_chat_prompt_uses_tool_guide_instead_of_inlining_novelai_rules():
    prompt_service = PromptService()
    original_use_prompt_model = chat_config.NOVELAI_CONFIG.get(
        "USE_PROMPT_MODEL_IN_CHAT_TOOL", True
    )

    try:
        chat_config.NOVELAI_CONFIG["USE_PROMPT_MODEL_IN_CHAT_TOOL"] = False

        conversation = prompt_service.build_chat_prompt(
            user_name="测试用户",
            message="帮我画一张图",
            replied_message=None,
            images=[],
            channel_context=[],
            world_book_entries=[],
            affection_status=None,
            guild_name="测试服务器",
            location_name="测试频道",
            user_id=123456,
        )

        all_user_text = _collect_user_text(conversation)

        assert "工具调用协议（精简版）" in all_user_text
        assert "get_tool_usage_guide" in all_user_text
        assert "当前提示词模型已关闭" not in all_user_text
        assert "最终可用的英文 Danbooru 标签串" not in all_user_text
    finally:
        chat_config.NOVELAI_CONFIG["USE_PROMPT_MODEL_IN_CHAT_TOOL"] = (
            original_use_prompt_model
        )
