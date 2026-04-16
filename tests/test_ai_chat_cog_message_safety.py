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


class _DummyTypingContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


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
_ensure_module(
    "src.chat.utils.database",
    chat_db_manager=MagicMock(),
    get_beijing_today_str=lambda: "2026-04-16",
)
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


def _build_message(*, author_bot: bool, content: str = "@月月 你好", mentioned: bool = True):
    bot_user = MagicMock()
    bot_user.id = 114514
    bot_user.display_name = "月月"

    bot = MagicMock()
    bot.user = bot_user

    message = MagicMock()
    message.id = 123
    message.content = content
    message.author = MagicMock()
    message.author.bot = author_bot
    message.author.id = 456 if not author_bot else 789
    message.author.display_name = "测试作者"
    message.guild = MagicMock()
    message.guild.id = 1
    message.guild.name = "测试服务器"
    message.mentions = [bot_user] if mentioned else []
    message.reply = AsyncMock(return_value=MagicMock())

    channel = MagicMock()
    channel.id = 2
    channel.name = "测试频道"
    channel.typing = lambda: _DummyTypingContext()
    channel.send = AsyncMock(return_value=MagicMock())
    message.channel = channel

    return bot, message


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



def test_on_message_allows_mentioned_bot_message_and_records_daily_usage():
    bot, message = _build_message(author_bot=True)
    cog = AIChatCog(bot=bot)
    cog.handle_chat_message = AsyncMock(return_value="bot reply")
    cog._reply_text_safely = AsyncMock(return_value=[MagicMock()])
    cog._reply_sources_below_image = AsyncMock()
    cog._suppress_link_previews = AsyncMock()

    ai_chat_cog_module.message_processor.process_message = AsyncMock(
        return_value={"user_content": "hi", "replied_content": "", "image_data_list": []}
    )
    ai_chat_cog_module.chat_service.should_process_message = AsyncMock(return_value=True)
    ai_chat_cog_module.chat_db_manager.is_user_globally_blacklisted = AsyncMock(
        return_value=False
    )
    ai_chat_cog_module.chat_db_manager.is_bot_reply_paused = AsyncMock(return_value=False)
    ai_chat_cog_module.chat_db_manager.get_bot_reply_daily_count = AsyncMock(
        return_value=0
    )
    ai_chat_cog_module.chat_db_manager.increment_bot_reply_daily_count = AsyncMock()

    asyncio.run(cog.on_message(message))

    cog.handle_chat_message.assert_awaited_once()
    cog._reply_text_safely.assert_awaited_once_with(
        message, "bot reply", mention_author=True
    )
    ai_chat_cog_module.chat_db_manager.increment_bot_reply_daily_count.assert_awaited_once()



def test_on_message_skips_bot_message_when_daily_limit_reached():
    bot, message = _build_message(author_bot=True)
    cog = AIChatCog(bot=bot)
    cog.handle_chat_message = AsyncMock()
    cog._reply_text_safely = AsyncMock()

    ai_chat_cog_module.message_processor.process_message = AsyncMock(
        return_value={"user_content": "hi", "replied_content": "", "image_data_list": []}
    )
    ai_chat_cog_module.chat_db_manager.is_bot_reply_paused = AsyncMock(return_value=False)
    ai_chat_cog_module.chat_db_manager.get_bot_reply_daily_count = AsyncMock(
        return_value=200
    )

    asyncio.run(cog.on_message(message))

    cog.handle_chat_message.assert_not_called()
    cog._reply_text_safely.assert_not_called()



def test_on_message_skips_bot_message_when_scope_is_paused():
    bot, message = _build_message(author_bot=True)
    cog = AIChatCog(bot=bot)
    cog.handle_chat_message = AsyncMock()
    cog._reply_text_safely = AsyncMock()

    ai_chat_cog_module.message_processor.process_message = AsyncMock(
        return_value={"user_content": "hi", "replied_content": "", "image_data_list": []}
    )
    ai_chat_cog_module.chat_db_manager.is_bot_reply_paused = AsyncMock(return_value=True)

    asyncio.run(cog.on_message(message))

    cog.handle_chat_message.assert_not_called()
    cog._reply_text_safely.assert_not_called()



def test_on_message_stop_bot_reply_command_pauses_current_scope():
    bot, message = _build_message(author_bot=False, content="@月月 停止回复bot")
    cog = AIChatCog(bot=bot)
    cog.handle_chat_message = AsyncMock()
    cog._reply_text_safely = AsyncMock(return_value=[])

    ai_chat_cog_module.message_processor.process_message = AsyncMock(
        return_value={
            "user_content": "停止回复bot",
            "replied_content": "",
            "image_data_list": [],
        }
    )
    ai_chat_cog_module.chat_db_manager.set_bot_reply_paused = AsyncMock()

    asyncio.run(cog.on_message(message))

    ai_chat_cog_module.chat_db_manager.set_bot_reply_paused.assert_awaited_once_with(
        message.channel.id, True
    )
    cog._reply_text_safely.assert_awaited_once()
    cog.handle_chat_message.assert_not_called()



def test_on_message_resume_bot_reply_command_unpauses_current_scope():
    bot, message = _build_message(author_bot=False, content="@月月 恢复回复bot")
    cog = AIChatCog(bot=bot)
    cog.handle_chat_message = AsyncMock()
    cog._reply_text_safely = AsyncMock(return_value=[])

    ai_chat_cog_module.message_processor.process_message = AsyncMock(
        return_value={
            "user_content": "恢复回复bot",
            "replied_content": "",
            "image_data_list": [],
        }
    )
    ai_chat_cog_module.chat_db_manager.set_bot_reply_paused = AsyncMock()

    asyncio.run(cog.on_message(message))

    ai_chat_cog_module.chat_db_manager.set_bot_reply_paused.assert_awaited_once_with(
        message.channel.id, False
    )
    cog._reply_text_safely.assert_awaited_once()
    cog.handle_chat_message.assert_not_called()


def test_on_message_records_bot_usage_after_channel_summary_brief():
    bot, message = _build_message(author_bot=True)
    cog = AIChatCog(bot=bot)
    cog.handle_chat_message = AsyncMock(return_value="频道总结内容")
    cog._send_newspaper_brief_with_full_text = AsyncMock(return_value=True)
    cog._record_bot_reply_usage_if_needed = AsyncMock()

    ai_chat_cog_module.gemini_service.last_called_tools = ["summarize_channel"]
    ai_chat_cog_module.gemini_service.last_tool_image_data = None
    ai_chat_cog_module.gemini_service.last_tool_source_links = []
    ai_chat_cog_module.message_processor.process_message = AsyncMock(
        return_value={"user_content": "hi", "replied_content": "", "image_data_list": []}
    )
    ai_chat_cog_module.chat_service.should_process_message = AsyncMock(return_value=True)
    ai_chat_cog_module.chat_db_manager.is_user_globally_blacklisted = AsyncMock(
        return_value=False
    )
    ai_chat_cog_module.chat_db_manager.is_bot_reply_paused = AsyncMock(return_value=False)
    ai_chat_cog_module.chat_db_manager.get_bot_reply_daily_count = AsyncMock(
        return_value=0
    )

    asyncio.run(cog.on_message(message))

    cog._send_newspaper_brief_with_full_text.assert_awaited_once()
    cog._record_bot_reply_usage_if_needed.assert_awaited_once_with(message)


def test_on_message_records_bot_usage_after_direct_dm_send():
    bot, message = _build_message(author_bot=True)
    cog = AIChatCog(bot=bot)
    cog.handle_chat_message = AsyncMock(return_value="这是一段会走私信分支的回复")
    cog._record_dashboard_delivery_stats = AsyncMock()
    cog._record_bot_reply_usage_if_needed = AsyncMock()
    cog._should_send_long_reply_via_dm = MagicMock(return_value=True)
    message.author.send = AsyncMock()

    ai_chat_cog_module.gemini_service.last_called_tools = []
    ai_chat_cog_module.gemini_service.last_tool_image_data = None
    ai_chat_cog_module.gemini_service.last_tool_source_links = []
    ai_chat_cog_module.message_processor.process_message = AsyncMock(
        return_value={"user_content": "hi", "replied_content": "", "image_data_list": []}
    )
    ai_chat_cog_module.chat_service.should_process_message = AsyncMock(return_value=True)
    ai_chat_cog_module.chat_db_manager.is_user_globally_blacklisted = AsyncMock(
        return_value=False
    )
    ai_chat_cog_module.chat_db_manager.is_bot_reply_paused = AsyncMock(return_value=False)
    ai_chat_cog_module.chat_db_manager.get_bot_reply_daily_count = AsyncMock(
        return_value=0
    )

    asyncio.run(cog.on_message(message))

    message.author.send.assert_awaited_once()
    cog._record_dashboard_delivery_stats.assert_awaited_once_with(dm_messages=1)
    cog._record_bot_reply_usage_if_needed.assert_awaited_once_with(message)
