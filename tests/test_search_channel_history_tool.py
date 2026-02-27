import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.chat.features.tools.functions import search_channel_history as history_tool


def test_search_channel_history_supports_user_id_filter_and_cross_guild_dedup(
    monkeypatch,
):
    calls = []

    async def fake_execute_search(
        bot, guild_id, search_params, channel_id=None, max_results=500
    ):
        calls.append(
            {
                "guild_id": guild_id,
                "search_params": dict(search_params),
                "channel_id": channel_id,
                "max_results": max_results,
            }
        )
        if channel_id is not None:
            return [{"id": "1", "content": "from-channel"}]
        if guild_id == 111:
            return [{"id": "1", "content": "from-guild-111-dup"}]
        if guild_id == 222:
            return [{"id": "2", "content": "from-guild-222"}]
        return []

    monkeypatch.setattr(history_tool, "_execute_search", fake_execute_search)
    bot = SimpleNamespace(
        guilds=[
            SimpleNamespace(id=111),
            SimpleNamespace(id=222),
        ]
    )

    result = asyncio.run(
        history_tool.search_channel_history(
            query="",
            target_user="123456789012345678",
            max_results=30,
            bot=bot,
            guild_id="111",
            channel_id="888888888888888888",
        )
    )

    assert result["channel_results"] == [{"id": "1", "content": "from-channel"}]
    assert result["guild_wide_results"] == [{"id": "2", "content": "from-guild-222"}]
    assert len(calls) == 3
    assert result["searched_guild_count"] == 2
    assert result["max_results"] == 30
    guild_scope_calls = [call for call in calls if call["channel_id"] is None]
    assert {call["guild_id"] for call in guild_scope_calls} == {111, 222}
    assert all(
        call["search_params"].get("author_id") == "123456789012345678" for call in calls
    )
    assert all("content" not in call["search_params"] for call in calls)
    assert all(call["max_results"] == 30 for call in calls)


def test_search_channel_history_default_max_results_is_500(monkeypatch):
    calls = []

    async def fake_execute_search(
        bot, guild_id, search_params, channel_id=None, max_results=500
    ):
        calls.append(max_results)
        return []

    monkeypatch.setattr(history_tool, "_execute_search", fake_execute_search)
    bot = SimpleNamespace(guilds=[SimpleNamespace(id=111)])

    result = asyncio.run(
        history_tool.search_channel_history(
            query="hello",
            bot=bot,
            guild_id="111",
        )
    )

    assert calls == [500]
    assert result["max_results"] == 500


def test_search_channel_history_non_positive_max_results_means_unlimited(monkeypatch):
    calls = []

    async def fake_execute_search(
        bot, guild_id, search_params, channel_id=None, max_results=500
    ):
        calls.append(max_results)
        return []

    monkeypatch.setattr(history_tool, "_execute_search", fake_execute_search)
    bot = SimpleNamespace(guilds=[SimpleNamespace(id=111)])

    result = asyncio.run(
        history_tool.search_channel_history(
            query="hello",
            max_results=0,
            bot=bot,
            guild_id="111",
        )
    )

    assert calls == [None]
    assert result["max_results"] is None


def test_resolve_target_user_id_by_username_from_guild_cache():
    member = SimpleNamespace(
        id=42,
        display_name="月月",
        global_name="YueYue",
        name="yueyue_bot",
    )

    class DummyGuild:
        def __init__(self):
            self.members = [member]

        def get_member_named(self, _username):
            return None

        async def query_members(self, query, limit):
            return []

    resolved_user_id, error = asyncio.run(
        history_tool._resolve_target_user_id("月月", [DummyGuild()])
    )

    assert error is None
    assert resolved_user_id == "42"


def test_search_channel_history_returns_error_when_target_user_unresolved(monkeypatch):
    async def fake_resolve_target_user_id(_target_user, _guilds):
        return None, "在当前服务器中找不到用户名“月月”。"

    execute_mock = AsyncMock()
    monkeypatch.setattr(
        history_tool, "_resolve_target_user_id", fake_resolve_target_user_id
    )
    monkeypatch.setattr(history_tool, "_execute_search", execute_mock)

    result = asyncio.run(
        history_tool.search_channel_history(
            query="测试",
            target_user="月月",
            bot=SimpleNamespace(guilds=[SimpleNamespace(id=123)]),
            guild_id="123",
        )
    )

    assert result["error"] is True
    assert result["channel_results"] == []
    assert result["guild_wide_results"] == []
    assert "找不到用户名" in result["hint"]
    assert execute_mock.await_count == 0
