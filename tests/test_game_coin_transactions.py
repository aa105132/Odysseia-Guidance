"""真实 SQLite 验证普通下注与桌游托管之间的余额并发和事务回滚。"""

import asyncio
import importlib
import sqlite3
from types import SimpleNamespace

import pytest

coin_module = importlib.import_module("src.chat.features.odysseia_coin.service.coin_service")
wallet_module = importlib.import_module("src.chat.features.games.blackjack-web.table_wallet")
table_module = importlib.import_module("src.chat.features.games.blackjack-web.table_service")


@pytest.fixture
def account(monkeypatch, tmp_path):
    path = tmp_path / "coins.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE user_coins (user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL);
            CREATE TABLE coin_transactions (user_id INTEGER, amount INTEGER, reason TEXT);
            INSERT INTO user_coins VALUES (1, 100);
        """)
    async def execute(function, *args, **kwargs):
        return await asyncio.to_thread(function, *args, **kwargs)
    monkeypatch.setattr(coin_module, "chat_db_manager", SimpleNamespace(db_path=str(path), _execute=execute))
    return path, coin_module.CoinService(), wallet_module.TableWallet(str(path))


def read_account(path):
    with sqlite3.connect(path) as connection:
        return (
            connection.execute("SELECT balance FROM user_coins WHERE user_id=1").fetchone()[0],
            connection.execute("SELECT sum(amount) FROM coin_transactions").fetchone()[0] or 0,
        )


@pytest.mark.asyncio
async def test_two_bets_cannot_spend_the_same_balance(account):
    path, coins, _ = account
    results = await asyncio.gather(*(coins.remove_coins(1, 100, "并发下注") for _ in range(2)))
    assert results.count(0) == 1 and results.count(None) == 1
    assert read_account(path) == (0, -100)


@pytest.mark.asyncio
async def test_blackjack_bet_and_table_escrow_cannot_overdraw(account):
    path, coins, wallet = account
    results = await asyncio.gather(
        coins.remove_coins(1, 100, "黑杰克下注"),
        wallet.reserve("round", ["1"], 100),
        return_exceptions=True,
    )
    assert read_account(path) == (0, -100)
    assert results[0] is None or isinstance(results[1], wallet_module.InsufficientTableBalance)


@pytest.mark.asyncio
@pytest.mark.parametrize("credit", [False, True])
async def test_ledger_failure_rolls_back_balance(account, credit):
    path, coins, _ = account
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TRIGGER fail_ledger BEFORE INSERT ON coin_transactions
            BEGIN SELECT RAISE(ABORT, '模拟流水写入失败'); END;
        """)
    operation = coins.add_coins if credit else coins.remove_coins
    with pytest.raises(sqlite3.IntegrityError):
        await operation(1, 20, "故障回归")
    assert read_account(path) == (100, 0)


@pytest.mark.asyncio
async def test_concurrent_credit_and_debit_preserve_ledger(account):
    path, coins, _ = account
    await asyncio.gather(
        *(coins.add_coins(1, 5, "派彩") for _ in range(8)),
        *(coins.remove_coins(1, 3, "下注") for _ in range(8)),
    )
    assert read_account(path) == (116, 16)
    assert await coins.add_coins(2, 50, "新账户签到") == 50


def test_reconnect_does_not_extend_the_active_turn():
    now = [1000.0]
    service = table_module.TableService(clock=lambda: now[0])
    user = {"user_id": "1", "username": "玩家", "avatar_url": ""}
    created = service.create(user, "texas", "solo", True)
    room_id = created["room_id"]
    service.ready(room_id, "1", True)
    service.start(room_id, "1")
    room = service._room(room_id)
    room.last_turn = "1"
    room.turn_deadline = 1060
    now[0] += 30
    service.join(room_id, user)
    assert room.turn_deadline == 1060
    service.leave(room_id, "1")
    deadline = room.turn_deadline
    service.join(room_id, user)
    assert room.turn_deadline == deadline


def test_rejoining_abandoned_game_requires_leaving_other_room():
    service = table_module.TableService()
    user = {"user_id": "1", "username": "玩家", "avatar_url": ""}
    first = service.create(user, "texas", "solo", True)["room_id"]
    service.ready(first, "1", True)
    service.start(first, "1")
    service.leave(first, "1")
    service.create(user, "mahjong", "solo", True)
    with pytest.raises(ValueError, match="先离开"):
        service.join(first, user)
