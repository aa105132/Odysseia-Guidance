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
fake_image_utils.extract_image_frames_for_ai = (
    lambda *args, **kwargs: ([object()], {"is_animated": False})
)
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


def test_build_chat_prompt_injects_human_style_and_thread_first_post_context():
    prompt_service = PromptService()

    conversation = prompt_service.build_chat_prompt(
        user_name="测试用户",
        message="我来了",
        replied_message=None,
        images=[],
        channel_context=[],
        world_book_entries=[],
        affection_status=None,
        guild_name="测试服务器",
        location_name="测试频道",
        user_id=123456,
        thread_first_post_context=(
            "<thread_first_post>\n"
            "帖子标题: 测试帖子\n"
            "发帖人: 楼主A\n"
            "标签: 日常\n"
            "首楼内容:\n"
            "今天想聊聊最近玩的游戏。\n"
            "</thread_first_post>"
        ),
    )

    all_user_text = _collect_user_text(conversation)

    assert "聊天风格补充（高优先级）" in all_user_text
    assert "先像正在这个频道里聊天的真人群友一样" in all_user_text
    assert "如果最近两三轮已经用过某种开头、结尾、撒娇句或狠话" in all_user_text
    assert "普通闲聊长度由当下话题决定" in all_user_text
    assert "不要机械卡字数" in all_user_text
    assert "10-50字之间随机浮动" not in all_user_text
    assert "<thread_first_post>" in all_user_text
    assert "今天想聊聊最近玩的游戏。" in all_user_text


def test_build_chat_prompt_reply_image_routing_uses_default_new_image_tool():
    prompt_service = PromptService()
    original_default_image_engine = chat_config.DEFAULT_IMAGE_ENGINE

    try:
        chat_config.DEFAULT_IMAGE_ENGINE = "comfyui"

        conversation = prompt_service.build_chat_prompt(
            user_name="测试用户",
            message="照这个风格再画一张",
            replied_message="> [回复 楼主A]: 这张图的构图我很喜欢",
            images=[
                {
                    "source": "replied_attachment",
                    "data": b"fake-image",
                    "mime_type": "image/png",
                }
            ],
            channel_context=[],
            world_book_entries=[],
            affection_status=None,
            guild_name="测试服务器",
            location_name="测试频道",
            user_id=123456,
        )

        all_user_text = _collect_user_text(conversation)

        assert "应优先调用 generate_image_comfyui" in all_user_text
        assert "参考画风/元素新画一张优先 generate_image_comfyui" in all_user_text
    finally:
        chat_config.DEFAULT_IMAGE_ENGINE = original_default_image_engine


def test_build_tool_result_wrapper_prompt_uses_relaxed_length_override_for_search():
    prompt_service = PromptService()

    prompt = prompt_service.build_tool_result_wrapper_prompt(
        "web_search", "测试搜索结果"
    )

    assert "普通闲聊优先短回" in prompt
    assert "聊天场景优先短段落、自然分段" in prompt
    assert "50字限制" not in prompt
