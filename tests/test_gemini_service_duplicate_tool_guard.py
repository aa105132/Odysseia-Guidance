# -*- coding: utf-8 -*-

import asyncio
import copy
import os
import sys
import types
from unittest.mock import MagicMock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("GEMINI_API_KEYS", "test-key")

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

fake_pil_image_module = types.ModuleType("PIL.Image")
fake_pil_image_module.Image = object
fake_pil_package = types.ModuleType("PIL")
fake_pil_package.Image = fake_pil_image_module
sys.modules.setdefault("PIL", fake_pil_package)
sys.modules.setdefault("PIL.Image", fake_pil_image_module)

sys.modules.setdefault("src.chat.services.context_service", MagicMock())
sys.modules.setdefault(
    "src.chat.features.affection.service.affection_service", MagicMock()
)
fake_prompt_service_module = types.ModuleType("src.chat.services.prompt_service")
fake_prompt_service_module.prompt_service = MagicMock()
sys.modules.setdefault("src.chat.services.prompt_service", fake_prompt_service_module)
sys.modules.setdefault(
    "src.chat.features.tools.services.tool_service",
    MagicMock(ToolService=MagicMock()),
)
sys.modules.setdefault(
    "src.chat.features.tools.utils.discord_image_utils",
    MagicMock(fetch_avatar_image=MagicMock()),
)
sys.modules.setdefault(
    "src.chat.utils.image_utils",
    MagicMock(
        sanitize_image=MagicMock(side_effect=lambda image: image),
        extract_image_frames_for_ai=MagicMock(return_value=([], {})),
    ),
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
import src.chat.services.gemini_service as gemini_module

# gemini_service 已拿到测试用 prompt_service 依赖，及时移除 stub，
# 避免污染其他测试对真实 PromptService 的导入。
sys.modules.pop("src.chat.services.prompt_service", None)


def test_openai_duplicate_tool_turn_forces_text_response(monkeypatch):
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
                                        "name": "get_user_profile",
                                        "arguments": '{"queries":["display_name"],"user_id":"1"}',
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
                                        "name": "get_user_profile",
                                        "arguments": '{"queries":["display_name"],"user_id":"1"}',
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
                            "content": "这是最终名片回答",
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
        return {"profile": {"display_name": {"value": "测试用户"}}}

    async def _fake_post_process_response(raw_response, user_id, guild_id):
        return raw_response

    monkeypatch.setattr(service, "_load_novelai_preset_context", _fake_load_context)
    monkeypatch.setattr(service, "_load_comfyui_choice_context", _fake_load_context)
    monkeypatch.setattr(
        service,
        "_convert_tools_to_openai_format",
        lambda *_args, **_kwargs: [
            {"type": "function", "function": {"name": "get_user_profile"}}
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
        gemini_module.prompt_service,
        "build_chat_prompt",
        lambda **kwargs: [{"role": "user", "parts": [kwargs["message"]]}],
    )

    result = asyncio.run(
        service._generate_with_openai_compatible(
            user_id=1,
            guild_id=1,
            message="帮我看看这个人的名片",
            model_name="grok-4.20-beta",
            api_url="https://example.com/v1",
            api_key="test-key",
        )
    )

    assert result == "这是最终名片回答"
    assert len(executed_tool_calls) == 1
    assert executed_tool_calls[0]["tool_name"] == "get_user_profile"
    assert len(captured_payloads) == 3
    assert "tools" in captured_payloads[0]
    assert "tools" in captured_payloads[1]
    assert "tools" not in captured_payloads[2]
    assert captured_payloads[2]["messages"][-1]["role"] == "tool"
    assert "[get_user_profile 已跳过]" in captured_payloads[2]["messages"][-1][
        "content"
    ]
