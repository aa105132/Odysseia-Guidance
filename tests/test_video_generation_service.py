# -*- coding: utf-8 -*-

import asyncio
import inspect
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath("."))

from src.chat.config import chat_config as app_config
import src.chat.features.video_generation.services.video_service as video_service_module
from src.chat.features.tools.functions.generate_video import (
    _ensure_chinese_video_prompt,
    _normalize_reference_image_prompt_terms,
    generate_video as generate_video_tool,
)
from src.chat.features.video_generation.services.video_service import (
    VideoGenerationService,
    VideoResult,
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


class _FakeGetResponse:
    status = 200
    headers = {"Content-Type": "image/jpeg"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def read(self):
        return b"fake-downloaded-image"


class _FakeClientSessionWithGet(_FakeClientSession):
    def get(self, url, timeout=None):
        self._recorder["get_url"] = url
        self._recorder["get_timeout"] = timeout
        return _FakeGetResponse()




def test_image_to_video_prompt_uses_reference_image_terms_by_default():
    english_prompt = "Cinematic 3D realistic animation, vlog style, character smiles at camera"

    prompt = _ensure_chinese_video_prompt(
        english_prompt,
        is_image_to_video=True,
        duration=10,
    )

    assert prompt.startswith("基于参考图生成视频")
    assert "首帧" not in prompt


def test_image_to_video_prompt_rewrites_frame_terms_without_explicit_frame_request():
    prompt = _normalize_reference_image_prompt_terms(
        "基于首帧图像生成真人感Vlog视频：保持首帧构图和角色身份一致，不要传首尾帧。",
        allow_frame_terms=False,
    )

    assert "基于参考图生成" in prompt
    assert "保持参考图构图" in prompt
    assert "只把图片作为普通参考图" in prompt
    assert "不要传参考图" not in prompt
    assert "首帧" not in prompt
    assert "尾帧" not in prompt

def test_generate_video_tool_exposes_new_video_params():
    signature = inspect.signature(generate_video_tool)
    assert "size" in signature.parameters
    assert "quality" in signature.parameters
    assert "model" in signature.parameters
    assert "reference_image_url" in signature.parameters
    assert "avatar_username" in signature.parameters
    assert "avatar_usernames" in signature.parameters
    assert "reference_image_mode" in signature.parameters
    assert "max_reference_images" in signature.parameters
    assert "generate_audio" in signature.parameters
    assert "prepare_video_first_frame" in signature.parameters
    assert "video_first_frame_prompt" in signature.parameters
    assert signature.parameters["quality"].default == "high"
    assert signature.parameters["generate_audio"].default is True


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
    assert recorder["json"]["generate_audio"] is True
    assert recorder["json"]["stream"] is True
    assert recorder["json"]["image_reference"].startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    ("base_url", "expected_endpoint"),
    [
        ("http://localhost:8000", "http://localhost:8000/v1/videos"),
        ("http://localhost:8000/v1", "http://localhost:8000/v1/videos"),
        ("http://localhost:8000/v1/videos", "http://localhost:8000/v1/videos"),
        ("http://localhost:8000/v1/video/generate", "http://localhost:8000/v1/video/generate"),
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


class _FakeStreamContent:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeStreamResponse:
    status = 200
    headers = {"content-type": "text/event-stream"}

    def __init__(self, chunks):
        self.content = _FakeStreamContent(chunks)

    async def text(self):
        return ""


class _FakeStreamPostContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeStreamClientSession:
    def __init__(self, recorder, chunks):
        self._recorder = recorder
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, url, headers=None, json=None, timeout=None):
        self._recorder["url"] = url
        self._recorder["headers"] = headers
        self._recorder["json"] = json
        return _FakeStreamPostContext(_FakeStreamResponse(self._chunks))


def test_generate_video_reads_streaming_heartbeat_response(monkeypatch):
    recorder = {}
    chunks = [
        b'data: {"status":"processing"}\n\n',
        b': heartbeat\n\n',
        b'data: {"id":"vid_stream","data":[{"url":"https://example.com/stream.mp4"}]}\n\n',
        b'data: [DONE]\n\n',
    ]

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "API_KEY", "test-key")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "BASE_URL", "http://localhost:8000")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MODEL_NAME", "grok-imagine-1.0-video")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "VIDEO_FORMAT", "url")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_SIZE", "1280x720")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_QUALITY", "high")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_DURATION", 30)
    monkeypatch.setattr(
        video_service_module.aiohttp,
        "ClientSession",
        lambda: _FakeStreamClientSession(recorder, chunks),
    )

    service = VideoGenerationService()
    service._client = {"api_key": "test-key", "base_url": "http://localhost:8000"}

    result = asyncio.run(service.generate_video(prompt="一只猫转头", duration=6))

    assert result is not None
    assert result.url == "https://example.com/stream.mp4"
    assert result.post_id == "vid_stream"
    assert recorder["json"]["stream"] is True



def test_generate_video_uses_v1_video_generate_payload(monkeypatch):
    recorder = {}
    payload = {
        "id": "vid_generate",
        "data": [{"url": "https://example.com/generated-new.mp4"}],
    }

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "API_KEY", "test-key")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "BASE_URL", "http://localhost:8000/v1/video/generate")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MODEL_NAME", "your-video-model")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "VIDEO_FORMAT", "url")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_SIZE", "1280x720")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_QUALITY", "high")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_DURATION", 30)
    monkeypatch.setattr(
        video_service_module.aiohttp,
        "ClientSession",
        lambda: _FakeClientSession(recorder, payload),
    )

    service = VideoGenerationService()
    service._client = {
        "api_key": "test-key",
        "base_url": "http://localhost:8000/v1/video/generate",
    }

    result = asyncio.run(
        service.generate_video(
            prompt="一只猫在雪地里奔跑，电影感镜头",
            duration=10,
            size="1280x720",
            quality="high",
        )
    )

    assert result is not None
    assert result.url == "https://example.com/generated-new.mp4"
    assert recorder["url"] == "http://localhost:8000/v1/video/generate"
    assert recorder["json"] == {
        "model": "your-video-model",
        "mode": "text-to-video",
        "prompt": "一只猫在雪地里奔跑，电影感镜头",
        "duration": 10,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "format": "mp4",
        "generate_audio": True,
        "stream": True,
    }



def test_extract_video_from_response_supports_nested_outputs_dict():
    service = VideoGenerationService()
    result = service._extract_video_from_response(
        {
            "id": "outer_id",
            "status": "success",
            "data": {
                "id": "inner_id",
                "status": "succeed",
                "outputs": [
                    {
                        "type": "video",
                        "url": "https://artifact.anycap.cloud/a/art_MZIrhc4k00FO0jfFLNkB",
                        "mime_type": "video/mp4",
                    }
                ],
            },
        },
        "url",
    )

    assert result is not None
    assert result.url == "https://artifact.anycap.cloud/a/art_MZIrhc4k00FO0jfFLNkB"
    assert result.post_id == "outer_id"


def test_extract_video_from_response_supports_video_tag_src_without_extension():
    service = VideoGenerationService()
    html = '<video controls playsinline src="https://artifact.anycap.cloud/a/art_rRv4KNaqMqvdZyBi6LE8" style="max-width:100%;height:auto;"></video>'
    result = service._extract_video_from_response(
        {"choices": [{"message": {"content": html}}]},
        "url",
    )

    assert result is not None
    assert result.url == "https://artifact.anycap.cloud/a/art_rRv4KNaqMqvdZyBi6LE8"



def test_video_generate_endpoint_uses_images_without_first_frame_alias(monkeypatch):
    recorder = {}
    payload = {"id": "vid_i2v", "data": {"outputs": [{"url": "https://example.com/i2v"}]}}

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "API_KEY", "test-key")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "BASE_URL", "http://localhost:8000/v1/video/generate")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MODEL_NAME", "seedance-2-fast")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "I2V_MODEL_NAME", "seedance-2-fast-i2v")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "VIDEO_FORMAT", "url")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_SIZE", "1280x720")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_QUALITY", "high")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_DURATION", 30)
    monkeypatch.setattr(
        video_service_module.aiohttp,
        "ClientSession",
        lambda: _FakeClientSession(recorder, payload),
    )

    service = VideoGenerationService()
    service._client = {"api_key": "test-key", "base_url": "http://localhost:8000/v1/video/generate"}

    result = asyncio.run(
        service.generate_video(
            prompt="让图里的猫跑起来",
            duration=5,
            image_data=b"fake-image",
            image_mime_type="image/png",
        )
    )

    assert result is not None
    assert recorder["json"]["duration"] == 5
    assert recorder["json"]["model"] == "seedance-2-fast-i2v"
    assert recorder["json"]["mode"] == "image-to-video"
    assert recorder["json"]["generate_audio"] is True
    assert "image_reference" not in recorder["json"]
    assert "first_frame_resource_path" not in recorder["json"]
    assert "first_frame_url" not in recorder["json"]
    assert recorder["json"]["images"][0].startswith("data:image/png;base64,")


def test_video_generate_endpoint_sends_multiple_reference_images(monkeypatch):
    recorder = {}
    payload = {"id": "vid_multi_i2v", "data": {"outputs": [{"url": "https://example.com/i2v-multi"}]}}

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "API_KEY", "test-key")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "BASE_URL", "http://localhost:8000/v1/video/generate")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MODEL_NAME", "seedance-2-fast")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "I2V_MODEL_NAME", "seedance-2-fast-i2v")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "VIDEO_FORMAT", "url")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_SIZE", "1280x720")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_QUALITY", "high")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_DURATION", 30)
    monkeypatch.setattr(
        video_service_module.aiohttp,
        "ClientSession",
        lambda: _FakeClientSession(recorder, payload),
    )

    service = VideoGenerationService()
    service._client = {"api_key": "test-key", "base_url": "http://localhost:8000/v1/video/generate"}

    result = asyncio.run(
        service.generate_video(
            prompt="让两张参考图中的角色自然互动",
            duration=8,
            reference_images=[
                {"data": b"first-image", "mime_type": "image/png"},
                {"data": b"second-image", "mime_type": "image/jpeg"},
            ],
        )
    )

    assert result is not None
    assert recorder["json"]["mode"] == "image-to-video"
    assert recorder["json"]["generate_audio"] is True
    assert len(recorder["json"]["images"]) == 2
    assert "first_frame_resource_path" not in recorder["json"]
    assert "first_frame_url" not in recorder["json"]
    assert recorder["json"]["images"][0].startswith("data:image/png;base64,")
    assert recorder["json"]["images"][1].startswith("data:image/jpeg;base64,")


def test_video_generate_endpoint_allows_explicit_muted_i2v(monkeypatch):
    recorder = {}
    payload = {"id": "vid_muted_i2v", "data": {"outputs": [{"url": "https://example.com/i2v-muted"}]}}

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "API_KEY", "test-key")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "BASE_URL", "http://localhost:8000/v1/video/generate")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MODEL_NAME", "seedance-2-fast")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "I2V_MODEL_NAME", "seedance-2-fast-i2v")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "VIDEO_FORMAT", "url")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_SIZE", "1280x720")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_QUALITY", "high")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_DURATION", 30)
    monkeypatch.setattr(
        video_service_module.aiohttp,
        "ClientSession",
        lambda: _FakeClientSession(recorder, payload),
    )

    service = VideoGenerationService()
    service._client = {"api_key": "test-key", "base_url": "http://localhost:8000/v1/video/generate"}

    result = asyncio.run(
        service.generate_video(
            prompt="让图里的猫安静地眨眼",
            duration=5,
            image_data=b"fake-image",
            image_mime_type="image/png",
            generate_audio=False,
        )
    )

    assert result is not None
    assert recorder["json"]["mode"] == "image-to-video"
    assert recorder["json"]["generate_audio"] is False


def test_generate_video_tool_resolves_avatar_usernames_as_multi_references(monkeypatch):
    captured = {}

    class _FakeVideoService:
        def is_available(self):
            return True

        async def generate_video(self, **kwargs):
            captured.update(kwargs)
            return VideoResult(url="https://example.com/generated.mp4")

    class _FakeMessage:
        id = 1
        guild = object()
        reference = None

        async def add_reaction(self, emoji):
            return None

        async def remove_reaction(self, emoji, user):
            return None

    async def _fake_resolve_username_to_id(guild, username):
        return {"小明": "111", "小红": "222"}.get(username), None

    async def _fake_fetch_avatar_image(user_id, bot=None, guild=None):
        return {
            "data": f"avatar-{user_id}".encode("utf-8"),
            "mime_type": "image/png",
            "filename": f"avatar_{user_id}.png",
        }

    import src.chat.features.tools.utils.resolve_user as resolve_user_module
    import src.chat.features.tools.utils.discord_image_utils as discord_image_utils_module

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_NUMBER_OF_VIDEOS", 1)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_CONCURRENT_VIDEO_TASKS", 1)
    monkeypatch.setattr(video_service_module, "video_service", _FakeVideoService())
    monkeypatch.setattr(resolve_user_module, "resolve_username_to_id", _fake_resolve_username_to_id)
    monkeypatch.setattr(discord_image_utils_module, "fetch_avatar_image", _fake_fetch_avatar_image)
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.odysseia_coin.service.coin_service",
        SimpleNamespace(coin_service=SimpleNamespace()),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.tools.ui.regenerate_view",
        SimpleNamespace(RegenerateView=object),
    )

    result = asyncio.run(
        generate_video_tool(
            prompt="基于两位用户头像生成二次元动画视频：0-3秒两人看向镜头，3-6秒自然挥手。不要文字，不要水印。",
            duration=6,
            use_reference_image=True,
            avatar_usernames=["小明", "小红"],
            message=_FakeMessage(),
        )
    )

    assert result["success"] is True
    assert result["mode"] == "图生视频"
    assert captured["generate_audio"] is True
    assert captured["image_data"] == b"avatar-111"
    assert len(captured["reference_images"]) == 2
    assert captured["reference_images"][0]["data"] == b"avatar-111"
    assert captured["reference_images"][1]["data"] == b"avatar-222"




def test_generate_video_tool_prepares_first_frame_for_reference_only_request(monkeypatch):
    captured_video = {}
    captured_edit = {}

    class _FakeVideoService:
        def is_available(self):
            return True

        async def generate_video(self, **kwargs):
            captured_video.update(kwargs)
            return VideoResult(url="https://example.com/generated.mp4")

    class _FakeImagenService:
        def is_available(self):
            return True

        async def edit_image(self, **kwargs):
            captured_edit.update(kwargs)
            return b"prepared-first-frame"

    class _FakeAttachment:
        content_type = "image/png"
        filename = "reference-sheet.png"

        async def read(self):
            return b"reference-sheet"

    class _FakeMessage:
        id = 1
        guild = None
        reference = None
        content = "生成真人Vlog视频，使用参考图，不要传首尾帧"
        attachments = [_FakeAttachment()]

        async def add_reaction(self, emoji):
            return None

        async def remove_reaction(self, emoji, user):
            return None

    import src.chat.features.image_generation.services.gemini_imagen_service as imagen_service_module

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_NUMBER_OF_VIDEOS", 1)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_CONCURRENT_VIDEO_TASKS", 1)
    monkeypatch.setattr(video_service_module, "video_service", _FakeVideoService())
    monkeypatch.setattr(imagen_service_module, "gemini_imagen_service", _FakeImagenService())
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.odysseia_coin.service.coin_service",
        SimpleNamespace(coin_service=SimpleNamespace()),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.tools.ui.regenerate_view",
        SimpleNamespace(RegenerateView=object),
    )

    result = asyncio.run(
        generate_video_tool(
            prompt=(
                "基于参考图生成真人Vlog视频：保持人物服装和饰品一致，"
                "0-3秒在剧组后台微笑，3-10秒对镜头说话。"
            ),
            duration=10,
            use_reference_image=True,
            message=_FakeMessage(),
        )
    )

    assert result["success"] is True
    assert captured_edit["reference_images"][0]["data"] == b"reference-sheet"
    assert "不要复刻参考图的三视图" in captured_edit["edit_prompt"]
    assert "剧组后台" in captured_edit["edit_prompt"]
    assert captured_video["image_data"] == b"prepared-first-frame"
    assert captured_video["image_mime_type"] == "image/png"
    assert captured_video["reference_images"] == [
        {
            "data": b"prepared-first-frame",
            "mime_type": "image/png",
            "filename": "prepared_video_first_frame.png",
        }
    ]


def test_generate_video_tool_prepares_first_frame_when_prompt_mentions_reference_only(monkeypatch):
    captured_video = {}
    captured_edit = {}

    class _FakeVideoService:
        def is_available(self):
            return True

        async def generate_video(self, **kwargs):
            captured_video.update(kwargs)
            return VideoResult(url="https://example.com/generated.mp4")

    class _FakeImagenService:
        def is_available(self):
            return True

        async def edit_image(self, **kwargs):
            captured_edit.update(kwargs)
            return b"prepared-first-frame"

    class _FakeAttachment:
        content_type = "image/png"
        filename = "reference.png"

        async def read(self):
            return b"original-reference"

    class _FakeMessage:
        id = 1
        guild = None
        reference = None
        content = "@月月啊 重新生成视频"
        attachments = [_FakeAttachment()]

        async def add_reaction(self, emoji):
            return None

        async def remove_reaction(self, emoji, user):
            return None

    import src.chat.features.image_generation.services.gemini_imagen_service as imagen_service_module

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_NUMBER_OF_VIDEOS", 1)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_CONCURRENT_VIDEO_TASKS", 1)
    monkeypatch.setattr(video_service_module, "video_service", _FakeVideoService())
    monkeypatch.setattr(imagen_service_module, "gemini_imagen_service", _FakeImagenService())
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.odysseia_coin.service.coin_service",
        SimpleNamespace(coin_service=SimpleNamespace()),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.tools.ui.regenerate_view",
        SimpleNamespace(RegenerateView=object),
    )

    result = asyncio.run(
        generate_video_tool(
            prompt=(
                "基于参考图生成3D写实二次元风格视频：保持银发、狐狸兽耳和高铁车厢背景一致。"
                "0-2秒角色眨眼微笑，2-4秒微微侧头，4-6秒凝视镜头。"
            ),
            duration=6,
            use_reference_image=True,
            size="720x1280",
            video_first_frame_prompt="首帧画面：银发狐耳少女坐在高铁车窗旁，半身近景，阳光从窗外照进来，窗外青山绿树高速后退。",
            message=_FakeMessage(),
        )
    )

    assert result["success"] is True
    assert captured_edit["reference_images"][0]["data"] == b"original-reference"
    assert "适合后续图生视频的全新首帧图" in captured_edit["edit_prompt"]
    assert "银发狐耳少女坐在高铁车窗旁" in captured_edit["edit_prompt"]
    assert "高铁车厢背景" not in captured_edit["edit_prompt"]
    assert captured_video["image_data"] == b"prepared-first-frame"
    assert captured_video["reference_images"][0]["filename"] == "prepared_video_first_frame.png"


def test_generate_video_tool_direct_image_animation_keeps_original_reference(monkeypatch):
    captured_video = {}
    edit_called = False

    class _FakeVideoService:
        def is_available(self):
            return True

        async def generate_video(self, **kwargs):
            captured_video.update(kwargs)
            return VideoResult(url="https://example.com/generated.mp4")

    class _FakeImagenService:
        def is_available(self):
            return True

        async def edit_image(self, **kwargs):
            nonlocal edit_called
            edit_called = True
            return b"should-not-be-used"

    class _FakeAttachment:
        content_type = "image/png"
        filename = "direct.png"

        async def read(self):
            return b"direct-reference"

    class _FakeMessage:
        id = 1
        guild = None
        reference = None
        content = "把这张图动起来，保持原图构图"
        attachments = [_FakeAttachment()]

        async def add_reaction(self, emoji):
            return None

        async def remove_reaction(self, emoji, user):
            return None

    import src.chat.features.image_generation.services.gemini_imagen_service as imagen_service_module

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_NUMBER_OF_VIDEOS", 1)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_CONCURRENT_VIDEO_TASKS", 1)
    monkeypatch.setattr(video_service_module, "video_service", _FakeVideoService())
    monkeypatch.setattr(imagen_service_module, "gemini_imagen_service", _FakeImagenService())
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.odysseia_coin.service.coin_service",
        SimpleNamespace(coin_service=SimpleNamespace()),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.tools.ui.regenerate_view",
        SimpleNamespace(RegenerateView=object),
    )

    result = asyncio.run(
        generate_video_tool(
            prompt="基于参考图生成视频：保持参考图构图，只让角色眨眼并轻轻挥手。不要文字，不要水印。",
            duration=6,
            use_reference_image=True,
            prepare_video_first_frame=False,
            message=_FakeMessage(),
        )
    )

    assert result["success"] is True
    assert edit_called is False
    assert captured_video["image_data"] == b"direct-reference"
    assert captured_video["reference_images"][0]["filename"] == "direct.png"

def test_generate_video_tool_infers_duration_from_prompt_timeline(monkeypatch):
    captured = {}

    class _FakeVideoService:
        def is_available(self):
            return True

        async def generate_video(self, **kwargs):
            captured.update(kwargs)
            return VideoResult(url="https://example.com/generated.mp4")

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_NUMBER_OF_VIDEOS", 1)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_CONCURRENT_VIDEO_TASKS", 1)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_DURATION", 30)
    monkeypatch.setattr(video_service_module, "video_service", _FakeVideoService())
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.odysseia_coin.service.coin_service",
        SimpleNamespace(coin_service=SimpleNamespace()),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.tools.ui.regenerate_view",
        SimpleNamespace(RegenerateView=object),
    )

    result = asyncio.run(
        generate_video_tool(
            prompt=(
                "基于首帧图像生成震撼动画视频：0-3秒，角色周身浮现光效；"
                "3-7秒，角色完成进化并抬手；7-10秒，能量收束并稳定定格。"
                "不要文字，不要水印。"
            )
        )
    )

    assert result["success"] is True
    assert result["duration"] == 10
    assert captured["duration"] == 10


def test_generate_video_tool_displays_charged_currency_in_embed_footer(monkeypatch):
    sent_messages = []
    removed = []

    class _FakeVideoService:
        def is_available(self):
            return True

        async def generate_video(self, **kwargs):
            return VideoResult(text_response="ok", post_id="post_1")

    class _FakeCoinService:
        async def get_balance(self, user_id):
            return 999

        async def remove_coins(self, user_id, amount, reason):
            removed.append((user_id, amount, reason))
            return 999 - amount

    class _FakeChannel:
        async def send(self, **kwargs):
            sent_messages.append(kwargs)
            return SimpleNamespace(id=1)

    class _FakeRegenerateView:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "VIDEO_GENERATION_COST", 10)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_NUMBER_OF_VIDEOS", 1)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_CONCURRENT_VIDEO_TASKS", 1)
    monkeypatch.setitem(app_config.COIN_CONFIG, "CURRENCY_NAME", "灵石")
    monkeypatch.setattr(video_service_module, "video_service", _FakeVideoService())
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.odysseia_coin.service.coin_service",
        SimpleNamespace(coin_service=_FakeCoinService()),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.tools.ui.regenerate_view",
        SimpleNamespace(RegenerateView=_FakeRegenerateView),
    )

    result = asyncio.run(
        generate_video_tool(
            prompt="生成一只猫跑步的视频",
            duration=6,
            channel=_FakeChannel(),
            user_id="123456",
        )
    )

    assert result["success"] is True
    assert removed[0][0] == 123456
    assert removed[0][1] == 10
    assert sent_messages
    footer_text = sent_messages[0]["embed"].footer.text
    assert "消耗: 10 灵石" in footer_text


def test_generate_video_tool_does_not_fallback_to_message_image_when_avatar_missing(monkeypatch):
    class _FakeVideoService:
        def is_available(self):
            return True

        async def generate_video(self, **kwargs):
            raise AssertionError("头像失败时不应继续调用视频生成服务")

    class _FakeAttachment:
        content_type = "image/png"
        filename = "unrelated.png"

        async def read(self):
            raise AssertionError("头像失败时不应读取无关消息图片")

    fake_message = SimpleNamespace(
        id=1,
        guild=None,
        content="",
        stickers=[],
        attachments=[_FakeAttachment()],
        reference=None,
    )

    async def _fake_fetch_avatar_image(*args, **kwargs):
        return None

    import src.chat.features.tools.utils.discord_image_utils as discord_image_utils_module

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_NUMBER_OF_VIDEOS", 1)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_CONCURRENT_VIDEO_TASKS", 1)
    monkeypatch.setattr(video_service_module, "video_service", _FakeVideoService())
    monkeypatch.setattr(discord_image_utils_module, "fetch_avatar_image", _fake_fetch_avatar_image)
    monkeypatch.setitem(
        sys.modules,
        "src.chat.features.odysseia_coin.service.coin_service",
        SimpleNamespace(coin_service=SimpleNamespace()),
    )

    result = asyncio.run(
        generate_video_tool(
            prompt="基于指定用户头像生成 0-10 秒特摄变身视频",
            use_reference_image=True,
            avatar_user_id="1172726720378446080",
            message=fake_message,
        )
    )

    assert result["generation_failed"] is True
    assert result["reason"] == "avatar_image_not_found"


def test_video_generate_endpoint_downloads_reference_url_as_data_uri(monkeypatch):
    recorder = {}
    payload = {"id": "vid_i2v_url", "data": {"outputs": [{"url": "https://example.com/i2v-url"}]}}

    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "ENABLED", True)
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "API_KEY", "test-key")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "BASE_URL", "http://localhost:8000/v1/video/generate")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MODEL_NAME", "seedance-2-fast")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "I2V_MODEL_NAME", "")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "VIDEO_FORMAT", "url")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_SIZE", "1280x720")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "DEFAULT_QUALITY", "high")
    monkeypatch.setitem(app_config.VIDEO_GEN_CONFIG, "MAX_DURATION", 30)
    monkeypatch.setattr(
        video_service_module.aiohttp,
        "ClientSession",
        lambda: _FakeClientSessionWithGet(recorder, payload),
    )

    service = VideoGenerationService()
    service._client = {"api_key": "test-key", "base_url": "http://localhost:8000/v1/video/generate"}

    result = asyncio.run(
        service.generate_video(
            prompt="让图里的猫自然动起来",
            duration=5,
            reference_image_url="https://example.com/cat.jpg",
        )
    )

    assert result is not None
    assert recorder["get_url"] == "https://example.com/cat.jpg"
    assert recorder["json"]["mode"] == "image-to-video"
    assert recorder["json"]["generate_audio"] is True
    assert "first_frame_resource_path" not in recorder["json"]
    assert "first_frame_url" not in recorder["json"]
    assert len(recorder["json"]["images"]) == 1
    assert recorder["json"]["images"][0].startswith("data:image/jpeg;base64,")
