"""个人牌局记录与真实流水的隔离、分页和结算原子性。"""

import sqlite3
import asyncio
from contextlib import AsyncExitStack

import pytest

from test_table_wallet import wallet, balances
from test_table_multiplayer_api import api, USER_IDS, _clients


@pytest.mark.asyncio
async def test_settlement_details_are_atomic_and_idempotent(wallet):
    await wallet.reserve("round", ["1", "2"], 100)
    details = {"1": {"room_id": "ROOM", "actions": [{"user_id": "1", "action": "raise", "amount": 10}]},
               "2": {"room_id": "ROOM", "final_state": {"hand": ["SpadeK"]}}}
    for _ in range(2):
        await wallet.settle("round", {"1": 150, "2": 50}, game_type="texas", round_details=details)
    assert balances(wallet)[1] == 150
    first = await wallet.round_detail("1", "round")
    assert first["details"] == details["1"]
    assert (first["stake"], first["payout"], first["profit"], first["has_details"]) == (100, 150, 50, True)
    assert await wallet.round_detail("3", "round") is None
    assert (await wallet.round_detail("2", "round"))["details"] == details["2"]
    with sqlite3.connect(wallet.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM table_round_details").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_invalid_or_failed_details_roll_back_money_and_can_retry(wallet):
    await wallet.reserve("broken", ["1", "2"], 100)
    with pytest.raises(ValueError):
        await wallet.settle("broken", {"1": 150, "2": 50}, game_type="texas", round_details={"1": {"x": float("nan")}})
    assert balances(wallet)[1] == 0
    assert (await wallet.history("1"))["entries"] == []
    with sqlite3.connect(wallet.db_path) as connection:
        connection.execute("CREATE TRIGGER fail_details BEFORE INSERT ON table_round_details WHEN NEW.user_id=2 BEGIN SELECT RAISE(ABORT,'details failed'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await wallet.settle("broken", {"1": 150, "2": 50}, game_type="texas")
    assert balances(wallet)[1] == 0
    with sqlite3.connect(wallet.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM table_game_results").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_details")
    await wallet.settle("broken", {"1": 150, "2": 50}, game_type="texas")
    assert (await wallet.round_detail("1", "broken"))["details"] == {}


@pytest.mark.asyncio
async def test_history_handles_legacy_and_missing_blackjack_stakes(wallet):
    await wallet.reserve("legacy", ["1"], 100)
    await wallet.settle("legacy", {"1": 120})
    with sqlite3.connect(wallet.db_path) as connection:
        connection.execute("DELETE FROM table_round_details")
        connection.execute("INSERT INTO table_game_results VALUES ('old-21', 1, 'blackjack', '甲', '', 35, '2026-01-01', '2026-01-01')")
    history = await wallet.history("1")
    assert history["total"] == 2
    assert [entry["round_key"] for entry in history["entries"]] == ["old-21", "legacy"]
    old = history["entries"][0]
    assert old["stake"] is None and old["payout"] is None and old["has_details"] is False
    legacy = history["entries"][1]
    assert (legacy["stake"], legacy["payout"], legacy["game_type"], legacy["settled_at"], legacy["legacy"]) == (100, 120, None, None, True)
    assert (await wallet.history("1", "blackjack"))["total"] == 1
    assert (await wallet.history("2"))["total"] == 0


@pytest.mark.asyncio
async def test_blackjack_details_record_stake_payout_and_do_not_overwrite_on_retry(wallet):
    await wallet.settle_blackjack("bj", "1", 20, 50, details={"final_state": {"hand": ["HeartA", "SpadeK"]}})
    before = balances(wallet)
    await wallet.settle_blackjack("bj", "1", 20, 50, details={"other": "changed"})
    assert balances(wallet) == before
    row = await wallet.round_detail("1", "bj")
    assert (row["stake"], row["payout"], row["profit"]) == (20, 50, 30)
    assert "final_state" in row["details"] and "other" not in row["details"]


@pytest.mark.asyncio
async def test_history_stable_paging_extrema_and_filtered_statistics(wallet):
    for key, payout in (("a", 150), ("b", 50), ("c", 250)):
        await wallet.settle_blackjack(key, "1", 100, payout)
    with sqlite3.connect(wallet.db_path) as connection:
        connection.execute("UPDATE table_game_results SET settled_at='2026-09-23T00:00:00+08:00'")
    first = await wallet.history("1", limit=2)
    second = await wallet.history("1", limit=2, offset=2)
    assert [entry["round_key"] for entry in first["entries"]] == ["c", "b"]
    assert [entry["round_key"] for entry in second["entries"]] == ["a"]
    assert first["has_more"] and not second["has_more"]
    stats = (await wallet.statistics("1"))["stats"]
    assert {key: stats[key] for key in ("max_win", "max_loss", "total_won", "total_lost", "average_profit")} == {
        "max_win": 150, "max_loss": 50, "total_won": 200, "total_lost": 50, "average_profit": 50}
    empty = (await wallet.statistics("1", "texas"))["stats"]
    assert all(empty[key] == 0 for key in ("max_win", "max_loss", "total_won", "total_lost", "average_profit"))


@pytest.mark.asyncio
async def test_leaderboard_has_100_entries_excludes_bots_and_preserves_self(wallet):
    await wallet.statistics("1")
    with sqlite3.connect(wallet.db_path) as connection:
        for uid in range(-1, 103):
            connection.execute("INSERT INTO table_game_results VALUES (?, ?, 'texas', '玩家', '', ?, ?, ?)",
                               (f"r{uid}", uid, 1000 - uid, "2026-09-23", wallet._today()))
    board = await wallet.leaderboard("102", excluded_user_ids=(1,))
    assert len(board["entries"]) == 100
    assert board["entries"][0]["user_id"] == "2"
    assert board["entries"][0]["rank"] == 1
    assert board["self"]["rank"] == 101


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,offset", [(True, 0), (0, 0), (101, 0), (1.5, 0), (20, -1), (20, True), (20, 100001)])
async def test_paging_rejects_invalid_values(wallet, limit, offset):
    with pytest.raises(ValueError):
        await wallet.history("1", limit=limit, offset=offset)
    with pytest.raises(ValueError):
        await wallet.transactions("1", limit=limit, offset=offset)
    if offset == 0:
        with pytest.raises(ValueError):
            await wallet.leaderboard("1", limit=limit)


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier,timestamp", [("transaction_id", True), ("id", False), (None, False)])
async def test_real_transactions_schema_variants_paging_and_account_isolation(wallet, identifier, timestamp):
    with sqlite3.connect(wallet.db_path) as connection:
        connection.execute("DROP TABLE coin_transactions")
        id_column = f"{identifier} INTEGER PRIMARY KEY AUTOINCREMENT," if identifier else ""
        timestamp_column = ", timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP" if timestamp else ""
        connection.execute(f"CREATE TABLE coin_transactions ({id_column}user_id INTEGER, amount INTEGER, reason TEXT{timestamp_column})")
        connection.executemany("INSERT INTO coin_transactions (user_id,amount,reason) VALUES (?,?,?)",
                               [(1, 20, "签到"), (2, 500, "红包"), (1, -10, "下注")])
    result = await wallet.transactions("1", limit=1)
    assert result["balance"] == 100
    assert result["total"] == 2 and result["has_more"]
    assert result["entries"][0]["amount"] == -10
    assert result["entries"][0]["reason"] == "下注"
    assert bool(result["entries"][0]["timestamp"]) == timestamp
    second = await wallet.transactions("1", limit=1, offset=1)
    assert second["entries"][0]["reason"] == "签到" and not second["has_more"]
    assert (await wallet.transactions("999"))["balance"] is None


@pytest.mark.asyncio
async def test_refunded_games_not_in_history_but_refund_is_in_wallet_transactions(wallet):
    await wallet.reserve("cancelled", ["1"], 100)
    await wallet.refund("cancelled")
    assert (await wallet.history("1"))["total"] == 0
    assert await wallet.round_detail("1", "cancelled") is None
    flow = await wallet.transactions("1")
    assert [row["amount"] for row in flow["entries"]] == [100, -100]
    assert flow["balance"] == 100


@pytest.mark.asyncio
async def test_blackjack_detail_failure_rolls_back_entire_credit(wallet):
    await wallet.statistics("1")
    with sqlite3.connect(wallet.db_path) as connection:
        connection.execute("CREATE TRIGGER reject_detail BEFORE INSERT ON table_round_details BEGIN SELECT RAISE(ABORT,'broken'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await wallet.settle_blackjack("failed-bj", "1", 100, 250, details={"room_id": "X"})
    assert balances(wallet)[1] == 100
    assert (await wallet.history("1"))["entries"] == []
    with sqlite3.connect(wallet.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM coin_transactions").fetchone()[0] == 0


def test_sqlite_transaction_timestamps_are_explicit_utc(wallet):
    assert wallet._transaction_timestamp("2026-09-23 01:02:03") == "2026-09-23T01:02:03+00:00"
    assert wallet._transaction_timestamp("2026-09-23T09:02:03+08:00") == "2026-09-23T09:02:03+08:00"
    assert wallet._transaction_timestamp(None) is None


def test_history_api_enforces_current_account_and_paging(api):
    async def scenario():
        wallet = api.module._get_table_wallet()
        await wallet.reserve("private-round", [USER_IDS[0]], 100)
        await wallet.settle("private-round", {USER_IDS[0]: 180}, game_type="texas",
                            round_details={USER_IDS[0]: {"room_id": "PRIVATE"}})
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:2])
            own, other = clients.values()
            detail = await own.get("/api/tables/history/private-round")
            assert detail.status_code == 200
            assert detail.json()["round"]["details"]["room_id"] == "PRIVATE"
            assert (await other.get("/api/tables/history/private-round")).status_code == 404
            assert (await other.get("/api/tables/history")).json()["total"] == 0
            history = await own.get("/api/tables/history?game_type=texas")
            assert history.json()["entries"][0]["stake"] == 100
            assert (await own.get("/api/tables/history?game_type=invalid")).status_code == 400
            assert (await own.get("/api/tables/history?limit=101")).status_code == 422
            assert (await own.get("/api/tables/transactions?offset=-1")).status_code == 422
            assert (await other.get("/api/tables/transactions")).json()["entries"] == []
            assert (await own.get("/api/tables/leaderboard?limit=100")).status_code == 200
    asyncio.run(scenario())
