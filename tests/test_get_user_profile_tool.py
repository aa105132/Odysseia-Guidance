# -*- coding: utf-8 -*-

import asyncio
import importlib
import os
import sys
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


fake_coin_service_module = types.ModuleType(
    "src.chat.features.odysseia_coin.service.coin_service"
)
fake_coin_service_module.coin_service = types.SimpleNamespace(
    get_balance=AsyncMock(return_value=0),
    get_transaction_count=AsyncMock(return_value=0),
)
sys.modules.setdefault(
    "src.chat.features.odysseia_coin.service.coin_service",
    fake_coin_service_module,
)

fake_database_module = types.ModuleType("src.database.database")
fake_database_module.AsyncSessionLocal = MagicMock()
sys.modules.setdefault("src.database.database", fake_database_module)

fake_models_module = types.ModuleType("src.database.models")
fake_models_module.CommunityMemberProfile = type(
    "CommunityMemberProfile",
    (),
    {"discord_id": None},
)
sys.modules.setdefault("src.database.models", fake_models_module)

fake_chat_db_module = types.ModuleType("src.chat.utils.database")
fake_chat_db_module.chat_db_manager = MagicMock()
sys.modules.setdefault("src.chat.utils.database", fake_chat_db_module)

fake_discord_module = types.ModuleType("discord")
fake_discord_module.Guild = object
fake_discord_module.Member = object
fake_discord_module.User = object
fake_discord_module.Client = object
sys.modules.setdefault("discord", fake_discord_module)

fake_sqlalchemy_module = types.ModuleType("sqlalchemy")
fake_sqlalchemy_module.select = lambda *args, **kwargs: None
sys.modules.setdefault("sqlalchemy", fake_sqlalchemy_module)

tool_module = importlib.import_module("src.chat.features.tools.functions.get_user_profile")


class _FakeAvatar:
    def __init__(self, url: str):
        self.url = url


class _FakeRole:
    def __init__(self, name: str):
        self.name = name


class _FakeUser:
    def __init__(self):
        self.display_name = "测试昵称"
        self.global_name = "测试全局名"
        self.name = "test_user"
        self.created_at = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        self.display_avatar = _FakeAvatar("https://example.com/avatar.png")


class _FakeMember(_FakeUser):
    def __init__(self):
        super().__init__()
        self.display_name = "服务器昵称"
        self.joined_at = datetime(2025, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
        self.roles = [_FakeRole("@everyone"), _FakeRole("测试身份组")]


class _FakeGuild:
    def __init__(self, member):
        self._member = member

    def get_member(self, user_id):
        return self._member if user_id == 123456 else None

    async def fetch_member(self, user_id):
        return self.get_member(user_id)


def test_get_user_profile_supports_card_queries(monkeypatch):
    fake_user = _FakeUser()
    fake_member = _FakeMember()
    fake_guild = _FakeGuild(fake_member)

    async def _fake_load_member_profile_record(_user_id: int):
        return {
            "title": "名片标题",
            "full_text": "名称: 测试昵称\n背景信息: 爱打游戏",
            "source_metadata": {
                "name": "名片里的名字",
                "personality": "活泼",
                "background": "爱打游戏",
                "preferences": "甜食",
            },
            "personal_summary": "喜欢聊天，也会记住熟人。",
            "created_at": datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            "updated_at": datetime(2025, 3, 1, 0, 0, 0, tzinfo=timezone.utc),
            "personal_message_count": 4,
            "history": [
                {"role": "user", "parts": ["你好"]},
                {"role": "model", "parts": ["你好呀"]},
                {"role": "user", "parts": ["再聊聊"]},
            ],
        }

    async def _fake_load_inventory_rows(_user_id: int):
        return [
            {
                "item_id": 1,
                "quantity": 2,
                "name": "小蛋糕",
                "description": "甜甜的",
                "category": "food",
            },
            {
                "item_id": 2,
                "quantity": 1,
                "name": "通行许可",
                "description": "帖子功能相关",
                "category": "special",
            },
        ]

    monkeypatch.setattr(
        tool_module,
        "_load_member_profile_record",
        _fake_load_member_profile_record,
    )
    monkeypatch.setattr(
        tool_module,
        "_load_inventory_rows",
        _fake_load_inventory_rows,
    )
    monkeypatch.setattr(
        tool_module.coin_service,
        "get_balance",
        AsyncMock(return_value=321),
    )
    monkeypatch.setattr(
        tool_module.coin_service,
        "get_transaction_count",
        AsyncMock(return_value=7),
    )

    fake_bot = types.SimpleNamespace(fetch_user=AsyncMock(return_value=fake_user))

    result = asyncio.run(
        tool_module.get_user_profile(
            user_id="123456",
            queries=[
                "display_name",
                "bio",
                "inventory",
                "currency",
                "join_date",
                "activity_stats",
                "avatar",
                "roles",
            ],
            bot=fake_bot,
            guild=fake_guild,
        )
    )

    assert result["errors"] == []
    assert result["unsupported_queries"] == []
    assert set(result["queries_successful"]) == {
        "display_name",
        "bio",
        "inventory",
        "currency",
        "join_date",
        "activity_stats",
        "avatar",
        "roles",
    }
    assert result["profile"]["display_name"]["value"] == "服务器昵称"
    assert result["profile"]["bio"]["background"] == "爱打游戏"
    assert result["profile"]["bio"]["personal_memory_summary"] == "喜欢聊天，也会记住熟人。"
    assert result["profile"]["currency"]["amount"] == 321
    assert result["profile"]["balance"]["name"] == "月光币"
    assert result["profile"]["inventory"]["total_item_types"] == 2
    assert result["profile"]["inventory"]["total_quantity"] == 3
    assert result["profile"]["activity_stats"]["coin_transaction_count"] == 7
    assert result["profile"]["activity_stats"]["memory_pending_user_turns"] == 4
    assert result["profile"]["activity_stats"]["memory_history_user_turns"] == 2
    assert result["profile"]["join_date"]["guild_joined_at"].startswith("2025-02-03")
    assert result["profile"]["join_date"]["discord_account_created_at"].startswith(
        "2024-01-02"
    )
    assert result["profile"]["avatar"]["url"] == "https://example.com/avatar.png"
    assert result["profile"]["avatar_url"] == "https://example.com/avatar.png"
    assert result["profile"]["roles"] == ["测试身份组"]
    assert "avatar_image_base64" not in str(result)


def test_get_user_profile_normalizes_alias_queries(monkeypatch):
    monkeypatch.setattr(
        tool_module,
        "_load_member_profile_record",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        tool_module,
        "_load_inventory_rows",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        tool_module.coin_service,
        "get_balance",
        AsyncMock(return_value=99),
    )
    monkeypatch.setattr(
        tool_module.coin_service,
        "get_transaction_count",
        AsyncMock(return_value=1),
    )

    fake_user = _FakeUser()
    fake_bot = types.SimpleNamespace(fetch_user=AsyncMock(return_value=fake_user))

    result = asyncio.run(
        tool_module.get_user_profile(
            user_id="123456",
            queries=["name", "balance", "items", "stats", "icon", "unknown_field"],
            bot=fake_bot,
            guild=None,
        )
    )

    assert result["queries_canonical"] == [
        "display_name",
        "currency",
        "inventory",
        "activity_stats",
        "avatar",
    ]
    assert result["unsupported_queries"] == ["unknown_field"]
    assert result["profile"]["currency"]["amount"] == 99
    assert result["profile"]["avatar"]["has_avatar"] is True
