import asyncio
import sys
import types
from unittest.mock import AsyncMock, patch

fake_sqlalchemy = types.ModuleType("sqlalchemy")
fake_sqlalchemy.text = lambda value: value
sys.modules.setdefault("sqlalchemy", fake_sqlalchemy)

fake_database_module = types.ModuleType("src.database.database")
fake_database_module.AsyncSessionLocal = object()
sys.modules.setdefault("src.database.database", fake_database_module)

fake_tutorial_service_module = types.ModuleType(
    "src.chat.features.tutorial_search.services.tutorial_rag_service"
)
fake_tutorial_service_module.tutorial_rag_service = types.SimpleNamespace()
sys.modules.setdefault(
    "src.chat.features.tutorial_search.services.tutorial_rag_service",
    fake_tutorial_service_module,
)

from src.chat.features.odysseia_coin.service.coin_service import (
    PERSONAL_MEMORY_ITEM_EFFECT_ID,
)
from src.chat.features.odysseia_coin.service.shop_service import ShopService


def test_prepare_shop_data_discounts_personal_memory_item_by_effect_id():
    service = ShopService()
    items = [
        {
            "item_id": 1,
            "name": "不是旧名字也应该打折",
            "price": 100,
            "effect_id": PERSONAL_MEMORY_ITEM_EFFECT_ID,
        }
    ]
    fake_world_book_service_module = types.ModuleType(
        "src.chat.features.world_book.services.world_book_service"
    )
    fake_world_book_service_module.world_book_service = types.SimpleNamespace(
        get_profile_by_discord_id=AsyncMock(return_value={"discord_id": "123"})
    )

    with patch(
        "src.chat.features.odysseia_coin.service.shop_service.coin_service.get_balance",
        new=AsyncMock(return_value=520),
    ), patch(
        "src.chat.features.odysseia_coin.service.shop_service.coin_service.get_all_items",
        new=AsyncMock(return_value=items),
    ), patch(
        "src.chat.features.odysseia_coin.service.shop_service.event_service.get_active_event",
        return_value=None,
    ), patch.dict(
        sys.modules,
        {
            "src.chat.features.world_book.services.world_book_service": fake_world_book_service_module
        },
    ):
        shop_data = asyncio.run(service.prepare_shop_data(user_id=123))

    assert shop_data.items[0]["price"] == 10
