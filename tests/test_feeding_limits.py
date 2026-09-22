"""投喂30分钟冷却与每日3次的真实服务判定。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import importlib

import pytest


module = importlib.import_module("src.chat.features.affection.service.feeding_service")


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes,count,allowed", [(29, 1, False), (31, 2, True), (31, 3, False), (None, 0, True)])
async def test_feeding_30_minute_cooldown_and_daily_three(monkeypatch, minutes, count, allowed):
    monkeypatch.setitem(module.FEEDING_CONFIG, "COOLDOWN_SECONDS", 1800)
    monkeypatch.setitem(module.FEEDING_CONFIG, "DAILY_LIMIT", 3)
    latest = None if minutes is None else ((datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(),)
    service = module.FeedingService()
    service.db_manager = SimpleNamespace(_db_transaction=object(), _execute=AsyncMock(side_effect=[latest, (count,)]))
    actual, message = await service.can_feed("1")
    assert actual == allowed
    if minutes == 29:
        assert "以后再喂" in message and service.db_manager._execute.await_count == 1
    elif count == 3:
        assert "3次" in message


@pytest.mark.asyncio
async def test_admin_can_explicitly_disable_cooldown_without_disabling_daily_cap(monkeypatch):
    monkeypatch.setitem(module.FEEDING_CONFIG, "COOLDOWN_SECONDS", 0)
    monkeypatch.setitem(module.FEEDING_CONFIG, "DAILY_LIMIT", 3)
    service = module.FeedingService()
    service.db_manager = SimpleNamespace(_db_transaction=object(), _execute=AsyncMock(side_effect=[(datetime.now(timezone.utc).isoformat(),), (3,)]))
    allowed, message = await service.can_feed("1")
    assert not allowed and "3次" in message
