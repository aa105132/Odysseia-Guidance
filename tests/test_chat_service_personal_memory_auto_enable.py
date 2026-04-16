import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class _DummyThread:
    pass


class _DummyGuildChannel:
    pass


discord_module = types.ModuleType("discord")
discord_module.Thread = _DummyThread
discord_module.Message = type("Message", (), {})
discord_module.User = type("User", (), {})
discord_module.abc = types.SimpleNamespace(GuildChannel=_DummyGuildChannel)
sys.modules["discord"] = discord_module
sys.modules["discord.abc"] = discord_module.abc

gemini_service = types.SimpleNamespace(
    generate_response=AsyncMock(return_value="AI回复"),
    last_called_tools=[],
)
sys.modules["src.chat.services.gemini_service"] = types.SimpleNamespace(
    gemini_service=gemini_service
)

context_service = types.SimpleNamespace(
    get_formatted_channel_history_new=AsyncMock(return_value="历史上下文")
)
sys.modules["src.chat.services.context_service_test"] = types.SimpleNamespace(
    get_context_service=lambda: context_service
)

world_book_service = types.SimpleNamespace(
    get_profile_by_discord_id=AsyncMock(return_value=None),
    find_entries=AsyncMock(return_value=[]),
)
sys.modules[
    "src.chat.features.world_book.services.world_book_service"
] = types.SimpleNamespace(world_book_service=world_book_service)

affection_service = types.SimpleNamespace(
    get_affection_status=AsyncMock(return_value={"level": 1}),
    increase_affection_on_message=AsyncMock(),
)
sys.modules["src.chat.features.affection.service.affection_service"] = (
    types.SimpleNamespace(affection_service=affection_service)
)

coin_service = types.SimpleNamespace(grant_daily_message_reward=AsyncMock(return_value=False))
sys.modules["src.chat.features.odysseia_coin.service.coin_service"] = (
    types.SimpleNamespace(coin_service=coin_service)
)

personal_memory_service = types.SimpleNamespace(
    update_and_conditionally_summarize_memory=AsyncMock()
)
sys.modules[
    "src.chat.features.personal_memory.services.personal_memory_service"
] = types.SimpleNamespace(personal_memory_service=personal_memory_service)

sys.modules["src.chat.utils.database"] = types.SimpleNamespace(
    chat_db_manager=MagicMock()
)

chat_settings_service = types.SimpleNamespace(
    get_current_ai_model=AsyncMock(return_value="test-model"),
    update_user_cooldown=AsyncMock(),
    get_effective_channel_config=AsyncMock(return_value={}),
)
sys.modules[
    "src.chat.features.chat_settings.services.chat_settings_service"
] = types.SimpleNamespace(chat_settings_service=chat_settings_service)

sys.modules["src.chat.config.chat_config"] = types.SimpleNamespace(
    DEBUG_CONFIG={"LOG_FINAL_CONTEXT": False}
)
sys.modules["src.chat.config"] = types.SimpleNamespace(
    chat_config=types.SimpleNamespace(TUTORIAL_SEARCH_SUFFIX="")
)

from src.chat.services.chat_service import ChatService  # noqa: E402


def test_handle_chat_message_without_existing_profile_still_updates_personal_memory():
    service = ChatService()

    author = MagicMock()
    author.id = 123
    author.display_name = "测试用户"

    guild = MagicMock()
    guild.id = 456

    channel = MagicMock()
    channel.id = 789

    message = MagicMock()
    message.id = 1001
    message.author = author
    message.guild = guild
    message.channel = channel

    processed_data = {
        "user_content": "你好",
        "replied_content": "",
        "image_data_list": [],
    }

    result = asyncio.run(
        service.handle_chat_message(message, processed_data, "测试服务器", "测试频道")
    )

    assert result == "AI回复"
    personal_memory_service.update_and_conditionally_summarize_memory.assert_awaited_once_with(
        user_id=123,
        user_name="测试用户",
        user_content="你好",
        ai_response="AI回复",
    )
