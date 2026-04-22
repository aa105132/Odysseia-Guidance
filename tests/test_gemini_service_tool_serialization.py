# -*- coding: utf-8 -*-

import asyncio
import copy
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


def test_build_openai_tool_image_followup_message_for_avatar():
    message = GeminiService._build_openai_tool_image_followup_message(
        "get_user_avatar",
        {
            "mime_type": "image/png",
            "data": b"avatar-bytes",
            "tool_name": "get_user_avatar",
        },
    )

    assert message is not None
    assert message["role"] == "user"
    assert isinstance(message["content"], list)
    assert message["content"][0]["type"] == "text"
    assert "头像参考图" in message["content"][0]["text"]
    assert message["content"][1]["type"] == "image_url"
    assert message["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_build_openai_tool_image_followup_message_ignores_other_tools():
    message = GeminiService._build_openai_tool_image_followup_message(
        "render_newspaper_brief",
        {
            "mime_type": "image/png",
            "data": b"brief-bytes",
            "tool_name": "render_newspaper_brief",
        },
    )

    assert message is None


def test_reset_last_tool_outputs_clears_previous_tool_state():
    service = GeminiService()
    service.last_called_tools = ["summarize_channel", "render_newspaper_brief"]
    service.last_tool_image_data = {
        "mime_type": "image/png",
        "data": b"brief-bytes",
        "tool_name": "render_newspaper_brief",
    }
    service.last_tool_source_links = [("示例来源", "https://example.com")]

    service._reset_last_tool_outputs()

    assert service.last_called_tools == []
    assert service.last_tool_image_data is None
    assert service.last_tool_source_links == []


def test_execute_openai_tool_call_injects_avatar_reference_for_novelai():
    service = GeminiService()
    captured_kwargs = {}

    async def _fake_generate_image_novelai(**kwargs):
        captured_kwargs.update(kwargs)
        return {"ok": True}

    service.tool_map["generate_image_novelai"] = _fake_generate_image_novelai
    service.last_tool_image_data = {
        "mime_type": "image/png",
        "data": b"avatar-ref",
        "tool_name": "get_user_avatar",
    }

    result = asyncio.run(
        service._execute_openai_tool_call(
            tool_name="generate_image_novelai",
            tool_args={"prompt": "1girl, solo"},
            current_turn_tool_names=["get_user_avatar", "generate_image_novelai"],
        )
    )

    assert result == {"ok": True}
    assert captured_kwargs["reference_image_index"] == 1
    assert len(captured_kwargs["_prepared_reference_images"]) == 1
    assert captured_kwargs["_prepared_reference_images"][0]["data"] == b"avatar-ref"
    assert (
        captured_kwargs["_prepared_reference_images"][0]["source"]
        == "tool:get_user_avatar"
    )


def test_execute_openai_tool_call_keeps_explicit_reference_index():
    service = GeminiService()
    captured_kwargs = {}

    async def _fake_generate_image_novelai(**kwargs):
        captured_kwargs.update(kwargs)
        return {"ok": True}

    service.tool_map["generate_image_novelai"] = _fake_generate_image_novelai
    service.last_tool_image_data = {
        "mime_type": "image/png",
        "data": b"avatar-ref",
        "tool_name": "get_user_avatar",
    }

    result = asyncio.run(
        service._execute_openai_tool_call(
            tool_name="generate_image_novelai",
            tool_args={
                "prompt": "1girl, solo",
                "reference_image_index": 2,
            },
            current_turn_tool_names=["generate_image_novelai"],
        )
    )

    assert result == {"ok": True}
    assert captured_kwargs["reference_image_index"] == 2
    assert "_prepared_reference_images" not in captured_kwargs


def test_execute_openai_tool_call_autofills_self_avatar_for_edit_image():
    service = GeminiService()
    captured_kwargs = {}

    async def _fake_edit_image(**kwargs):
        captured_kwargs.update(kwargs)
        return {"ok": True}

    service.tool_map["edit_image"] = _fake_edit_image
    fake_message = types.SimpleNamespace(
        author=types.SimpleNamespace(id=123456789),
        guild=None,
    )

    result = asyncio.run(
        service._execute_openai_tool_call(
            tool_name="edit_image",
            tool_args={"edit_prompt": "按我的头像画成成熟人妻版本"},
            discord_message=fake_message,
        )
    )

    assert result == {"ok": True}
    assert captured_kwargs["avatar_user_id"] == "123456789"


def test_execute_openai_tool_call_autofills_self_avatar_for_novelai(monkeypatch):
    service = GeminiService()
    captured_kwargs = {}

    async def _fake_generate_image_novelai(**kwargs):
        captured_kwargs.update(kwargs)
        return {"ok": True}

    async def _fake_fetch_avatar_image(*args, **kwargs):
        return {
            "data": b"avatar-ref",
            "mime_type": "image/png",
            "filename": "avatar.png",
        }

    service.tool_map["generate_image_novelai"] = _fake_generate_image_novelai
    monkeypatch.setattr(
        sys.modules["src.chat.services.gemini_service"],
        "fetch_avatar_image",
        _fake_fetch_avatar_image,
    )
    fake_message = types.SimpleNamespace(
        author=types.SimpleNamespace(id=987654321),
        guild=None,
    )

    result = asyncio.run(
        service._execute_openai_tool_call(
            tool_name="generate_image_novelai",
            tool_args={"prompt": "按我的头像画成熟人妻版本"},
            discord_message=fake_message,
        )
    )

    assert result == {"ok": True}
    assert captured_kwargs["reference_image_index"] == 1
    assert captured_kwargs["_prepared_reference_images"][0]["data"] == b"avatar-ref"
    assert captured_kwargs["_prepared_reference_images"][0]["source"] == "auto:self_avatar"


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
    def __init__(self, body, headers=None):
        self.status = 200
        self._body = body
        self.headers = headers or {}

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


def test_openai_compat_request_auto_parses_sse_text_chunks(monkeypatch):
    service = GeminiService()
    fake_session = _FakeClientSession(
        [
            _SuccessResponse(
                (
                    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk",'
                    '"created":1,"model":"grok-4.20-beta","choices":[{"index":0,'
                    '"delta":{"role":"assistant","content":"你好"},"finish_reason":null}]}\n\n'
                    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk",'
                    '"created":1,"model":"grok-4.20-beta","choices":[{"index":0,'
                    '"delta":{"content":"世界"},"finish_reason":"stop"}],'
                    '"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}\n\n'
                    "data: [DONE]\n"
                ),
                headers={"Content-Type": "text/event-stream"},
            )
        ]
    )

    monkeypatch.setattr(
        sys.modules["src.chat.services.gemini_service"].aiohttp,
        "ClientSession",
        lambda: fake_session,
    )

    result = asyncio.run(
        service._post_openai_chat_completion_with_fallback(
            api_url="http://example.com/v1/chat/completions",
            headers={},
            payload={"model": "test", "messages": []},
            timeout_seconds=30,
            disabled_payload_fields=set(),
            log_prefix="OpenAI compat SSE parse test",
        )
    )

    assert result["choices"][0]["message"]["role"] == "assistant"
    assert result["choices"][0]["message"]["content"] == "你好世界"
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"]["completion_tokens"] == 2


def test_openai_compat_request_auto_parses_sse_tool_calls(monkeypatch):
    service = GeminiService()
    fake_session = _FakeClientSession(
        [
            _SuccessResponse(
                (
                    'data: {"id":"chatcmpl-2","object":"chat.completion.chunk",'
                    '"created":2,"model":"grok-4.20-beta","choices":[{"index":0,'
                    '"delta":{"role":"assistant","tool_calls":[{"index":0,'
                    '"id":"call_1","type":"function","function":{"name":"web_search",'
                    '"arguments":"{\\"q\\":"}}]},"finish_reason":null}]}\n\n'
                    'data: {"id":"chatcmpl-2","object":"chat.completion.chunk",'
                    '"created":2,"model":"grok-4.20-beta","choices":[{"index":0,'
                    '"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"hello\\"}"}}],'
                    '"content":""},"finish_reason":"tool_calls"}]}\n\n'
                    "data: [DONE]\n"
                ),
                headers={"Content-Type": "text/event-stream"},
            )
        ]
    )

    monkeypatch.setattr(
        sys.modules["src.chat.services.gemini_service"].aiohttp,
        "ClientSession",
        lambda: fake_session,
    )

    result = asyncio.run(
        service._post_openai_chat_completion_with_fallback(
            api_url="http://example.com/v1/chat/completions",
            headers={},
            payload={"model": "test", "messages": []},
            timeout_seconds=30,
            disabled_payload_fields=set(),
            log_prefix="OpenAI compat SSE tool-call parse test",
        )
    )

    tool_call = result["choices"][0]["message"]["tool_calls"][0]
    assert tool_call["id"] == "call_1"
    assert tool_call["function"]["name"] == "web_search"
    assert tool_call["function"]["arguments"] == '{"q":"hello"}'
    assert result["choices"][0]["finish_reason"] == "tool_calls"


def test_openai_tool_loop_skips_duplicate_web_fetch(monkeypatch):
    service = GeminiService()
    captured_payloads = []
    executed_tool_calls = []
    responses = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "web_fetch",
                                        "arguments": '{"url":"https://example.com/repo"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_2",
                                    "function": {
                                        "name": "web_fetch",
                                        "arguments": '{"url":"https://example.com/repo"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": "这是最终总结",
                        }
                    }
                ]
            },
        ]
    )

    async def _fake_load_context(_user_id):
        return None

    async def _fake_post_openai_chat_completion_with_fallback(**kwargs):
        captured_payloads.append(copy.deepcopy(kwargs["payload"]))
        return next(responses)

    async def _fake_execute_openai_tool_call(**kwargs):
        executed_tool_calls.append(copy.deepcopy(kwargs))
        return (
            "[网页内容获取结果 - URL: https://example.com/repo]\n\n"
            "AstrBot 是一个用于社区机器人的项目仓库。"
        )

    async def _fake_post_process_response(raw_response, user_id, guild_id):
        return raw_response

    monkeypatch.setattr(service, "_load_novelai_preset_context", _fake_load_context)
    monkeypatch.setattr(service, "_load_comfyui_choice_context", _fake_load_context)
    monkeypatch.setattr(
        service,
        "_convert_tools_to_openai_format",
        lambda: [{"type": "function", "function": {"name": "web_fetch"}}],
    )
    monkeypatch.setattr(
        service,
        "_post_openai_chat_completion_with_fallback",
        _fake_post_openai_chat_completion_with_fallback,
    )
    monkeypatch.setattr(
        service, "_execute_openai_tool_call", _fake_execute_openai_tool_call
    )
    monkeypatch.setattr(service, "_post_process_response", _fake_post_process_response)
    monkeypatch.setattr(
        sys.modules["src.chat.services.gemini_service"].prompt_service,
        "build_chat_prompt",
        lambda **kwargs: [{"role": "user", "parts": [kwargs["message"]]}],
    )

    result = asyncio.run(
        service._generate_with_openai_compatible(
            user_id=1,
            guild_id=1,
            message="帮我看看这个链接",
            model_name="grok-4.20-beta",
            api_url="https://example.com/v1",
            api_key="test-key",
        )
    )

    assert result == "这是最终总结"
    assert len(executed_tool_calls) == 1
    assert executed_tool_calls[0]["tool_name"] == "web_fetch"
    assert len(captured_payloads) == 3
    assert captured_payloads[2]["messages"][-1]["role"] == "tool"
    assert "[web_fetch 已跳过]" in captured_payloads[2]["messages"][-1]["content"]


def test_openai_tool_loop_keeps_tool_responses_contiguous_before_avatar_followup(
    monkeypatch,
):
    service = GeminiService()
    captured_payloads = []

    responses = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "get_user_avatar",
                                        "arguments": "{}",
                                    },
                                },
                                {
                                    "id": "call_2",
                                    "function": {
                                        "name": "get_user_profile",
                                        "arguments": '{"queries":["display_name"],"user_id":"1"}',
                                    },
                                },
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": "这是最终回答",
                        }
                    }
                ]
            },
        ]
    )

    async def _fake_load_context(_user_id):
        return None

    async def _fake_post_openai_chat_completion_with_fallback(**kwargs):
        captured_payloads.append(copy.deepcopy(kwargs["payload"]))
        return next(responses)

    async def _fake_execute_openai_tool_call(**kwargs):
        if kwargs["tool_name"] == "get_user_avatar":
            return {
                "image_data": {
                    "data": b"avatar-bytes",
                    "mime_type": "image/png",
                }
            }
        return {"profile": {"display_name": {"value": "测试用户"}}}

    async def _fake_post_process_response(raw_response, user_id, guild_id):
        return raw_response

    monkeypatch.setattr(service, "_load_novelai_preset_context", _fake_load_context)
    monkeypatch.setattr(service, "_load_comfyui_choice_context", _fake_load_context)
    monkeypatch.setattr(
        service,
        "_convert_tools_to_openai_format",
        lambda *_args, **_kwargs: [
            {"type": "function", "function": {"name": "get_user_avatar"}},
            {"type": "function", "function": {"name": "get_user_profile"}},
        ],
    )
    monkeypatch.setattr(
        service,
        "_post_openai_chat_completion_with_fallback",
        _fake_post_openai_chat_completion_with_fallback,
    )
    monkeypatch.setattr(
        service, "_execute_openai_tool_call", _fake_execute_openai_tool_call
    )
    monkeypatch.setattr(service, "_post_process_response", _fake_post_process_response)
    monkeypatch.setattr(
        sys.modules["src.chat.services.gemini_service"].prompt_service,
        "build_chat_prompt",
        lambda **kwargs: [{"role": "user", "parts": [kwargs["message"]]}],
    )

    result = asyncio.run(
        service._generate_with_openai_compatible(
            user_id=1,
            guild_id=1,
            message="请参考我的头像继续处理",
            model_name="grok-4.20-beta",
            api_url="https://example.com/v1",
            api_key="test-key",
        )
    )

    assert result == "这是最终回答"
    assert len(captured_payloads) == 2

    second_messages = captured_payloads[1]["messages"]
    assistant_index = next(
        idx
        for idx, item in enumerate(second_messages)
        if item.get("role") == "assistant" and item.get("tool_calls")
    )
    assert [
        second_messages[assistant_index + 1]["role"],
        second_messages[assistant_index + 2]["role"],
        second_messages[assistant_index + 3]["role"],
    ] == ["tool", "tool", "user"]
    assert second_messages[assistant_index + 1]["tool_call_id"] == "call_1"
    assert second_messages[assistant_index + 2]["tool_call_id"] == "call_2"
