"""统计使用真实结算账本，验证原子性、重复请求和北京时间日榜。"""

import asyncio
from contextlib import AsyncExitStack
import sqlite3

import pytest

from test_table_wallet import wallet, balances
from test_table_multiplayer_api import (
    api, USER_IDS, _clients, _create_and_join, _ready_and_start,
    _finish_through_requests,
)


@pytest.mark.asyncio
async def test_statistics_record_actual_profits_once_and_ignore_refunds(wallet):
    await wallet.reserve("round-1", ["1", "2"], 100)
    profiles = {"1": {"username": "甲", "avatar_url": "/a.png"}, "2": {"username": "乙"}}
    for _ in range(2):
        await wallet.settle("round-1", {"1": 150, "2": 50}, game_type="texas", profiles=profiles)
    await wallet.reserve("refund", ["2"], 100)
    await wallet.refund("refund")
    stats = (await wallet.statistics("1", "texas"))["stats"]
    assert stats == {"rounds": 1, "wins": 1, "losses": 0,
                     "draws": 0, "win_rate": 100, "net_profit": 50, "today_profit": 50,
                     "legacy_rounds": 0}
    loser = (await wallet.statistics("2", "texas"))["stats"]
    assert (loser["rounds"], loser["losses"], loser["net_profit"]) == (1, 1, -50)
    board = await wallet.leaderboard("2", "today", "texas")
    assert [entry["net_profit"] for entry in board["entries"]] == [50, -50]
    assert board["entries"][0]["username"] == "甲"
    assert board["self"]["rank"] == 2
    restarted_wallet = type(wallet)(wallet.db_path)
    assert (await restarted_wallet.statistics("1", "texas"))["stats"] == stats


@pytest.mark.asyncio
async def test_statistics_filters_day_and_game_and_keeps_tied_ranks(wallet, monkeypatch):
    await wallet.reserve("older", ["1", "2"], 100)
    await wallet.settle("older", {"1": 100, "2": 100}, game_type="texas")
    with sqlite3.connect(wallet.db_path) as connection:
        connection.execute("UPDATE table_game_results SET settled_day = '2026-09-21'")
    monkeypatch.setattr(wallet, "_today", lambda: "2026-09-22")
    assert (await wallet.leaderboard("1", "today", "texas"))["entries"] == []
    totals = await wallet.leaderboard("2", "all", "texas", limit=1)
    assert len(totals["entries"]) == 1
    assert totals["self"]["rank"] == 1
    assert (await wallet.statistics("1", "golden_flower"))["stats"]["rounds"] == 0
    assert (await wallet.statistics("1", "texas"))["stats"]["draws"] == 1


@pytest.mark.asyncio
async def test_statistics_failure_rolls_back_payout_and_can_retry(wallet):
    await wallet.reserve("atomic", ["1", "2"], 100)
    with sqlite3.connect(wallet.db_path) as connection:
        connection.executescript("""
            CREATE TRIGGER fail_result BEFORE INSERT ON table_game_results
            WHEN NEW.user_id = 2 BEGIN SELECT RAISE(ABORT, '统计写入失败'); END;
        """)
    with pytest.raises(sqlite3.IntegrityError):
        await wallet.settle("atomic", {"1": 150, "2": 50}, game_type="texas")
    assert balances(wallet)[1] == 0
    assert (await wallet.statistics("1"))["stats"]["rounds"] == 0
    with sqlite3.connect(wallet.db_path) as connection:
        connection.execute("DROP TRIGGER fail_result")
    await wallet.settle("atomic", {"1": 150, "2": 50}, game_type="texas")
    assert balances(wallet)[1] == 150
    assert (await wallet.statistics("1"))["stats"]["rounds"] == 1


def test_statistics_api_integrates_with_real_finished_table(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:2]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, "texas")
            room = await _ready_and_start(clients, room["room_id"])
            room = await _finish_through_requests(api, clients, room)
            for uid, client in clients.items():
                response = await client.get("/api/tables/stats?game_type=texas")
                assert response.status_code == 200, response.text
                stats = response.json()["stats"]
                assert stats["rounds"] == 1
                assert stats["net_profit"] == room["actual_settlement"][uid]
                assert (await client.get("/api/tables/stats?game_type=invalid")).status_code == 400
                assert (await client.get("/api/tables/leaderboard?period=invalid")).status_code == 422
            response = await clients[ids[0]].get("/api/tables/leaderboard?period=today&game_type=texas")
            assert response.status_code == 200, response.text
            entries = response.json()["entries"]
            assert len(entries) == 2
            assert sum(entry["net_profit"] for entry in entries) == 0
            assert entries[0]["net_profit"] >= entries[1]["net_profit"]
    asyncio.run(scenario())


def test_statistics_excludes_ai_players(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:1])
            client = next(iter(clients.values()))
            created = await client.post("/api/tables/create", json={
                "game_type": "texas", "mode": "solo", "include_yueyue": True,
            })
            assert created.status_code == 200, created.text
            room = created.json()["room"]
            room = await _ready_and_start(clients, room["room_id"])
            room = await _finish_through_requests(api, clients, room)
            response = await client.get("/api/tables/leaderboard?period=all&game_type=texas")
            assert response.status_code == 200, response.text
            entries = response.json()["entries"]
            assert len(entries) == 1
            assert entries[0]["user_id"] == USER_IDS[0]
            assert entries[0]["net_profit"] == room["actual_settlement"][USER_IDS[0]]
    asyncio.run(scenario())

@pytest.mark.asyncio
async def test_legacy_results_only_count_in_unfiltered_totals(wallet):
    await wallet.reserve("legacy", ["1", "2"], 100)
    await wallet.settle("legacy", {"1": 120, "2": 80})
    stats = (await wallet.statistics("1"))["stats"]
    assert (stats["rounds"], stats["legacy_rounds"], stats["net_profit"], stats["today_profit"]) == (1, 1, 20, 0)
    assert (await wallet.statistics("1", "texas"))["stats"]["rounds"] == 0
    assert (await wallet.leaderboard("1", "today"))["entries"] == []
    board = await wallet.leaderboard("1", "all")
    assert board["legacy_rounds"] == 2
    assert board["self"]["net_profit"] == 20


@pytest.mark.asyncio
async def test_blackjack_settlement_atomic_and_retryable(wallet):
    await wallet.statistics("1")
    with sqlite3.connect(wallet.db_path) as connection:
        connection.execute("CREATE TRIGGER fail_blackjack BEFORE INSERT ON table_game_results BEGIN SELECT RAISE(ABORT, '模拟统计失败'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await wallet.settle_blackjack("blackjack:1", "1", 100, 250)
    assert balances(wallet)[1] == 100
    with sqlite3.connect(wallet.db_path) as connection:
        connection.execute("DROP TRIGGER fail_blackjack")
    for _ in range(2):
        assert await wallet.settle_blackjack("blackjack:1", "1", 100, 250) == 350
    stats = (await wallet.statistics("1", "blackjack"))["stats"]
    assert (stats["rounds"], stats["net_profit"]) == (1, 150)
    with pytest.raises(ValueError, match="不同金额"):
        await wallet.settle_blackjack("blackjack:1", "1", 100, 200)
