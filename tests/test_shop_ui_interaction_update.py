import asyncio
import importlib
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@contextmanager
def load_simple_shop_view():
    fake_sqlalchemy = types.ModuleType("sqlalchemy")
    fake_sqlalchemy.text = lambda value: value

    fake_database_module = types.ModuleType("src.database.database")
    fake_database_module.AsyncSessionLocal = object()

    fake_tutorial_service_module = types.ModuleType(
        "src.chat.features.tutorial_search.services.tutorial_rag_service"
    )
    fake_tutorial_service_module.tutorial_rag_service = SimpleNamespace()

    fake_gift_service_module = types.ModuleType(
        "src.chat.features.affection.service.gift_service"
    )
    fake_gift_service_module.GiftService = object

    fake_gemini_service_module = types.ModuleType("src.chat.services.gemini_service")
    fake_gemini_service_module.gemini_service = object()
    fake_gemini_service_module.GeminiService = object

    fake_modules = {
        "sqlalchemy": fake_sqlalchemy,
        "src.database.database": fake_database_module,
        "src.chat.features.tutorial_search.services.tutorial_rag_service": (
            fake_tutorial_service_module
        ),
        "src.chat.features.affection.service.gift_service": fake_gift_service_module,
        "src.chat.services.gemini_service": fake_gemini_service_module,
    }
    reloaded_modules = [
        "src.chat.features.odysseia_coin.service.shop_service",
        "src.chat.features.odysseia_coin.ui.components.shop_components",
        "src.chat.features.odysseia_coin.ui.panels.tutorial_panel",
        "src.chat.features.odysseia_coin.ui.shop_ui",
    ]
    old_modules = {name: sys.modules.get(name) for name in reloaded_modules}

    try:
        for name in reloaded_modules:
            sys.modules.pop(name, None)
        with patch.dict(sys.modules, fake_modules):
            module = importlib.import_module(
                "src.chat.features.odysseia_coin.ui.shop_ui"
            )
            yield module.SimpleShopView
    finally:
        for name in reloaded_modules:
            sys.modules.pop(name, None)
            if old_modules[name] is not None:
                sys.modules[name] = old_modules[name]


def test_deferred_shop_update_edits_original_ephemeral_message():
    async def run_case():
        with load_simple_shop_view() as SimpleShopView:
            view = object.__new__(SimpleShopView)
            new_shop_embed = discord.Embed(title="🌙 月月商店", description="更新后")
            view.shop_panel = SimpleNamespace(
                create_embed=AsyncMock(return_value=new_shop_embed)
            )

            old_shop_embed = discord.Embed(title="🌙 月月商店", description="更新前")
            interaction = SimpleNamespace(
                message=SimpleNamespace(id=123, embeds=[old_shop_embed]),
                response=SimpleNamespace(
                    edit_message=AsyncMock(
                        side_effect=discord.errors.InteractionResponded(None)
                    )
                ),
                followup=SimpleNamespace(edit_message=AsyncMock()),
                edit_original_response=AsyncMock(),
            )

            await view._update_shop_embed(interaction)

            interaction.edit_original_response.assert_awaited_once_with(
                embeds=[new_shop_embed], view=view
            )
            interaction.followup.edit_message.assert_not_awaited()

    asyncio.run(run_case())
