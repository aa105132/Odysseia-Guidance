import asyncio
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.chat.config import chat_config
from src.chat.features.tools.functions.generate_video import generate_video
from src.chat.features.video_generation.services.video_service import (
    VideoGenerationService,
)


def test_generate_video_tool_signature_exposes_new_api_params():
    signature = inspect.signature(generate_video)

    assert signature.parameters["duration"].default == 6
    assert "size" in signature.parameters
    assert "quality" in signature.parameters
    assert "model" in signature.parameters
    assert "reference_image_url" in signature.parameters


def test_video_service_posts_to_v1_videos_with_expected_payload(monkeypatch):
    monkeypatch.setitem(chat_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(chat_config.VIDEO_GEN_CONFIG, "MODEL_NAME", "grok-imagine-1.0-video")
    monkeypatch.setitem(chat_config.VIDEO_GEN_CONFIG, "VIDEO_FORMAT", "url")
    monkeypatch.setitem(chat_config.VIDEO_GEN_CONFIG, "MAX_DURATION", 30)
    monkeypatch.setitem(chat_config.VIDEO_GEN_CONFIG, "DEFAULT_SIZE", "1280x720")
    monkeypatch.setitem(chat_config.VIDEO_GEN_CONFIG, "DEFAULT_QUALITY", "high")

    captured = {}

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {
                "id": "video_123",
                "data": [{"url": "https://example.com/video.mp4"}],
            }

        async def text(self):
            return ""

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return _FakeResponse()

    monkeypatch.setattr(
        "src.chat.features.video_generation.services.video_service.aiohttp.ClientSession",
        lambda: _FakeSession(),
    )

    service = VideoGenerationService()
    service._client = {
        "api_key": "test-key",
        "base_url": "http://localhost:8000",
    }

    result = asyncio.run(
        service.generate_video(
            prompt="霓虹雨夜街头，慢镜头追拍",
            duration=18,
            image_data=b"fake-image",
            image_mime_type="image/png",
            size="1792x1024",
            quality="high",
        )
    )

    assert result is not None
    assert result.url == "https://example.com/video.mp4"
    assert result.post_id == "video_123"
    assert captured["url"] == "http://localhost:8000/v1/videos"
    assert captured["kwargs"]["json"]["model"] == "grok-imagine-1.0-video"
    assert captured["kwargs"]["json"]["prompt"] == "霓虹雨夜街头，慢镜头追拍"
    assert captured["kwargs"]["json"]["size"] == "1792x1024"
    assert captured["kwargs"]["json"]["seconds"] == 18
    assert captured["kwargs"]["json"]["quality"] == "high"
    assert captured["kwargs"]["json"]["stream"] is True
    assert captured["kwargs"]["json"]["image_reference"].startswith(
        "data:image/png;base64,"
    )
