# -*- coding: utf-8 -*-

import importlib
import asyncio
import os
import sys
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath("."))
os.environ.setdefault("GEMINI_API_KEYS", "test-key")

if "discord" not in sys.modules:
    discord_module = types.ModuleType("discord")

    class _DummyEmbed:
        def __init__(self, *args, **kwargs):
            pass

    class _DummyFile:
        def __init__(self, *args, **kwargs):
            pass

    class _DummyMessage:
        pass

    discord_module.Embed = _DummyEmbed
    discord_module.File = _DummyFile
    discord_module.Message = _DummyMessage
    discord_module.abc = types.SimpleNamespace(User=type("User", (), {}))
    sys.modules["discord"] = discord_module

mock_google_genai = MagicMock()
google_module = sys.modules.get("google")
if google_module is None:
    google_module = types.ModuleType("google")
    google_module.__path__ = []
    sys.modules["google"] = google_module
setattr(google_module, "genai", mock_google_genai)
sys.modules.setdefault("google.genai", mock_google_genai)
sys.modules.setdefault("google.genai.types", MagicMock())
sys.modules.setdefault("google.genai.errors", MagicMock())
sys.modules.setdefault("src.chat.services.context_service", MagicMock())
sys.modules.setdefault(
    "src.chat.features.affection.service.affection_service",
    MagicMock(),
)
sys.modules.setdefault("src.chat.services.prompt_service", MagicMock())
sys.modules.setdefault(
    "src.chat.features.tools.services.tool_service",
    MagicMock(ToolService=MagicMock()),
)
sys.modules.setdefault(
    "src.chat.features.tools.utils.discord_image_utils",
    MagicMock(fetch_avatar_image=MagicMock()),
)
sys.modules.setdefault(
    "src.chat.features.tools.tool_loader",
    MagicMock(load_tools_from_directory=MagicMock(return_value=([], {}))),
)
sys.modules.setdefault(
    "src.chat.features.chat_settings.services.chat_settings_service",
    MagicMock(chat_settings_service=MagicMock()),
)
sys.modules.setdefault(
    "src.database.services.token_usage_service",
    MagicMock(token_usage_service=MagicMock()),
)
sys.modules.setdefault(
    "src.database.database",
    MagicMock(AsyncSessionLocal=MagicMock()),
)

generate_image_tool = importlib.import_module(
    "src.chat.features.tools.functions.generate_image"
)
imagen_service_module = importlib.import_module(
    "src.chat.features.image_generation.services.gemini_imagen_service"
)
gemini_service_module = importlib.import_module("src.chat.services.gemini_service")
app_config = importlib.import_module("src.chat.config.chat_config")


def test_generate_image_rewrites_english_prompt_to_chinese(monkeypatch):
    captured: dict = {}

    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "IMAGE_GENERATION_COST", 0)
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "DEFAULT_NUMBER_OF_IMAGES", 1)
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "MAX_IMAGES_PER_REQUEST", 4)
    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "is_available",
        lambda: True,
    )

    async def fake_generate_single_image(**kwargs):
        captured.update(kwargs)
        return b"fake-image"

    async def fake_generate_simple_response(**kwargs):
        request_text = kwargs["messages"][-1]["content"]
        if "原负面提示词" in request_text:
            return "不要出现文字水印、模糊和低清晰度"
        return "一只可爱的小猫，毛茸茸的皮毛，柔和的光线，细节丰富"

    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "generate_single_image",
        fake_generate_single_image,
    )
    monkeypatch.setattr(
        gemini_service_module.gemini_service,
        "generate_simple_response",
        fake_generate_simple_response,
    )

    result = asyncio.run(
        generate_image_tool.generate_image(
            prompt="cute fluffy cat, soft lighting, highly detailed",
            negative_prompt="text watermark, blurry, low quality",
            preview_message=None,
            success_message=None,
            number_of_images=1,
        )
    )

    assert result["success"] is True
    assert captured["prompt"] == "一只可爱的小猫，毛茸茸的皮毛，柔和的光线，细节丰富"
    assert captured["negative_prompt"] == "不要出现文字水印、模糊和低清晰度"


def test_generate_image_keeps_chinese_prompt_without_rewrite(monkeypatch):
    captured: dict = {}

    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "IMAGE_GENERATION_COST", 0)
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "DEFAULT_NUMBER_OF_IMAGES", 1)
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "MAX_IMAGES_PER_REQUEST", 4)
    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "is_available",
        lambda: True,
    )

    async def fake_generate_single_image(**kwargs):
        captured.update(kwargs)
        return b"fake-image"

    async def fail_if_called(**kwargs):
        raise AssertionError("中文提示词不应触发额外改写")

    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "generate_single_image",
        fake_generate_single_image,
    )
    monkeypatch.setattr(
        gemini_service_module.gemini_service,
        "generate_simple_response",
        fail_if_called,
    )

    result = asyncio.run(
        generate_image_tool.generate_image(
            prompt="一只可爱的小猫，毛茸茸的皮毛，柔和的光线，细节丰富",
            negative_prompt="不要出现文字水印、模糊和低清晰度",
            preview_message=None,
            success_message=None,
            number_of_images=1,
        )
    )

    assert result["success"] is True
    assert captured["prompt"] == "一只可爱的小猫，毛茸茸的皮毛，柔和的光线，细节丰富"
    assert captured["negative_prompt"] == "不要出现文字水印、模糊和低清晰度"
