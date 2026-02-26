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
        if turn.get('role') != 'user':
            continue
        for part in turn.get('parts', []):
            if isinstance(part, str):
                parts.append(part)
    return '\n'.join(parts)


def test_build_chat_prompt_injects_comfyui_model_and_lora_context_rules():
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
            comfyui_choice_context={
                'available_model_names': [
                    'zimage_real_v4.safetensors',
                    'qwen_photo_v2.safetensors',
                    'anime_mix_v9.safetensors',
                ],
                'available_lora_names': [
                    'skin_detail.safetensors',
                    '<wlr:portrait_softlight:0.8>',
                ],
            },
        )

        all_user_text = _collect_user_text(conversation)

        assert 'ComfyUI 底模与 LoRA 选择规则' in all_user_text
        assert '真人优先候选底模' in all_user_text
        assert 'zimage_real_v4.safetensors' in all_user_text
        assert 'qwen_photo_v2.safetensors' in all_user_text
        assert '可用底模列表' in all_user_text
        assert '可用 LoRA 列表' in all_user_text
        assert '中文自然语言描述' in all_user_text
        assert 'SD tag 风格' in all_user_text
        assert '只输出最终提示词正文' in all_user_text
        assert '前景/中景/背景' in all_user_text
        assert '建议不少于 12 句且不少于 350 字' in all_user_text
    finally:
        chat_config.DEFAULT_IMAGE_ENGINE = original_default_image_engine


def test_build_chat_prompt_treats_zit_model_as_real_human_candidate():
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
            comfyui_choice_context={
                'available_model_names': [
                    'moodyPornMix_zitV9.safetensors',
                    'moodyWildMix_v10Base50steps.safetensors',
                    'anime_mix_v9.safetensors',
                ],
                'available_lora_names': [],
            },
        )

        all_user_text = _collect_user_text(conversation)

        assert '真人优先候选底模' in all_user_text
        assert 'moodyPornMix_zitV9.safetensors' in all_user_text
        assert 'moodyWildMix_v10Base50steps.safetensors' in all_user_text
        assert 'zimage / qwen / zit / zib' in all_user_text
    finally:
        chat_config.DEFAULT_IMAGE_ENGINE = original_default_image_engine
