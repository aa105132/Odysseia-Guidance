# -*- coding: utf-8 -*-

from datetime import date
import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

mock_google_genai = MagicMock()
google_module = sys.modules.get("google")
if google_module is None:
    google_module = types.ModuleType("google")
    google_module.__path__ = []
    sys.modules["google"] = google_module
setattr(google_module, "genai", mock_google_genai)
sys.modules.setdefault("google.genai", mock_google_genai)
sys.modules.setdefault("google.genai.types", MagicMock())
sys.modules.setdefault(
    "src.chat.features.tools.services.user_tool_settings_service",
    MagicMock(user_tool_settings_service=MagicMock()),
)

discord_module = types.ModuleType("discord")


class _DummyView:
    def __init__(self, *args, **kwargs):
        self.children = []


class _DummyButton:
    def __init__(self, *args, **kwargs):
        self.label = kwargs.get("label")
        self.disabled = kwargs.get("disabled", False)


def _dummy_button(*args, **kwargs):
    def decorator(func):
        return func

    return decorator


discord_module.ui = types.SimpleNamespace(
    View=_DummyView,
    Button=_DummyButton,
    button=_dummy_button,
)
discord_module.ButtonStyle = types.SimpleNamespace(success=1)
discord_module.Embed = MagicMock()
discord_module.Color = types.SimpleNamespace(gold=lambda: MagicMock())
discord_module.Interaction = object
discord_module.TextChannel = object
discord_module.Thread = type("Thread", (), {})
discord_module.Client = object
discord_module.Message = object
discord_module.Forbidden = Exception
sys.modules["discord"] = discord_module

from src.chat.config.chat_config import SPRING_FESTIVAL_CONFIG
from src.chat.features.tools.functions.spring_festival_red_envelope import (
    spring_festival_red_envelope,
)
from src.chat.features.tools.services.tool_service import ToolService
from src.chat.features.tools.tool_availability import (
    filter_tool_declarations,
    is_spring_festival_in_date_window,
    is_spring_festival_tool_visible,
)


@pytest.fixture(autouse=True)
def restore_spring_festival_config():
    original_config = dict(SPRING_FESTIVAL_CONFIG)
    try:
        yield
    finally:
        SPRING_FESTIVAL_CONFIG.clear()
        SPRING_FESTIVAL_CONFIG.update(original_config)


def _make_tool(name: str):
    async def _tool():
        return {"ok": True}

    _tool.__name__ = name
    return _tool


def test_spring_festival_tool_hidden_when_disabled():
    SPRING_FESTIVAL_CONFIG["enabled"] = False
    SPRING_FESTIVAL_CONFIG["start_date"] = "2026-02-10"
    SPRING_FESTIVAL_CONFIG["end_date"] = "2026-02-20"

    visible = is_spring_festival_tool_visible(today=date(2026, 2, 15))

    assert visible is False


def test_spring_festival_tool_uses_configured_date_window():
    SPRING_FESTIVAL_CONFIG["enabled"] = True
    SPRING_FESTIVAL_CONFIG["start_date"] = "2026-02-10"
    SPRING_FESTIVAL_CONFIG["end_date"] = "2026-02-20"

    assert is_spring_festival_in_date_window(today=date(2026, 2, 15)) is True
    assert is_spring_festival_in_date_window(today=date(2026, 3, 1)) is False


def test_spring_festival_tool_uses_fallback_window_when_date_not_configured():
    SPRING_FESTIVAL_CONFIG["enabled"] = True
    SPRING_FESTIVAL_CONFIG["start_date"] = ""
    SPRING_FESTIVAL_CONFIG["end_date"] = ""

    assert is_spring_festival_in_date_window(today=date(2026, 1, 20)) is True
    assert is_spring_festival_in_date_window(today=date(2026, 3, 2)) is False


def test_filter_tool_declarations_hides_spring_festival_tool_when_out_of_season():
    SPRING_FESTIVAL_CONFIG["enabled"] = True
    SPRING_FESTIVAL_CONFIG["start_date"] = "2099-02-10"
    SPRING_FESTIVAL_CONFIG["end_date"] = "2099-02-20"

    visible_tools = filter_tool_declarations(
        [
            _make_tool("spring_festival_red_envelope"),
            _make_tool("get_user_avatar"),
        ]
    )

    assert [tool.__name__ for tool in visible_tools] == ["get_user_avatar"]


def test_tool_service_returns_filtered_tool_declarations():
    SPRING_FESTIVAL_CONFIG["enabled"] = True
    SPRING_FESTIVAL_CONFIG["start_date"] = "2099-02-10"
    SPRING_FESTIVAL_CONFIG["end_date"] = "2099-02-20"

    tool_service = ToolService(
        bot=None,
        tool_map={},
        tool_declarations=[
            _make_tool("spring_festival_red_envelope"),
            _make_tool("get_user_avatar"),
        ],
    )

    visible_tools = tool_service.get_visible_tool_declarations()

    assert [tool.__name__ for tool in visible_tools] == ["get_user_avatar"]


def test_spring_festival_red_envelope_returns_out_of_season_message():
    SPRING_FESTIVAL_CONFIG["enabled"] = True
    SPRING_FESTIVAL_CONFIG["start_date"] = "2099-02-10"
    SPRING_FESTIVAL_CONFIG["end_date"] = "2099-02-20"

    result = asyncio.run(
        spring_festival_red_envelope(
            blessing_text="新春快乐",
            user_id="123",
            bot=AsyncMock(),
        )
    )

    assert result["success"] is False
    assert result["message"] == "当前不在新春红包活动期间。"
