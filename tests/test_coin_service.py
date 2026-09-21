"""灵石服务回归：使用隔离 SQLite 验证余额、签到及送礼退款。"""

from datetime import datetime, timedelta, timezone
import importlib
import sqlite3
from unittest.mock import AsyncMock

import pytest

from src.chat.utils.database import ChatDatabaseManager

coin_module = importlib.import_module("src.chat.features.odysseia_coin.service.coin_service")


@pytest.fixture
def service(monkeypatch, tmp_path):
    path = tmp_path / "coin_service.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE user_coins (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0,
                last_daily_message_date TEXT
            );
            CREATE TABLE coin_transactions (user_id INTEGER, amount INTEGER, reason TEXT);
        """)
    monkeypatch.setattr(coin_module, "chat_db_manager", ChatDatabaseManager(str(path)))
    monkeypatch.setattr(coin_module, "COIN_CONFIG", {"DAILY_FIRST_CHAT_REWARD": 10})
    return coin_module.CoinService()


@pytest.mark.asyncio
async def test_add_coins(service):
    assert await service.add_coins(1, 100, "初始余额") == 100
    assert await service.add_coins(1, 10, "测试") == 110
    assert await service.get_balance(1) == 110


@pytest.mark.asyncio
async def test_remove_coins_insufficient_balance(service):
    await service.add_coins(2, 50, "初始余额")
    assert await service.remove_coins(2, 100, "测试购买") is None
    assert await service.get_balance(2) == 50


@pytest.mark.asyncio
async def test_grant_daily_reward_first_time(service):
    assert await service.grant_daily_message_reward(3) is True
    assert await service.get_balance(3) == 10


@pytest.mark.asyncio
async def test_grant_daily_reward_already_granted(service):
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    await coin_module.chat_db_manager._execute(
        coin_module.chat_db_manager._db_transaction,
        "INSERT INTO user_coins VALUES (?, ?, ?)", (4, 50, today), commit=True,
    )
    assert await service.grant_daily_message_reward(4) is False
    assert await service.get_balance(4) == 50


@pytest.mark.asyncio
async def test_purchase_gift_and_rollback_on_failure(service, monkeypatch):
    await service.add_coins(5, 200, "初始余额")
    monkeypatch.setattr(service, "get_item_by_id", AsyncMock(return_value={
        "item_id": 1, "name": "泰迪熊", "price": 120, "target": "ai", "effect_id": None,
    }))
    affection = AsyncMock()
    affection.increase_affection_for_gift.return_value = (False, "今天已经送过啦！")
    monkeypatch.setattr(coin_module, "affection_service", affection)
    result = await service.purchase_item(5, 105, 1)
    assert result[0] is False
    assert "已经送过" in result[1]
    assert result[2] == 200
    assert await service.get_balance(5) == 200
