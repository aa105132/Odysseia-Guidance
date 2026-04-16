# -*- coding: utf-8 -*-

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.chat.utils.database import ChatDatabaseManager


def test_bot_reply_daily_count_methods_track_usage():
    async def _run():
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "chat.db")
            manager = ChatDatabaseManager(db_path=db_path)
            await manager.init_async()

            assert await manager.get_bot_reply_daily_count("2026-04-16") == 0

            await manager.increment_bot_reply_daily_count("2026-04-16")
            await manager.increment_bot_reply_daily_count("2026-04-16", amount=3)

            assert await manager.get_bot_reply_daily_count("2026-04-16") == 4

    asyncio.run(_run())


def test_bot_reply_pause_methods_toggle_scope():
    async def _run():
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "chat.db")
            manager = ChatDatabaseManager(db_path=db_path)
            await manager.init_async()

            assert await manager.is_bot_reply_paused(123456) is False

            await manager.set_bot_reply_paused(123456, True)
            assert await manager.is_bot_reply_paused(123456) is True

            await manager.set_bot_reply_paused(123456, False)
            assert await manager.is_bot_reply_paused(123456) is False

    asyncio.run(_run())
