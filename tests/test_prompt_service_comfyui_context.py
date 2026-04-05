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
        if turn.get('role') != 'user':
            continue
        for part in turn.get('parts', []):
            if isinstance(part, str):
                parts.append(part)
    return '\n'.join(parts)


def test_build_chat_prompt_uses_compact_tool_guidance_for_comfyui():
    prompt_service = PromptService()
    original_default_image_engine = chat_config.DEFAULT_IMAGE_ENGINE

    try:
        chat_config.DEFAULT_IMAGE_ENGINE = 'comfyui'

        conversation = prompt_service.build_chat_prompt(
            user_name='测试用户',
            message='帮我画一个真人写真',
            replied_message=None,
            images=[],
            channel_context=[],
            world_book_entries=[],
            affection_status=None,
            guild_name='测试服务器',
            location_name='测试频道',
            user_id=123456,
        )

        all_user_text = _collect_user_text(conversation)

        assert '工具调用协议（精简版）' in all_user_text
        assert 'get_tool_usage_guide' in all_user_text
        assert 'generate_image_comfyui' in all_user_text
        assert '可用底模列表' not in all_user_text
        assert 'zimage_real_v4.safetensors' not in all_user_text
        assert 'skin_detail.safetensors' not in all_user_text
    finally:
        chat_config.DEFAULT_IMAGE_ENGINE = original_default_image_engine


def test_build_chat_prompt_does_not_inline_comfyui_choice_lists():
    prompt_service = PromptService()
    original_default_image_engine = chat_config.DEFAULT_IMAGE_ENGINE

    try:
        chat_config.DEFAULT_IMAGE_ENGINE = 'comfyui'

        conversation = prompt_service.build_chat_prompt(
            user_name='测试用户',
            message='帮我画一个真人写真',
            replied_message=None,
            images=[],
            channel_context=[],
            world_book_entries=[],
            affection_status=None,
            guild_name='测试服务器',
            location_name='测试频道',
            user_id=123456,
        )

        all_user_text = _collect_user_text(conversation)

        assert 'get_tool_usage_guide' in all_user_text
        assert 'moodyPornMix_zitV9.safetensors' not in all_user_text
        assert 'moodyWildMix_v10Base50steps.safetensors' not in all_user_text
    finally:
        chat_config.DEFAULT_IMAGE_ENGINE = original_default_image_engine
