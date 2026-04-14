# -*- coding: utf-8 -*-

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_MISSING = object()
_ORIGINAL_MODULES = {}


def _remember_module(module_name: str):
    if module_name not in _ORIGINAL_MODULES:
        _ORIGINAL_MODULES[module_name] = sys.modules.get(module_name, _MISSING)


def _ensure_module(module_name: str, **attrs):
    _remember_module(module_name)
    module = sys.modules.get(module_name)
    if module is None:
        module = types.ModuleType(module_name)
        sys.modules[module_name] = module
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


class _DummyHTTPException(Exception):
    pass


class _DummyForbidden(Exception):
    pass


class _DummyCog:
    @classmethod
    def listener(cls):
        def decorator(func):
            return func

        return decorator


discord_module = types.ModuleType("discord")
_remember_module("discord")
discord_module.HTTPException = _DummyHTTPException
discord_module.Forbidden = _DummyForbidden
discord_module.TextChannel = type("TextChannel", (), {})
discord_module.Thread = type("Thread", (), {})
discord_module.Message = type("Message", (), {})
class _DummyFile:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


discord_module.File = _DummyFile
discord_module.abc = types.SimpleNamespace(User=object, GuildChannel=object)
discord_module.errors = types.SimpleNamespace(HTTPException=_DummyHTTPException)
sys.modules["discord"] = discord_module

discord_ext_module = types.ModuleType("discord.ext")
discord_commands_module = types.ModuleType("discord.ext.commands")
_remember_module("discord.ext")
_remember_module("discord.ext.commands")
discord_commands_module.Cog = _DummyCog
discord_commands_module.Bot = type("Bot", (), {})
discord_ext_module.commands = discord_commands_module
sys.modules["discord.ext"] = discord_ext_module
sys.modules["discord.ext.commands"] = discord_commands_module

_ensure_module("src.chat.services.chat_service", chat_service=MagicMock())
_ensure_module(
    "src.chat.services.message_processor", message_processor=MagicMock()
)
_ensure_module(
    "google.protobuf.json_format",
    MessageToDict=MagicMock(),
    ParseDict=MagicMock(),
)
google_package = _ensure_module("google")
setattr(google_package, "__path__", getattr(google_package, "__path__", []))
protobuf_package = _ensure_module("google.protobuf")
setattr(protobuf_package, "__path__", getattr(protobuf_package, "__path__", []))
_ensure_module(
    "src.chat.services.gemini_service",
    gemini_service=MagicMock(
        last_tool_image_data=None,
        last_called_tools=[],
        last_tool_source_links=[],
    ),
)
_ensure_module(
    "src.chat.features.tools.functions.summarize_channel",
    text_to_newspaper_brief_image=MagicMock(),
)
_ensure_module("src.chat.utils.database", chat_db_manager=MagicMock())
_ensure_module("src.database.database", AsyncSessionLocal=MagicMock())
_ensure_module(
    "src.database.services.dashboard_daily_stats_service",
    dashboard_daily_stats_service=MagicMock(),
)
_ensure_module(
    "src.chat.config.chat_config",
    CHAT_ENABLED=True,
    MESSAGE_SETTINGS={
        "DM_THRESHOLD": 1800,
        "NEWSPAPER_BRIEF_THRESHOLD": 250,
        "LONG_REPLY_IN_DM_ENABLED": False,
    },
)
config_package = _ensure_module("src.chat.config")
setattr(
    config_package,
    "chat_config",
    types.SimpleNamespace(UNRESTRICTED_CHANNEL_IDS=[]),
)
_ensure_module(
    "src.chat.features.odysseia_coin.service.coin_service",
    coin_service=MagicMock(),
)

from src.chat.cogs.ai_chat_cog import AIChatCog
from src.chat.cogs import ai_chat_cog as ai_chat_cog_module


def _restore_stubbed_modules():
    for module_name, original_module in _ORIGINAL_MODULES.items():
        if original_module is _MISSING:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = original_module


_restore_stubbed_modules()


def test_reply_text_safely_skips_blank_text():
    cog = AIChatCog(bot=MagicMock())

    message = MagicMock()
    message.id = 123
    message.author = MagicMock()
    message.author.id = 456
    message.channel = MagicMock()
    message.channel.id = 789
    message.reply = AsyncMock()
    message.channel.send = AsyncMock()

    result = asyncio.run(cog._reply_text_safely(message, "   \n\t  "))

    assert result == []
    message.reply.assert_not_called()
    message.channel.send.assert_not_called()


def test_reply_text_safely_records_channel_stats_by_chunk_count():
    cog = AIChatCog(bot=MagicMock())
    cog._record_dashboard_delivery_stats = AsyncMock()

    message = MagicMock()
    message.reply = AsyncMock(return_value=MagicMock())
    message.channel = MagicMock()
    message.channel.send = AsyncMock(return_value=MagicMock())

    result = asyncio.run(cog._reply_text_safely(message, "a" * 2001))

    assert len(result) == 2
    cog._record_dashboard_delivery_stats.assert_awaited_once_with(channel_messages=2)


def test_send_dm_text_safely_records_dm_stats():
    cog = AIChatCog(bot=MagicMock())
    cog._record_dashboard_delivery_stats = AsyncMock()

    user = MagicMock()
    user.send = AsyncMock()

    asyncio.run(cog._send_dm_text_safely(user, "引导语", "a" * 4001))

    assert user.send.await_count == 4
    cog._record_dashboard_delivery_stats.assert_awaited_once_with(dm_messages=4)


def test_should_send_newspaper_brief_only_for_channel_summary():
    cog = AIChatCog(bot=MagicMock())

    assert cog._should_send_newspaper_brief([]) is False
    assert cog._should_send_newspaper_brief(["web_search"]) is False
    assert cog._should_send_newspaper_brief(["render_newspaper_brief"]) is True
    assert cog._should_send_newspaper_brief(["summarize_channel"]) is True
    assert (
        cog._should_send_newspaper_brief(
            ["web_search", "summarize_channel", "render_newspaper_brief"]
        )
        is True
    )


def test_should_send_long_reply_via_dm_respects_toggle():
    cog = AIChatCog(bot=MagicMock())

    original_settings = dict(ai_chat_cog_module.MESSAGE_SETTINGS)
    try:
        ai_chat_cog_module.MESSAGE_SETTINGS["DM_THRESHOLD"] = 10
        ai_chat_cog_module.MESSAGE_SETTINGS["LONG_REPLY_IN_DM_ENABLED"] = False
        assert cog._should_send_long_reply_via_dm("这是一段明显超出阈值的回复内容") is False

        ai_chat_cog_module.MESSAGE_SETTINGS["LONG_REPLY_IN_DM_ENABLED"] = True
        assert cog._should_send_long_reply_via_dm("这是一段明显超出阈值的回复内容") is True
    finally:
        ai_chat_cog_module.MESSAGE_SETTINGS.clear()
        ai_chat_cog_module.MESSAGE_SETTINGS.update(original_settings)


def test_should_send_long_reply_via_dm_ignores_custom_emoji_length():
    cog = AIChatCog(bot=MagicMock())

    original_settings = dict(ai_chat_cog_module.MESSAGE_SETTINGS)
    try:
        ai_chat_cog_module.MESSAGE_SETTINGS["DM_THRESHOLD"] = 5
        ai_chat_cog_module.MESSAGE_SETTINGS["LONG_REPLY_IN_DM_ENABLED"] = True
        assert cog._should_send_long_reply_via_dm("<:wave:1234567890>abc") is False
        assert cog._should_send_long_reply_via_dm("<:wave:1234567890>abcdef") is True
    finally:
        ai_chat_cog_module.MESSAGE_SETTINGS.clear()
        ai_chat_cog_module.MESSAGE_SETTINGS.update(original_settings)


def test_send_newspaper_brief_reply_records_image_stats():
    cog = AIChatCog(bot=MagicMock())
    cog._record_dashboard_delivery_stats = AsyncMock()
    cog._reply_sources_below_image = AsyncMock()

    original_renderer = ai_chat_cog_module.text_to_newspaper_brief_image
    ai_chat_cog_module.text_to_newspaper_brief_image = MagicMock(return_value=b"image")
    try:
        message = MagicMock()
        message.reply = AsyncMock(return_value=MagicMock())

        result = asyncio.run(
            cog._send_newspaper_brief_reply(
                message=message,
                body_text="正文",
                source_text="来源",
                source_links=[("示例", "https://example.com")],
            )
        )

        assert result is True
        cog._record_dashboard_delivery_stats.assert_awaited_once_with(
            channel_messages=1,
            image_messages=1,
        )
        cog._reply_sources_below_image.assert_awaited_once()
    finally:
        ai_chat_cog_module.text_to_newspaper_brief_image = original_renderer


def test_send_newspaper_brief_with_full_text_sends_image_then_text():
    cog = AIChatCog(bot=MagicMock())
    cog._send_newspaper_brief_reply = AsyncMock(return_value=True)
    cog._reply_text_safely = AsyncMock(return_value=[MagicMock()])
    cog._suppress_link_previews = AsyncMock()

    message = MagicMock()

    result = asyncio.run(
        cog._send_newspaper_brief_with_full_text(
            message=message,
            response_text="完整文字\n信息来源：\n- 示例: https://example.com",
            source_text="信息来源：\n- 示例: https://example.com",
            source_links=[("示例", "https://example.com")],
            used_web_search=True,
            provided_image_data={"mime_type": "image/png", "data": b"img"},
        )
    )

    assert result is True
    cog._send_newspaper_brief_reply.assert_awaited_once()
    _, kwargs = cog._send_newspaper_brief_reply.await_args
    assert kwargs["send_sources"] is False
    assert kwargs["body_text"] == "完整文字\n信息来源：\n- 示例: https://example.com"
    cog._reply_text_safely.assert_awaited_once_with(
        message,
        "完整文字\n信息来源：\n- 示例: https://example.com",
        mention_author=False,
    )
    cog._suppress_link_previews.assert_awaited_once()
