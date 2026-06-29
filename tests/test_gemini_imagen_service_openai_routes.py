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


def test_resolve_openai_image_api_mode_routes_gpt_image_to_chat(monkeypatch):
    """gpt-image-* 默认走 chat/completions，避免强制锁死在 /images/generations。"""
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "OPENAI_IMAGE_API_MODE", "auto")
    service = GeminiImagenService()

    assert service._resolve_openai_image_api_mode("gpt-image-1") == "chat_completions"


def test_resolve_openai_image_api_mode_respects_images_api_override(monkeypatch):
    """显式指定 images_api 时，任意模型（含 gpt-image-*）都应走 /images/*。"""
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "OPENAI_IMAGE_API_MODE", "auto")
    service = GeminiImagenService()

    assert (
        service._resolve_openai_image_api_mode(
            "gpt-image-1", mode_override="images_api"
        )
        == "images_api"
    )


def test_resolve_openai_image_api_mode_routes_regular_models_to_chat(monkeypatch):
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "OPENAI_IMAGE_API_MODE", "auto")
    service = GeminiImagenService()

    assert service._resolve_openai_image_api_mode("gpt-4.1") == "chat_completions"
    assert service._resolve_openai_image_api_mode("imagen-3.0-generate-002") == "chat_completions"


def test_openai_streaming_timeout_has_no_wall_clock_total(monkeypatch):
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "STREAMING_TIMEOUT_SECONDS", 240)
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "REQUEST_TIMEOUT_SECONDS", 120)
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "CONNECT_TIMEOUT_SECONDS", 7)
    service = GeminiImagenService()

    streaming_timeout = service._build_openai_timeout(streaming=True)
    request_timeout = service._build_openai_timeout(streaming=False)

    assert streaming_timeout.total is None
    assert streaming_timeout.connect == 7
    assert streaming_timeout.sock_connect == 7
    assert streaming_timeout.sock_read == 240
    assert request_timeout.total == 120
    assert request_timeout.sock_read == 120


def test_generate_openai_format_keeps_grok_on_images_api(monkeypatch):
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "OPENAI_IMAGE_API_MODE", "auto")
    service = GeminiImagenService()

    async def _fake_images_api(**kwargs):
        return None

    async def _unexpected_chat_fallback(**kwargs):
        raise AssertionError("grok-imagine-* 不应回退到 chat/completions")

    monkeypatch.setattr(
        service, "_generate_image_openai_images_api_format", _fake_images_api
    )
    monkeypatch.setattr(
        service,
        "_generate_image_openai_chat_completions_format",
        _unexpected_chat_fallback,
    )

    result = asyncio.run(
        service._generate_image_openai_format(
            prompt="test",
            negative_prompt=None,
            aspect_ratio="1:1",
            number_of_images=1,
            model_name="grok-imagine-1.0",
        )
    )

    assert result is None






def test_generate_openai_format_reference_image_uses_images_edits(monkeypatch):
    """带参考图时应优先走 /images/edits，而不是丢图的 /images/generations。"""
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "OPENAI_IMAGE_API_MODE", "auto")
    service = GeminiImagenService()

    async def _unexpected_generations(**kwargs):
        raise AssertionError("带参考图的投喂生图不应走 /images/generations")

    async def _unexpected_chat(**kwargs):
        raise AssertionError("单张参考图应优先使用 /images/edits 传 image 字段")

    captured = {}
    expected = b"edited-from-reference"

    async def _fake_edits(**kwargs):
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(
        service, "_generate_image_openai_images_api_format", _unexpected_generations
    )
    monkeypatch.setattr(
        service, "_generate_image_openai_chat_completions_format", _unexpected_chat
    )
    monkeypatch.setattr(service, "_edit_image_openai_images_api_format", _fake_edits)

    result = asyncio.run(
        service._generate_image_openai_format(
            prompt="银狐少女认真吃用户投喂的食物",
            negative_prompt=None,
            aspect_ratio="1:1",
            number_of_images=1,
            model_name="grok-imagine-1.0",
            reference_image_bytes=b"food-reference",
            reference_image_mime="image/jpeg",
        )
    )

    assert result == [expected]
    assert captured["reference_images"] == [
        {"data": b"food-reference", "mime_type": "image/jpeg"}
    ]
    assert "必须以参考图里的真实食物为准" in captured["edit_prompt"]


def test_generate_openai_format_reference_image_skips_images_api(monkeypatch):
    """带参考图时不能走 /images/generations，否则参考图会被丢弃。"""
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "OPENAI_IMAGE_API_MODE", "auto")
    service = GeminiImagenService()

    async def _unexpected_images_api(**kwargs):
        raise AssertionError("带参考图的生成不应走 /images/generations")

    captured = {}
    expected = [b"generated-from-reference"]

    async def _fake_chat_completions(**kwargs):
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(
        service, "_generate_image_openai_images_api_format", _unexpected_images_api
    )
    monkeypatch.setattr(
        service,
        "_generate_image_openai_chat_completions_format",
        _fake_chat_completions,
    )

    result = asyncio.run(
        service._generate_image_openai_format(
            prompt="月月正在吃用户投喂的食物",
            negative_prompt=None,
            aspect_ratio="1:1",
            number_of_images=1,
            model_name="grok-imagine-1.0",
            reference_image_bytes=b"food-reference",
            reference_image_mime="image/jpeg",
        )
    )

    assert result == expected
    assert captured["reference_image_bytes"] == b"food-reference"
    assert captured["reference_image_mime"] == "image/jpeg"


def test_openai_chat_completions_reference_image_disables_streaming(monkeypatch):
    """参考图优先级高于流式配置，确保请求体里真实携带 image_url。"""
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "STREAMING_ENABLED", True)

    service = GeminiImagenService()
    service._client = {
        "api_key": "test-key",
        "base_url": "http://localhost:8000/v1",
    }

    async def _unexpected_streaming(**kwargs):
        raise AssertionError("带参考图时不应走不支持参考图的流式分支")

    monkeypatch.setattr(
        service, "_generate_image_openai_format_streaming", _unexpected_streaming
    )

    generated = b"generated-image"
    encoded = base64.b64encode(generated).decode("ascii")
    captured = {}

    class _FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return '{"data":[{"b64_json":"' + encoded + '"}]}'

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json
            captured["timeout"] = timeout
            return _FakeResponse()

    monkeypatch.setattr(
        "src.chat.features.image_generation.services.gemini_imagen_service.aiohttp.ClientSession",
        lambda: _FakeSession(),
    )

    result = asyncio.run(
        service._generate_image_openai_chat_completions_format(
            prompt="月月正在吃用户投喂的食物",
            negative_prompt=None,
            aspect_ratio="1:1",
            number_of_images=1,
            model_name="grok-imagine-1.0",
            reference_image_bytes=b"food-reference",
            reference_image_mime="image/jpeg",
            openai_stream=True,
        )
    )

    assert result == [generated]
    assert captured["url"] == "http://localhost:8000/v1/chat/completions"

    content = captured["payload"]["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )
    assert "stream" not in captured["payload"]

def test_edit_openai_format_keeps_grok_on_images_api(monkeypatch):
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "OPENAI_IMAGE_API_MODE", "auto")
    service = GeminiImagenService()

    async def _fake_images_api(**kwargs):
        return None

    async def _unexpected_chat_fallback(**kwargs):
        raise AssertionError("grok-imagine-* 编辑模型不应回退到 chat/completions")

    monkeypatch.setattr(
        service, "_edit_image_openai_images_api_format", _fake_images_api
    )
    monkeypatch.setattr(
        service,
        "_edit_image_openai_chat_completions_format",
        _unexpected_chat_fallback,
    )

    result = asyncio.run(
        service._edit_image_openai_format(
            reference_images=[{"data": b"avatar", "mime_type": "image/png"}],
            edit_prompt="test",
            aspect_ratio="1:1",
            model_name="grok-imagine-1.0-edit",
        )
    )

    assert result is None


def test_edit_openai_format_multi_image_uses_images_api(monkeypatch):
    """多参考图也应走 /images/edits，避免兼容图片接口丢失参考图。"""
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "OPENAI_IMAGE_API_MODE", "auto")
    service = GeminiImagenService()

    fake_image = b"fake-edited-image"
    captured = {}

    async def _fake_images_api(**kwargs):
        captured.update(kwargs)
        return fake_image

    monkeypatch.setattr(
        service, "_edit_image_openai_images_api_format", _fake_images_api
    )
    monkeypatch.setattr(
        service,
        "_edit_image_openai_chat_completions_format",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("多参考图应优先走 /images/edits")
        ),
    )

    reference_images = [
        {"data": b"img1", "mime_type": "image/png"},
        {"data": b"img2", "mime_type": "image/png"},
    ]
    result = asyncio.run(
        service._edit_image_openai_format(
            reference_images=reference_images,
            edit_prompt="merge these two images",
            aspect_ratio="1:1",
            model_name="grok-imagine-1.0-edit",
        )
    )

    assert result == fake_image
    assert captured["reference_images"] == reference_images


def test_openai_images_edits_uploads_first_nine_references_in_order(monkeypatch):
    """multipart /images/edits 应按原顺序上传前 9 张有效参考图，不能取最后几张。"""
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "STREAMING_ENABLED", False)
    service = GeminiImagenService()
    service._client = {
        "api_key": "test-key",
        "base_url": "http://localhost:8000/v1",
    }

    generated = b"edited-image"
    encoded = base64.b64encode(generated).decode("ascii")
    captured = {}

    class _FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return '{"data":[{"b64_json":"' + encoded + '"}]}'

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, data=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["form"] = data
            captured["timeout"] = timeout
            return _FakeResponse()

    monkeypatch.setattr(
        "src.chat.features.image_generation.services.gemini_imagen_service.aiohttp.ClientSession",
        lambda: _FakeSession(),
    )

    reference_images = [
        {"data": f"img-{index}".encode("ascii"), "mime_type": "image/png"}
        for index in range(11)
    ]

    result = asyncio.run(
        service._edit_image_openai_images_api_format(
            reference_images=reference_images,
            edit_prompt="把头像自然 P 到第 1 张底图里",
            aspect_ratio="1:1",
            model_name="grok-imagine-1.0-edit",
        )
    )

    image_fields = [
        value
        for headers, _field_headers, value in captured["form"]._fields
        if headers.get("name") == "image"
    ]

    assert result == generated
    assert captured["url"] == "http://localhost:8000/v1/images/edits"
    assert image_fields == [f"img-{index}".encode("ascii") for index in range(9)]


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


def test_parse_sse_payload_text_extracts_delta_images_data_url():
    service = GeminiImagenService()
    expected_bytes = b"stream-image-bytes"
    encoded = base64.b64encode(expected_bytes).decode("ascii")
    raw_text = (
        ': ping\n\n'
        'data: {"choices":[{"delta":{"role":"assistant","images":[{"index":0,"type":"image_url","image_url":{"url":"data:image/png;base64,' + encoded + '"}}]}}]}\n'
        'data: [DONE]\n'
    )

    parsed = asyncio.run(service._parse_sse_payload_text(raw_text))
    images = asyncio.run(
        service._extract_images_from_openai_response(
            parsed,
            response_format_override="base64",
        )
    )

    assert images == [expected_bytes]


def test_extract_images_from_openai_response_supports_message_images_data_url():
    service = GeminiImagenService()
    expected_bytes = b"message-image-bytes"
    encoded = base64.b64encode(expected_bytes).decode("ascii")

    images = asyncio.run(
        service._extract_images_from_openai_response(
            {
                "choices": [
                    {
                        "message": {
                            "images": [
                                {
                                    "index": 0,
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "data:image/png;base64," + encoded,
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            response_format_override="base64",
        )
    )

    assert images == [expected_bytes]
