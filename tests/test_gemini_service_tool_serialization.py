# -*- coding: utf-8 -*-

import asyncio
import os
import sys
import types
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("GEMINI_API_KEYS", "test-key")

# 避免导入 GeminiService 时拉起与本测试无关的复杂依赖
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
    "src.chat.features.affection.service.affection_service", MagicMock()
)
sys.modules.setdefault("src.chat.services.prompt_service", MagicMock())
sys.modules.setdefault(
    "src.chat.features.tools.services.tool_service",
    MagicMock(ToolService=MagicMock()),
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

from src.chat.services.gemini_service import GeminiService


def test_sanitize_tool_result_for_history_replaces_binary_bytes():
    tool_result = {
        "image_data": {
            "data": b"\x89PNG\r\n",
            "mime_type": "image/png",
        },
        "user_info": {
            "avatar_bytes": bytearray(b"abc"),
            "tags": {"x", "y"},
        },
        "hint": "头像已获取",
    }

    sanitized = GeminiService._sanitize_tool_result_for_history(tool_result)

    assert sanitized["image_data"]["data"] == "<binary-image-data>"
    assert sanitized["user_info"]["avatar_bytes"] == "<binary-bytes:3>"
    assert isinstance(sanitized["user_info"]["tags"], list)

    # 必须能安全写入 JSON 历史消息
    import json

    json.dumps(sanitized, ensure_ascii=False)


def test_extract_tool_image_payload_accepts_bytearray_and_memoryview():
    bytearray_result = {
        "image_data": {
            "data": bytearray(b"avatar"),
            "mime_type": "image/png",
        }
    }
    memoryview_result = {
        "image_data": {
            "data": memoryview(b"avatar-2"),
            "mime_type": "image/jpeg",
        }
    }

    extracted_bytearray = GeminiService._extract_tool_image_payload(
        bytearray_result, "get_user_avatar"
    )
    extracted_memoryview = GeminiService._extract_tool_image_payload(
        memoryview_result, "render_newspaper_brief"
    )

    assert extracted_bytearray == {
        "mime_type": "image/png",
        "data": b"avatar",
        "tool_name": "get_user_avatar",
    }
    assert extracted_memoryview == {
        "mime_type": "image/jpeg",
        "data": b"avatar-2",
        "tool_name": "render_newspaper_brief",
    }


class _FakeResponseContext:
    def __init__(self, event):
        self._event = event

    async def __aenter__(self):
        if isinstance(self._event, Exception):
            raise self._event
        return self._event

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeClientSession:
    def __init__(self, events):
        self._events = events
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        event = self._events[self.calls]
        self.calls += 1
        return _FakeResponseContext(event)


class _SuccessResponse:
    def __init__(self, body):
        self.status = 200
        self._body = body

    async def text(self):
        return self._body


def test_openai_compat_request_retries_after_connection_refused(monkeypatch):
    service = GeminiService()
    events = [
        ConnectionRefusedError(111, "connection refused"),
        ConnectionRefusedError(111, "connection refused"),
        _SuccessResponse('{"ok": true}'),
    ]
    fake_session = _FakeClientSession(events)

    monkeypatch.setitem(
        sys.modules["src.chat.services.gemini_service"].app_config.API_RETRY_CONFIG,
        "OPENAI_COMPAT_MAX_ATTEMPTS",
        3,
    )
    monkeypatch.setitem(
        sys.modules["src.chat.services.gemini_service"].app_config.API_RETRY_CONFIG,
        "OPENAI_COMPAT_RETRY_BASE_DELAY_SECONDS",
        0,
    )
    monkeypatch.setattr(
        sys.modules["src.chat.services.gemini_service"].aiohttp,
        "ClientSession",
        lambda: fake_session,
    )
    sleep_calls = []

    async def _fake_sleep(delay):
        sleep_calls.append(delay)

    monkeypatch.setattr(
        sys.modules["src.chat.services.gemini_service"].asyncio,
        "sleep",
        _fake_sleep,
    )

    result = asyncio.run(
        service._post_openai_chat_completion_with_fallback(
            api_url="http://example.com/v1/chat/completions",
            headers={},
            payload={"model": "test", "messages": []},
            timeout_seconds=30,
            disabled_payload_fields=set(),
            log_prefix="OpenAI compat retry test",
        )
    )

    assert result == {"ok": True}
    assert fake_session.calls == 3
    assert sleep_calls == [0.2, 0.4]


def test_openai_compat_request_failure_message_contains_attempts(monkeypatch):
    service = GeminiService()
    fake_session = _FakeClientSession(
        [ConnectionRefusedError(111, "connection refused")]
    )

    monkeypatch.setitem(
        sys.modules["src.chat.services.gemini_service"].app_config.API_RETRY_CONFIG,
        "OPENAI_COMPAT_MAX_ATTEMPTS",
        1,
    )
    monkeypatch.setattr(
        sys.modules["src.chat.services.gemini_service"].aiohttp,
        "ClientSession",
        lambda: fake_session,
    )

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            service._post_openai_chat_completion_with_fallback(
                api_url="http://example.com/v1/chat/completions",
                headers={},
                payload={"model": "test", "messages": []},
                timeout_seconds=30,
                disabled_payload_fields=set(),
                log_prefix="OpenAI compat retry test",
            )
        )

    assert "after 1/1 attempts" in str(exc_info.value)
