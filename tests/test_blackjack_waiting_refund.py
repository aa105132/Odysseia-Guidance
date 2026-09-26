"""等待下注离线退款：真实 ASGI、隔离账户及临时 SQLite，不访问线上资金。"""
import asyncio
import importlib
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_blackjack_multiplayer_api import (
    api, single_api, HOST_ID, GUEST_ID, THIRD_ID, _client, _create_room,
    _start_round, _finish_round, _request,
)
from test_blackjack_statistics import real_stats


def clock(monkeypatch, environment):
    now = [1000.0]
    monkeypatch.setattr(environment.engine, "time", SimpleNamespace(
        time=environment.engine.time.time, monotonic=lambda: now[0],
    ))
    return now


@pytest.mark.asyncio
async def test_waiting_timeout_boundary_current_stake_and_no_llm(api, monkeypatch):
    now = clock(monkeypatch, api)
    api.module.game_bot_runner = None
    service = api.module.multiplayer_blackjack_service
    async with _client(api) as client:
        room_id = await _create_room(client, (HOST_ID,))
        api.module._bind_session_room("test-session", room_id)
        for amount in (100, 150, 75):
            assert (await _request(client, "bet", room_id=room_id, amount=amount)).status_code == 200
        now[0] += 299
        await api.module._tick_multiplayer_rooms()
        assert api.coins.balances[HOST_ID] == 925
        assert room_id in service._rooms
        now[0] += 1
        await api.module._tick_multiplayer_rooms()
        await api.module._tick_multiplayer_rooms()
        assert api.coins.balances[HOST_ID] == 1000
        assert room_id not in service._rooms
        assert "test-session" not in api.module.activity_room_bindings
        refunds = [e for e in api.coins.entries if "超时退还" in e[2]]
        assert len(refunds) == 1 and refunds[0][1] == 75
        assert not api.module.table_wallet.results


@pytest.mark.asyncio
async def test_outer_background_loop_calls_multiplayer_tick_without_llm(api, monkeypatch):
    api.module.game_bot_runner = None
    tick = AsyncMock()
    monkeypatch.setattr(api.module, "_tick_multiplayer_rooms", tick)
    calls = 0

    async def sleep_once(seconds):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(api.module, "asyncio", SimpleNamespace(sleep=sleep_once))
    with pytest.raises(asyncio.CancelledError):
        await api.module._tick_tables()
    tick.assert_awaited_once()


@pytest.mark.asyncio
async def test_only_own_authenticated_poll_renews_lease_and_host_transfers(api, monkeypatch):
    now = clock(monkeypatch, api)
    service = api.module.multiplayer_blackjack_service
    async with _client(api) as client:
        room_id = await _create_room(client)
        for uid in (HOST_ID, GUEST_ID):
            assert (await _request(client, "bet", uid, room_id=room_id, amount=100)).status_code == 200
        for _ in range(3):
            now[0] += 100
            assert (await _request(client, room_id, GUEST_ID, method="GET")).status_code == 200
            assert (await _request(client, room_id, THIRD_ID, method="GET")).status_code == 403
            service.list_rooms(HOST_ID)
            service.get_room_state(room_id)  # 后台读取不是人的心跳
            await api.module._tick_multiplayer_rooms()
        room = service._rooms[room_id]
        assert set(room.players) == {GUEST_ID}
        assert room.host_user_id == GUEST_ID
        assert api.coins.balances[HOST_ID] == 1000
        assert api.coins.balances[GUEST_ID] == 900
        now[0] += 300
        await api.module._tick_multiplayer_rooms()
        assert api.coins.balances[GUEST_ID] == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("heartbeat_action", ["poll", "join", "ready", "continue-ready", "bet"])
async def test_reconnection_and_own_actions_extend_grace(api, monkeypatch, heartbeat_action):
    now = clock(monkeypatch, api)
    service = api.module.multiplayer_blackjack_service
    async with _client(api) as client:
        room_id = await _create_room(client, (HOST_ID,))
        await _request(client, "bet", room_id=room_id, amount=100)
        now[0] += 299
        if heartbeat_action == "poll":
            response = await _request(client, room_id, method="GET")
        else:
            payload = {"room_id": room_id}
            if heartbeat_action == "ready":
                payload["ready"] = True
            if heartbeat_action == "bet":
                payload["amount"] = 100
            response = await _request(client, heartbeat_action, **payload)
        assert response.status_code == 200, response.text
        now[0] += 299
        await api.module._tick_multiplayer_rooms()
        assert room_id in service._rooms
        assert api.coins.balances[HOST_ID] == 900
        now[0] += 1
        await api.module._tick_multiplayer_rooms()
        assert api.coins.balances[HOST_ID] == 1000


@pytest.mark.asyncio
async def test_bot_excluded_and_room_closes_after_last_human_refund(api, monkeypatch):
    now = clock(monkeypatch, api)
    service = api.module.multiplayer_blackjack_service
    async with _client(api) as client:
        room_id = await _create_room(client, (HOST_ID,))
        await _request(client, "bet", room_id=room_id, amount=100)
        assert (await _request(client, "bot", room_id=room_id, include_yueyue=True)).status_code == 200
        now[0] += 300
        assert service.expired_waiting_players(room_id) == [HOST_ID]
        await api.module._tick_multiplayer_rooms()
        assert room_id not in service._rooms
        assert all(uid != service.YUEYUE_USER_ID for uid, _, _ in api.coins.entries)
        assert api.coins.balances[HOST_ID] == 1000


@pytest.mark.asyncio
async def test_no_bet_no_credit_and_active_round_not_waiting_refunded(api, monkeypatch):
    now = clock(monkeypatch, api)
    service = api.module.multiplayer_blackjack_service
    async with _client(api) as client:
        room_id = await _create_room(client)
        now[0] += 1000
        await api.module._tick_multiplayer_rooms()
        assert not api.coins.entries
        await _start_round(client, room_id)
        now[0] += 1000
        assert service.expired_waiting_players(room_id) == []
        await api.module._tick_multiplayer_rooms()
        assert not api.module.table_wallet.waiting_refunds
        assert api.coins.balances[HOST_ID] == 900
        response = await _request(client, "leave", GUEST_ID, room_id=room_id)
        assert response.status_code == 200
        assert api.coins.balances[GUEST_ID] == 900
        assert any(uid == str(GUEST_ID) and profit == -100
                   for (_, uid), profit in api.module.table_wallet.results.items())


@pytest.mark.asyncio
async def test_continue_next_round_timeout_uses_fresh_key(api, monkeypatch):
    now = clock(monkeypatch, api)
    service = api.module.multiplayer_blackjack_service
    async with _client(api) as client:
        room_id = await _create_room(client)
        await _start_round(client, room_id)
        old_key = service._rooms[room_id].players[HOST_ID].waiting_refund_key
        await _finish_round(client, room_id)
        assert (await _request(client, "continue-ready", room_id=room_id)).status_code == 200
        assert service._rooms[room_id].players[HOST_ID].waiting_refund_key != old_key
        assert api.coins.balances[HOST_ID] == 1000
        now[0] += 300
        await api.module._tick_multiplayer_rooms()
        assert api.coins.balances[HOST_ID] == 1100
        assert len(api.module.table_wallet.waiting_refunds) == 1
        assert len(api.module.table_wallet.results) == 2


@pytest.mark.asyncio
async def test_explicit_leave_races_timeout_refunds_once(api, monkeypatch):
    now = clock(monkeypatch, api)
    async with _client(api) as client:
        room_id = await _create_room(client, (HOST_ID,))
        await _request(client, "bet", room_id=room_id, amount=100)
        now[0] += 300
        await asyncio.gather(api.module._tick_multiplayer_rooms(),
                             _request(client, "leave", room_id=room_id))
        assert api.coins.balances[HOST_ID] == 1000
        assert len(api.module.table_wallet.waiting_refunds) == 1
        assert sum(amount > 0 for _, amount, _ in api.coins.entries) == 1


@pytest.mark.asyncio
async def test_failed_refund_frozen_until_retry_and_other_members_not_blocked(api, monkeypatch):
    now = clock(monkeypatch, api)
    service = api.module.multiplayer_blackjack_service
    async with _client(api) as client:
        room_id = await _create_room(client)
        for uid in (HOST_ID, GUEST_ID):
            await _request(client, "bet", uid, room_id=room_id, amount=100)
            await _request(client, "ready", uid, room_id=room_id, ready=True)
        now[0] += 300
        api.coins.fail_next_credit_for = HOST_ID
        await api.module._tick_multiplayer_rooms()
        room = service._rooms[room_id]
        assert set(room.players) == {HOST_ID}
        assert room.players[HOST_ID].waiting_refund_pending
        key = room.players[HOST_ID].waiting_refund_key
        assert api.coins.balances[HOST_ID] == 900
        assert api.coins.balances[GUEST_ID] == 1000
        count = len(api.coins.entries)
        for action, extra in (("bet", {"amount": 50}), ("bet", {"amount": 200}),
                              ("continue-ready", {}), ("ready", {"ready": True})):
            response = await _request(client, action, room_id=room_id, **extra)
            assert response.status_code == 400, response.text
        assert len(api.coins.entries) == count
        assert room.players[HOST_ID].waiting_refund_key == key
        with pytest.raises(ValueError, match="退款"):
            service.start_round(room_id, HOST_ID)
        service.heartbeat(room_id, HOST_ID)
        assert service.expired_waiting_players(room_id) == [HOST_ID]
        await api.module._tick_multiplayer_rooms()
        assert api.coins.balances[HOST_ID] == 1000
        assert room_id not in service._rooms


@pytest.mark.asyncio
async def test_start_checks_expired_guest_before_dealing(api, monkeypatch):
    now = clock(monkeypatch, api)
    service = api.module.multiplayer_blackjack_service
    async with _client(api) as client:
        room_id = await _create_room(client)
        for uid in (HOST_ID, GUEST_ID):
            await _request(client, "bet", uid, room_id=room_id, amount=100)
            await _request(client, "ready", uid, room_id=room_id, ready=True)
        now[0] += 300
        response = await _request(client, "start", room_id=room_id)
        assert response.status_code == 200, response.text
        assert set(service._rooms[room_id].players) == {HOST_ID}
        assert api.coins.balances[GUEST_ID] == 1000
        assert api.coins.balances[HOST_ID] == 900


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["lost-response", "cancelled", "removal-failure"])
async def test_committed_sqlite_refund_retry_never_duplicates(real_stats, monkeypatch, mode):
    now = clock(monkeypatch, real_stats)
    app = real_stats.module
    service = app.multiplayer_blackjack_service
    wallet = app.table_wallet
    async with _client(real_stats) as client:
        room_id = await _create_room(client, (HOST_ID,))
        await _request(client, "bet", room_id=room_id, amount=100)
        original = wallet.refund_blackjack_waiting
        original_leave = service.leave_room

        async def interrupted(*args):
            await original(*args)
            if mode == "cancelled":
                raise asyncio.CancelledError
            raise RuntimeError("提交成功后响应中断")

        if mode == "removal-failure":
            def fail_leave(*args):
                raise RuntimeError("席位清理失败")
            monkeypatch.setattr(service, "leave_room", fail_leave)
        else:
            monkeypatch.setattr(wallet, "refund_blackjack_waiting", interrupted)
        now[0] += 300
        if mode == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await app._tick_multiplayer_rooms()
        else:
            await app._tick_multiplayer_rooms()
        assert await app.coin_service.get_balance(HOST_ID) == 1000
        assert service._rooms[room_id].players[HOST_ID].waiting_refund_pending
        # 重新创建钱包对象证明幂等标记来自 SQLite，不是对象内缓存。
        app.table_wallet = type(wallet)(wallet.db_path)
        monkeypatch.setattr(service, "leave_room", original_leave)
        response = await _request(client, "leave", room_id=room_id)
        assert response.status_code == 200, response.text
        await app._tick_multiplayer_rooms()
        assert await app.coin_service.get_balance(HOST_ID) == 1000
        with sqlite3.connect(wallet.db_path) as connection:
            assert connection.execute("SELECT count(*) FROM blackjack_waiting_refunds").fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM coin_transactions WHERE amount > 0").fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM table_game_results").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_sqlite_refund_rollback_concurrency_and_argument_integrity(tmp_path):
    wallet_type = importlib.import_module("src.chat.features.games.blackjack-web.table_wallet").TableWallet
    path = tmp_path / "waiting-refund.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE user_coins (user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL);
            CREATE TABLE coin_transactions (user_id INTEGER, amount INTEGER, reason TEXT);
            INSERT INTO user_coins VALUES (1, 900);
        """)
    wallet = wallet_type(str(path))
    await wallet.statistics("1")  # 建表后注入提交前失败
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TRIGGER fail_refund BEFORE INSERT ON blackjack_waiting_refunds BEGIN SELECT RAISE(ABORT, 'fail'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await wallet.refund_blackjack_waiting("key", 1, 100, "ROOM", "等待超时退款")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT balance FROM user_coins").fetchone()[0] == 900
        assert connection.execute("SELECT count(*) FROM coin_transactions").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_refund")
    await asyncio.gather(*(wallet.refund_blackjack_waiting("key", 1, 100, "ROOM", "等待超时退款") for _ in range(8)))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT balance FROM user_coins").fetchone()[0] == 1000
        assert connection.execute("SELECT count(*) FROM coin_transactions").fetchone()[0] == 1
    for uid, amount, room in ((1, 101, "ROOM"), (2, 100, "ROOM"), (1, 100, "OTHER")):
        with pytest.raises(ValueError, match="不一致"):
            await wallet.refund_blackjack_waiting("key", uid, amount, room, "退款")
    with pytest.raises(ValueError, match="账户不存在"):
        await wallet.refund_blackjack_waiting("missing", 2, 100, "ROOM", "退款")
    for amount in (True, 0, -1, 1.2):
        with pytest.raises(ValueError):
            await wallet.refund_blackjack_waiting("invalid", 1, amount, "ROOM", "退款")
    assert (await wallet.statistics("1", "blackjack"))["stats"]["rounds"] == 0


@pytest.mark.asyncio
async def test_rejoin_and_rebet_same_room_gets_distinct_refund(api, monkeypatch):
    now = clock(monkeypatch, api)
    service = api.module.multiplayer_blackjack_service
    async with _client(api) as client:
        room_id = await _create_room(client)
        keys = []
        for amount in (100, 200):
            assert (await _request(client, "join", room_id=room_id)).status_code == 200
            assert (await _request(client, "bet", room_id=room_id, amount=amount)).status_code == 200
            keys.append(service._rooms[room_id].players[HOST_ID].waiting_refund_key)
            now[0] += 300
            await api.module._tick_multiplayer_rooms()
            assert api.coins.balances[HOST_ID] == 1000
            assert HOST_ID not in service._rooms[room_id].players
        assert len(set(keys)) == 2
        assert len(api.module.table_wallet.waiting_refunds) == 2
        assert [amount for _, amount, reason in api.coins.entries if "超时退还" in reason] == [100, 200]


@pytest.mark.asyncio
async def test_running_round_background_settles_without_llm_or_client_poll(api, monkeypatch):
    now = clock(monkeypatch, api)
    service = api.module.multiplayer_blackjack_service
    api.module.game_bot_runner = None
    async with _client(api) as client:
        room_id = await _create_room(client)
        await _start_round(client, room_id)
        room = service._rooms[room_id]
        # 只使行动计时到期，不发任何客户端请求。
        for _ in range(3):
            room.turn_deadline = 0
            await api.module._tick_multiplayer_rooms()
        assert room.state == "finished" and room.payouts_committed
        assert api.coins.balances[HOST_ID] == api.coins.balances[GUEST_ID] == 1100
        assert len(api.module.table_wallet.results) == 2
        now[0] += 10000
        await api.module._tick_multiplayer_rooms()
        assert not api.module.table_wallet.waiting_refunds
        assert api.coins.balances[HOST_ID] == api.coins.balances[GUEST_ID] == 1100


@pytest.mark.asyncio
async def test_explicit_leave_failure_retries_before_offline_timeout(api, monkeypatch):
    clock(monkeypatch, api)
    service = api.module.multiplayer_blackjack_service
    async with _client(api) as client:
        room_id = await _create_room(client, (HOST_ID,))
        await _request(client, "bet", room_id=room_id, amount=100)
        api.coins.fail_next_credit_for = HOST_ID
        response = await _request(client, "leave", room_id=room_id)
        assert response.status_code == 500
        assert service._rooms[room_id].players[HOST_ID].waiting_refund_pending
        assert api.coins.balances[HOST_ID] == 900
        await api.module._tick_multiplayer_rooms()
        assert api.coins.balances[HOST_ID] == 1000
        assert room_id not in service._rooms


@pytest.mark.asyncio
async def test_start_does_not_deal_if_expired_guest_refund_failed(api, monkeypatch):
    now = clock(monkeypatch, api)
    service = api.module.multiplayer_blackjack_service
    async with _client(api) as client:
        room_id = await _create_room(client)
        for uid in (HOST_ID, GUEST_ID):
            await _request(client, "bet", uid, room_id=room_id, amount=100)
            await _request(client, "ready", uid, room_id=room_id, ready=True)
        now[0] += 300
        api.coins.fail_next_credit_for = GUEST_ID
        response = await _request(client, "start", room_id=room_id)
        assert response.status_code == 400 and "退款" in response.text
        room = service._rooms[room_id]
        assert room.state == "waiting" and not room.dealer_hand
        assert room.players[GUEST_ID].waiting_refund_pending
        assert api.coins.balances[GUEST_ID] == 900
        await api.module._tick_multiplayer_rooms()
        assert api.coins.balances[GUEST_ID] == 1000
        assert room.state == "waiting"


@pytest.mark.asyncio
async def test_start_and_timeout_race_never_refunds_a_live_stake(api, monkeypatch):
    now = clock(monkeypatch, api)
    service = api.module.multiplayer_blackjack_service
    async with _client(api) as client:
        room_id = await _create_room(client)
        for uid in (HOST_ID, GUEST_ID):
            await _request(client, "bet", uid, room_id=room_id, amount=100)
            await _request(client, "ready", uid, room_id=room_id, ready=True)
        now[0] += 300
        await _request(client, room_id, method="GET")  # 在线房主，离线客人
        _, response = await asyncio.gather(api.module._tick_multiplayer_rooms(),
                                          _request(client, "start", room_id=room_id))
        assert response.status_code == 200, response.text
        room = service._rooms[room_id]
        assert room.state == "playing" and set(room.players) == {HOST_ID}
        assert api.coins.balances[HOST_ID] == 900
        assert api.coins.balances[GUEST_ID] == 1000
        assert len(api.module.table_wallet.waiting_refunds) == 1
