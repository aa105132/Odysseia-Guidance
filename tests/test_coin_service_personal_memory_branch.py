import asyncio
import sys
import types
from unittest.mock import AsyncMock, patch

from src.chat.features.odysseia_coin.service.coin_service import (
    PERSONAL_MEMORY_ITEM_EFFECT_ID,
    coin_service,
)


def test_purchase_item_uses_existing_world_book_profile_for_personal_memory():
    fake_world_book_service_module = types.ModuleType(
        "src.chat.features.world_book.services.world_book_service"
    )
    fake_world_book_service_module.world_book_service = types.SimpleNamespace(
        get_profile_by_discord_id=AsyncMock(return_value={"discord_id": "123"})
    )

    item = {
        "item_id": 1,
        "name": "名片",
        "price": 10,
        "target": "self",
        "effect_id": PERSONAL_MEMORY_ITEM_EFFECT_ID,
    }

    with patch.object(
        coin_service, "get_item_by_id", new=AsyncMock(return_value=item)
    ), patch.object(
        coin_service, "get_balance", new=AsyncMock(return_value=100)
    ), patch.object(
        coin_service, "remove_coins", new=AsyncMock(return_value=90)
    ), patch.dict(
        sys.modules,
        {
            "src.chat.features.world_book.services.world_book_service": fake_world_book_service_module
        },
    ):
        result = asyncio.run(coin_service.purchase_item(123, 456, 1))

    assert result[0] is True
    assert "更新你的个人档案" in result[1]
    assert result[2] == 90
    assert result[3] is True
