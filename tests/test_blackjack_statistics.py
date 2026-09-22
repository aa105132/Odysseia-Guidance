"""21 点 API 使用真实 SQLite 余额和统计，验证双倍、自然 21、弃局与结算重试。"""
import asyncio
import importlib
import sqlite3

import pytest

from test_blackjack_multiplayer_api import (
    api, single_api, HOST_ID, GUEST_ID, THIRD_ID, _client, _create_room, _start_round,
    _finish_round, _request,
)


@pytest.fixture
def real_stats(single_api, monkeypatch):
    path = single_api.single_db_path
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE user_coins (user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL);
            CREATE TABLE coin_transactions (user_id INTEGER, amount INTEGER, reason TEXT);
        """)
        connection.executemany("INSERT INTO user_coins VALUES (?, 1000)", [(uid,) for uid in (HOST_ID, GUEST_ID, THIRD_ID)])
    wallet_type = importlib.import_module("src.chat.features.games.blackjack-web.table_wallet").TableWallet
    single_api.module.table_wallet = wallet_type(str(path))

    class Coins:
        async def get_balance(self, uid):
            with sqlite3.connect(path) as connection:
                return connection.execute("SELECT balance FROM user_coins WHERE user_id = ?", (uid,)).fetchone()[0]

        async def remove_coins(self, uid, amount, reason):
            with sqlite3.connect(path) as connection:
                if connection.execute("UPDATE user_coins SET balance = balance - ? WHERE user_id = ? AND balance >= ?", (amount, uid, amount)).rowcount != 1:
                    return None
            return await self.get_balance(uid)

        async def add_coins(self, uid, amount, reason):
            with sqlite3.connect(path) as connection:
                connection.execute("UPDATE user_coins SET balance = balance + ? WHERE user_id = ?", (amount, uid))
            return await self.get_balance(uid)

    monkeypatch.setattr(single_api.module, "coin_service", Coins())
    return single_api


def _deck(api, monkeypatch, sequence):
    cards = [card for card in api.single_service._create_deck() if card not in sequence]
    cards.extend(reversed(sequence))
    monkeypatch.setattr(api.single_service, "_create_deck", lambda: list(cards))


@pytest.mark.parametrize("kind,sequence,action,profit", [
    ("natural", ["SpadeA", "HeartK", "Club10", "Diamond7"], None, 150),
    ("double", ["Spade5", "Heart6", "Club10", "Diamond7", "Heart10"], "double", 200),
    ("forfeit", ["Spade10", "Heart9", "Club10", "Diamond7"], "forfeit", -100),
    ("bust", ["Spade10", "Heart9", "Club10", "Diamond7", "Heart5"], "hit", -100),
    ("push", ["Spade10", "Heart7", "Club10", "Diamond7"], "stand", 0),
])
def test_single_statistics(real_stats, monkeypatch, kind, sequence, action, profit):
    _deck(real_stats, monkeypatch, sequence)
    async def run():
        async with _client(real_stats) as client:
            response = await client.post("/api/game/start", json={"amount": 100})
            assert response.status_code == 200, response.text
            if action:
                response = await client.post(f"/api/game/{action}")
                assert response.status_code == 200, response.text
            response = await client.get("/api/tables/stats?game_type=blackjack")
            assert response.status_code == 200, response.text
            stats = response.json()["stats"]
            assert (stats["rounds"], stats["net_profit"]) == (1, profit)
            assert await real_stats.module.coin_service.get_balance(HOST_ID) == 1000 + profit
    asyncio.run(run())


def test_single_failed_statistics_sync_retries_without_refund_or_duplicate_payout(real_stats):
    async def run():
        wallet = real_stats.module.table_wallet
        await wallet.statistics(str(HOST_ID))
        with sqlite3.connect(wallet.db_path) as connection:
            connection.execute("CREATE TRIGGER fail_stats BEFORE INSERT ON table_game_results BEGIN SELECT RAISE(ABORT, '模拟统计失败'); END")
        async with _client(real_stats) as client:
            assert (await client.post("/api/game/start", json={"amount": 100})).status_code == 200
            assert (await client.post("/api/game/stand")).status_code == 500
            assert await real_stats.module.coin_service.get_balance(HOST_ID) == 900
            with sqlite3.connect(wallet.db_path) as connection:
                connection.execute("DROP TRIGGER fail_stats")
            assert (await client.get("/api/game/current")).json()["new_balance"] == 1100
            assert (await client.get("/api/game/current")).json()["new_balance"] == 1100
            assert (await wallet.statistics(str(HOST_ID), "blackjack"))["stats"]["rounds"] == 1
    asyncio.run(run())


def test_multi_statistics_keeps_distinct_rounds(real_stats):
    async def run():
        async with _client(real_stats) as client:
            users = (HOST_ID, GUEST_ID, THIRD_ID)
            room_id = await _create_room(client, users)
            for _ in range(2):
                await _start_round(client, room_id, users)
                await _finish_round(client, room_id, users)
                for uid in users:
                    assert (await _request(client, room_id, uid, method="GET")).status_code == 200
            for uid, profit in zip(users, (200, 200, -200)):
                stats = (await real_stats.module.table_wallet.statistics(str(uid), "blackjack"))["stats"]
                assert (stats["rounds"], stats["net_profit"]) == (2, profit)
    asyncio.run(run())


def test_multi_forfeit_counts_loss_but_waiting_refund_does_not(real_stats):
    async def run():
        async with _client(real_stats) as client:
            room_id = await _create_room(client)
            await _start_round(client, room_id)
            response = await _request(client, "leave", GUEST_ID, room_id=room_id)
            assert response.status_code == 200, response.text
            stats = (await real_stats.module.table_wallet.statistics(str(GUEST_ID), "blackjack"))["stats"]
            assert (stats["rounds"], stats["net_profit"]) == (1, -100)
            await _request(client, "stand", HOST_ID, room_id=room_id)
            assert (await _request(client, "bet", HOST_ID, room_id=room_id, amount=100)).status_code == 200
            assert (await _request(client, "leave", HOST_ID, room_id=room_id)).status_code == 200
            stats = (await real_stats.module.table_wallet.statistics(str(HOST_ID), "blackjack"))["stats"]
            assert (stats["rounds"], stats["net_profit"]) == (1, 100)
    asyncio.run(run())


def test_single_committed_payout_survives_delete_failure_and_restart_cleanup(real_stats, monkeypatch):
    original_delete = real_stats.single_service.delete_game
    failures = [True]

    async def delete_game(uid):
        if failures:
            failures.pop()
            raise RuntimeError("模拟删除失败")
        await original_delete(uid)

    monkeypatch.setattr(real_stats.single_service, "delete_game", delete_game)

    async def run():
        async with _client(real_stats) as client:
            assert (await client.post("/api/game/start", json={"amount": 100})).status_code == 200
            assert (await client.post("/api/game/stand")).status_code == 500
            assert await real_stats.module.coin_service.get_balance(HOST_ID) == 1100
            # 重启清理只能退款未结束局，已派彩局保留幂等键继续完成清理。
            await real_stats.single_service.cleanup_stale_games()
            assert await real_stats.single_service.get_active_game(HOST_ID) is not None
            assert (await client.get("/api/game/current")).json()["new_balance"] == 1100
            assert (await real_stats.module.table_wallet.statistics(str(HOST_ID), "blackjack"))["stats"]["rounds"] == 1
    asyncio.run(run())
