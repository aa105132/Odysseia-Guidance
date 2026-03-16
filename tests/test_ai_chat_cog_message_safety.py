# -*- coding: utf-8 -*-

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _ensure_module(module_name: str, **attrs):
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
discord_module.HTTPException = _DummyHTTPException
discord_module.Forbidden = _DummyForbidden
discord_module.TextChannel = type("TextChannel", (), {})
discord_module.Thread = type("Thread", (), {})
discord_module.Message = type("Message", (), {})
discord_module.File = type("File", (), {})
discord_module.abc = types.SimpleNamespace(User=object, GuildChannel=object)
discord_module.errors = types.SimpleNamespace(HTTPException=_DummyHTTPException)
sys.modules["discord"] = discord_module

discord_ext_module = types.ModuleType("discord.ext")
discord_commands_module = types.ModuleType("discord.ext.commands")
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
_ensure_module(
    "src.chat.config.chat_config",
    CHAT_ENABLED=True,
    MESSAGE_SETTINGS={"DM_THRESHOLD": 1800, "NEWSPAPER_BRIEF_THRESHOLD": 250},
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


def test_should_send_newspaper_brief_only_for_channel_summary():
    cog = AIChatCog(bot=MagicMock())

    assert cog._should_send_newspaper_brief([]) is False
    assert cog._should_send_newspaper_brief(["web_search"]) is False
    assert cog._should_send_newspaper_brief(["render_newspaper_brief"]) is False
    assert cog._should_send_newspaper_brief(["summarize_channel"]) is True
    assert (
        cog._should_send_newspaper_brief(
            ["web_search", "summarize_channel", "render_newspaper_brief"]
        )
        is True
    )
