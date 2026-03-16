# -*- coding: utf-8 -*-

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

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


def test_build_chat_prompt_injects_final_tag_rule_when_novelai_prompt_model_disabled():
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

        assert "当前提示词模型已关闭" in all_user_text
        assert "最终可用的英文 Danbooru 标签串" in all_user_text
        assert "不要写自然语言摘要" in all_user_text
        assert "use_prompt_model=False" in all_user_text
    finally:
        chat_config.NOVELAI_CONFIG["USE_PROMPT_MODEL_IN_CHAT_TOOL"] = (
            original_use_prompt_model
        )
