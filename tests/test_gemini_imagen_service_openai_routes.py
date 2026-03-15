# -*- coding: utf-8 -*-

import asyncio
import base64
import inspect
import os
import sys

sys.path.insert(0, os.path.abspath("."))

from src.chat.config import chat_config as app_config
from src.chat.features.image_generation.services.gemini_imagen_service import (
    GeminiImagenService,
)
from src.chat.features.tools.functions.edit_image import edit_image
from src.chat.features.tools.functions.generate_image import generate_image


OPENAI_IMAGE_PARAM_NAMES = (
    "model_name_override",
    "openai_image_size",
    "openai_response_format",
    "openai_stream",
    "openai_quality",
    "openai_style",
    "openai_image_api_mode",
)


def test_image_tools_expose_openai_route_params():
    generate_signature = inspect.signature(generate_image)
    edit_signature = inspect.signature(edit_image)

    for param_name in OPENAI_IMAGE_PARAM_NAMES:
        assert param_name in generate_signature.parameters
        assert param_name in edit_signature.parameters


def test_resolve_openai_image_api_mode_routes_grok_to_images_api(monkeypatch):
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "OPENAI_IMAGE_API_MODE", "auto")
    service = GeminiImagenService()

    assert service._resolve_openai_image_api_mode("grok-imagine-1.0") == "images_api"
    assert service._resolve_openai_image_api_mode("gpt-image-1") == "images_api"


def test_resolve_openai_image_api_mode_routes_regular_models_to_chat(monkeypatch):
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "OPENAI_IMAGE_API_MODE", "auto")
    service = GeminiImagenService()

    assert service._resolve_openai_image_api_mode("gpt-4.1") == "chat_completions"
    assert service._resolve_openai_image_api_mode("imagen-3.0-generate-002") == "chat_completions"


def test_parse_sse_payload_text_extracts_data_items():
    service = GeminiImagenService()
    raw_text = (
        'data: {"id":"img_1","data":[{"b64_json":"aGVsbG8="}]}\n'
        "\n"
        "data: [DONE]\n"
    )

    parsed = asyncio.run(service._parse_sse_payload_text(raw_text))

    assert parsed == {"data": [{"b64_json": "aGVsbG8="}]}


def test_extract_images_from_openai_response_supports_b64_json():
    service = GeminiImagenService()
    expected_bytes = b"fake-image-bytes"
    encoded = base64.b64encode(expected_bytes).decode("ascii")

    images = asyncio.run(
        service._extract_images_from_openai_response(
            {"data": [{"b64_json": encoded}]},
            response_format_override="base64",
        )
    )

    assert images == [expected_bytes]


def test_extract_images_from_openai_response_supports_url(monkeypatch):
    service = GeminiImagenService()
    expected_bytes = b"url-image-bytes"

    async def _fake_download(url: str):
        assert url == "https://example.com/test.png"
        return expected_bytes

    monkeypatch.setattr(service, "_download_image_from_url", _fake_download)

    images = asyncio.run(
        service._extract_images_from_openai_response(
            {"data": [{"url": "https://example.com/test.png"}]},
            response_format_override="url",
        )
    )

    assert images == [expected_bytes]
