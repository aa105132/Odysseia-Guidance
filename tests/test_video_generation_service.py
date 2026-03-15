# -*- coding: utf-8 -*-

import asyncio
import inspect
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath("."))

from src.chat.config import chat_config as app_config
import src.chat.features.video_generation.services.video_service as video_service_module
from src.chat.features.tools.functions.generate_video import (
    generate_video as generate_video_tool,
)
from src.chat.features.video_generation.services.video_service import (
    VideoGenerationService,
)


class _FakeResponse:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload, ensure_ascii=False)


class _FakePostContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeClientSession:
    def __init__(self, recorder, payload):
        self._recorder = recorder
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, headers=None, json=None, timeout=None):
        self._recorder["url"] = url
        self._recorder["headers"] = headers
        self._recorder["json"] = json
        return _FakePostContext(_FakeResponse(self._payload))


def test_generate_video_tool_exposes_new_video_params():
    signature = inspect.signature(generate_video_tool)
    assert "size" in signature.parameters
    assert "quality" in signature.parameters
    assert "model" in signature.parameters
    assert "reference_image_url" in signature.parameters
    assert signature.parameters["quality"].default == "high"


def test_extract_video_from_response_supports_data_array():
    service = VideoGenerationService()
    service._client = {"api_key": "test-key", "base_url": "http://localhost:8000"}

    result = service._extract_video_from_response(
        {
            "id": "vid_123",
            "data": [
                {
                    "url": "https://example.com/generated.mp4",
                }
            ],
        },
        "url",
    )

    assert result is not None
    assert result.url == "https://example.com/generated.mp4"
    assert result.post_id == "vid_123"


def test_generate_video_uses_v1_videos_payload(monkeypatch):
    recorder = {}
    payload = {
        "id": "vid_456",
        "data": [
            {
                "url": "https://example.com/generated.mp4",
            }
        ],
    }

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "API_KEY", "test-key")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "BASE_URL", "http://localhost:8000")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MODEL_NAME", "grok-imagine-1.0-video")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_SIZE", "1280x720")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_QUALITY", "high")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_DURATION", 30)
    monkeypatch.setattr(
        video_service_module.aiohttp,
        "ClientSession",
        lambda: _FakeClientSession(recorder, payload),
    )

    service = VideoGenerationService()
    service._client = {"api_key": "test-key", "base_url": "http://localhost:8000"}

    result = asyncio.run(
        service.generate_video(
            prompt="霓虹雨夜街头，慢镜头追拍",
            duration=18,
            image_data=b"fake-image-bytes",
            image_mime_type="image/png",
            size="1792x1024",
            quality="standard",
        )
    )

    assert result is not None
    assert result.url == "https://example.com/generated.mp4"
    assert recorder["url"] == "http://localhost:8000/v1/videos"
    assert recorder["headers"]["Authorization"] == "Bearer test-key"
    assert recorder["json"]["model"] == "grok-imagine-1.0-video"
    assert recorder["json"]["prompt"] == "霓虹雨夜街头，慢镜头追拍"
    assert recorder["json"]["size"] == "1792x1024"
    assert recorder["json"]["seconds"] == 18
    assert recorder["json"]["quality"] == "standard"
    assert recorder["json"]["image_reference"].startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    ("base_url", "expected_endpoint"),
    [
        ("http://localhost:8000", "http://localhost:8000/v1/videos"),
        ("http://localhost:8000/v1", "http://localhost:8000/v1/videos"),
        ("http://localhost:8000/v1/videos", "http://localhost:8000/v1/videos"),
        (
            "http://localhost:8000/v1/chat/completions",
            "http://localhost:8000/v1/videos",
        ),
        ("http://localhost:8000/v1/responses", "http://localhost:8000/v1/videos"),
        (
            "http://localhost:8000/v1/images/generations",
            "http://localhost:8000/v1/videos",
        ),
    ],
)
def test_resolve_videos_endpoint_forces_dedicated_v1_videos_route(
    base_url, expected_endpoint
):
    service = VideoGenerationService()
    service._client = {"api_key": "test-key", "base_url": base_url}

    assert service._resolve_videos_endpoint() == expected_endpoint
