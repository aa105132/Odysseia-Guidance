"""用真实 SQLite 验证灵石托管的事务、幂等与恢复，不访问实际账户。"""

import asyncio
import importlib
import sqlite3

import pytest


wallet_module = importlib.import_module("src.chat.features.games.blackjack-web.table_wallet")
service_module = importlib.import_module("src.chat.features.games.blackjack-web.table_service")


@pytest.fixture
def wallet(tmp_path):
    path = tmp_path / "coins.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE user_coins (user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL);
            CREATE TABLE coin_transactions (user_id INTEGER, amount INTEGER, reason TEXT);
            INSERT INTO user_coins VALUES (1, 100), (2, 20000), (3, 50);
        """)
    return wallet_module.TableWallet(str(path))


def balances(wallet):
    with sqlite3.connect(wallet.db_path) as connection:
        return dict(connection.execute("SELECT user_id,balance FROM user_coins"))


@pytest.mark.asyncio
async def test_insufficient_player_rolls_back_whole_table(wallet):
    with pytest.raises(wallet_module.InsufficientTableBalance):
        await wallet.reserve("r1", ["1", "3"], 100)
    assert balances(wallet) == {1: 100, 2: 20000, 3: 50}
    with sqlite3.connect(wallet.db_path) as connection:
        assert connection.execute("SELECT count(*) FROM coin_transactions").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_room_entry_threshold_is_checked_inside_reservation_transaction(wallet):
    with pytest.raises(wallet_module.InsufficientTableBalance):
        await wallet.reserve("tier", ["2", "1"], 100, entry_min=1000)
    assert balances(wallet) == {1: 100, 2: 20000, 3: 50}
    with sqlite3.connect(wallet.db_path) as connection:
        assert connection.execute("SELECT count(*) FROM coin_transactions").fetchone()[0] == 0
    await wallet.reserve("tier", ["2"], 500, entry_min=1000)
    assert balances(wallet)[2] == 19500
    # 重试已托管请求不需要再次满足准入，也不能重复冻结。
    await wallet.reserve("tier", ["2"], 500, entry_min=20000)
    assert balances(wallet)[2] == 19500


@pytest.mark.asyncio
async def test_reserve_and_settle_are_idempotent(wallet):
    await wallet.reserve("r1", ["1", "2"], 100)
    await wallet.reserve("r1", ["1", "2"], 100)
    assert balances(wallet)[1] == 0
    await wallet.settle("r1", {"1": 150, "2": 50})
    await wallet.settle("r1", {"1": 150, "2": 50})
    assert balances(wallet) == {1: 150, 2: 19950, 3: 50}
    with pytest.raises(ValueError, match="不同金额"):
        await wallet.settle("r1", {"1": 160, "2": 40})
    assert balances(wallet)[1] == 150


@pytest.mark.asyncio
async def test_concurrent_tables_cannot_overdraw(wallet):
    results = await asyncio.gather(
        wallet.reserve("r1", ["1"], 100),
        wallet.reserve("r2", ["1"], 100),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, wallet_module.InsufficientTableBalance) for result in results) == 1
    assert balances(wallet)[1] == 0


@pytest.mark.asyncio
async def test_transaction_failure_leaves_settlement_retryable(wallet):
    await wallet.reserve("r1", ["1", "2"], 100)
    with sqlite3.connect(wallet.db_path) as connection:
        connection.executescript("""
            CREATE TRIGGER fail_credit BEFORE INSERT ON coin_transactions
            WHEN NEW.user_id = 2 AND NEW.amount > 0
            BEGIN SELECT RAISE(ABORT, '模拟流水失败'); END;
        """)
    with pytest.raises(sqlite3.IntegrityError):
        await wallet.settle("r1", {"1": 150, "2": 50})
    assert balances(wallet)[1] == 0
    with sqlite3.connect(wallet.db_path) as connection:
        assert connection.execute("SELECT count(*) FROM table_game_escrow WHERE status='settled'").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_credit")
    await wallet.settle("r1", {"1": 150, "2": 50})
    assert balances(wallet)[1] == 150


@pytest.mark.asyncio
async def test_restart_refunds_only_unfinished_rounds_once(wallet):
    await wallet.reserve("r1", ["1"], 100)
    await wallet.reserve("r2", ["2"], 500)
    await wallet.settle("r2", {"2": 510})
    assert await wallet.recover() == 1
    assert await wallet.recover() == 0
    assert balances(wallet) == {1: 100, 2: 20010, 3: 50}
    with pytest.raises(ValueError, match="已退款"):
        await wallet.settle("r1", {"1": 100})


@pytest.mark.asyncio
async def test_invalid_settlement_rolls_back_every_credit(wallet):
    await wallet.reserve("r1", ["1", "2"], 100)
    with pytest.raises(ValueError):
        await wallet.settle("r1", {"1": 150, "2": -1})
    assert balances(wallet)[1] == 0


def test_traditional_settlement_caps_only_at_chosen_buy_in():
    from types import SimpleNamespace

    service = service_module.TableService()
    room = service_module.GameTable("ROOM", "landlord", "1", "multi", 100)
    room.players = {uid: service_module.TablePlayer(uid, uid, "") for uid in ("1", "2", "3")}
    room.state = "finished"
    room.settlement_status = "reserved"
    room.escrow_key = "round"
    room.engine = SimpleNamespace(public_state=lambda _: {
        "players": [{"user_id": "1", "score_delta": -10000}, {"user_id": "2", "score_delta": 5000}, {"user_id": "3", "score_delta": 5000}]
    })
    service.rooms[room.room_id] = room
    assert service.settlement("ROOM") == ("round", {"1": 0, "2": 150, "3": 150})
    assert room.actual_settlement == {"1": -100, "2": 50, "3": 50}
    room.buy_in = 20000
    assert service.settlement("ROOM")[1] == {"1": 10000, "2": 25000, "3": 25000}


def test_settlement_remainders_preserve_whole_coin_total():
    from types import SimpleNamespace

    service = service_module.TableService()
    room = service_module.GameTable("ROOM", "landlord", "1", "multi", 101)
    room.players = {uid: service_module.TablePlayer(uid, uid, "") for uid in ("1", "2", "3")}
    room.state = "finished"
    room.settlement_status = "reserved"
    room.escrow_key = "round"
    room.engine = SimpleNamespace(public_state=lambda _: {
        "players": [{"user_id": "1", "score_delta": -10000}, {"user_id": "2", "score_delta": 5000}, {"user_id": "3", "score_delta": 5000}]
    })
    service.rooms[room.room_id] = room
    assert service.settlement("ROOM")[1] == {"1": 0, "2": 152, "3": 151}
    assert sum(room.actual_settlement.values()) == 0
