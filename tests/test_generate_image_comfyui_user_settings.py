# -*- coding: utf-8 -*-

import os
import sys

import pytest
import discord

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if not hasattr(discord, 'ui'):
    pytest.skip('discord.ui 不可用，跳过该测试文件', allow_module_level=True)

from src.chat.features.tools.functions import generate_image_comfyui as comfy_tool
from src.chat.utils.database import chat_db_manager


def test_is_default_workflow_path_uses_runtime_defaults(monkeypatch):
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'WORKFLOW_PATH', 'D:/Comfy/default.json')
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'DEFAULT_REALISTIC_WORKFLOW_PATH', 'D:/Comfy/realistic.json')
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'DEFAULT_ANIME_WORKFLOW_PATH', 'D:/Comfy/anime.json')

    assert comfy_tool._is_default_workflow_path('')
    assert comfy_tool._is_default_workflow_path('D:\\Comfy\\default.json')
    assert comfy_tool._is_default_workflow_path('d:/comfy/realistic.json')
    assert comfy_tool._is_default_workflow_path('D:\\Comfy\\anime.json')
    assert not comfy_tool._is_default_workflow_path('D:\\Comfy\\users\\u1\\workflow.json')


@pytest.mark.asyncio
async def test_generate_image_comfyui_skip_user_persisted_settings_on_default_workflow(monkeypatch):
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'ENABLED', True)
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'IMAGE_GENERATION_COST', 0)
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'WORKFLOW_PATH', 'D:/Comfy/default.json')
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'DEFAULT_REALISTIC_WORKFLOW_PATH', '')
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'DEFAULT_ANIME_WORKFLOW_PATH', '')

    monkeypatch.setattr(comfy_tool.comfyui_service, 'is_server_ready', lambda: True)
    monkeypatch.setattr(comfy_tool.comfyui_service, 'workflow_template', {'1': {'inputs': {'text': '{{positive_prompt}}'}}})
    monkeypatch.setattr(
        comfy_tool.comfyui_service,
        'resolve_default_model_name',
        lambda **kwargs: '',
    )

    captured: dict = {}

    async def fake_generate_media(**kwargs):
        captured.update(kwargs)
        return {
            'bytes': b'img',
            'filename': 'a.png',
            'mime_type': 'image/png',
            'media_kind': 'image',
        }

    async def fake_get_user_settings(_user_id: int):
        return {
            'workflow_path': '',
            'default_lora': 'user_lora.safetensors',
            'width': 1024,
            'height': 1536,
            'steps': 55,
            'cfg': 7.5,
            'sampler': 'dpmpp_2m',
            'scheduler': 'karras',
            'seed': 777,
            'model_name': 'user_model.safetensors',
            'vae_name': 'user_vae.safetensors',
            'clip_name': 'user_clip.safetensors',
            'fixed_positive_prompt': 'user_fixed_positive',
            'fixed_negative_prompt': 'user_fixed_negative',
        }

    monkeypatch.setattr(comfy_tool.comfyui_service, 'generate_media', fake_generate_media)
    monkeypatch.setattr(chat_db_manager, 'get_comfyui_user_settings', fake_get_user_settings)

    result = await comfy_tool.generate_image_comfyui(
        prompt='测试默认工作流',
        user_id='123',
    )

    assert result.get('success') is True
    assert captured.get('workflow_path') is None
    assert captured.get('width') is None
    assert captured.get('height') is None
    assert captured.get('steps') is None
    assert captured.get('cfg') is None
    assert captured.get('sampler') is None
    assert captured.get('scheduler') is None
    assert captured.get('seed') is None
    assert captured.get('lora') is None
    assert captured.get('model_name') is None
    assert captured.get('vae_name') is None
    assert captured.get('clip_name') is None
    assert captured.get('user_fixed_positive_prompt') == ''
    assert captured.get('user_fixed_negative_prompt') == ''


@pytest.mark.asyncio
async def test_generate_image_comfyui_keep_user_persisted_settings_on_user_workflow(monkeypatch):
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'ENABLED', True)
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'IMAGE_GENERATION_COST', 0)
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'WORKFLOW_PATH', 'D:/Comfy/default.json')
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'DEFAULT_REALISTIC_WORKFLOW_PATH', '')
    monkeypatch.setitem(comfy_tool.COMFYUI_CONFIG, 'DEFAULT_ANIME_WORKFLOW_PATH', '')

    monkeypatch.setattr(comfy_tool.comfyui_service, 'is_server_ready', lambda: True)
    monkeypatch.setattr(comfy_tool.comfyui_service, 'workflow_template', {'1': {'inputs': {'text': '{{positive_prompt}}'}}})
    monkeypatch.setattr(
        comfy_tool.comfyui_service,
        'resolve_default_model_name',
        lambda **kwargs: '',
    )

    captured: dict = {}

    async def fake_generate_media(**kwargs):
        captured.update(kwargs)
        return {
            'bytes': b'img',
            'filename': 'a.png',
            'mime_type': 'image/png',
            'media_kind': 'image',
        }

    async def fake_get_user_settings(_user_id: int):
        return {
            'workflow_path': 'D:/Comfy/users/u1/workflow.json',
            'default_lora': 'user_lora.safetensors',
            'width': 1024,
            'height': 1536,
            'steps': 55,
            'cfg': 7.5,
            'sampler': 'dpmpp_2m',
            'scheduler': 'karras',
            'seed': 777,
            'model_name': 'user_model.safetensors',
            'vae_name': 'user_vae.safetensors',
            'clip_name': 'user_clip.safetensors',
            'fixed_positive_prompt': 'user_fixed_positive',
            'fixed_negative_prompt': 'user_fixed_negative',
        }

    monkeypatch.setattr(comfy_tool.comfyui_service, 'generate_media', fake_generate_media)
    monkeypatch.setattr(chat_db_manager, 'get_comfyui_user_settings', fake_get_user_settings)

    result = await comfy_tool.generate_image_comfyui(
        prompt='测试用户工作流',
        user_id='123',
    )

    assert result.get('success') is True
    assert captured.get('workflow_path') == 'D:/Comfy/users/u1/workflow.json'
    assert captured.get('width') == 1024
    assert captured.get('height') == 1536
    assert captured.get('steps') == 55
    assert captured.get('cfg') == 7.5
    assert captured.get('sampler') == 'dpmpp_2m'
    assert captured.get('scheduler') == 'karras'
    assert captured.get('seed') == 777
    assert captured.get('lora') == 'user_lora.safetensors'
    assert captured.get('model_name') == 'user_model.safetensors'
    assert captured.get('vae_name') == 'user_vae.safetensors'
    assert captured.get('clip_name') == 'user_clip.safetensors'
    assert captured.get('user_fixed_positive_prompt') == 'user_fixed_positive'
    assert captured.get('user_fixed_negative_prompt') == 'user_fixed_negative'
