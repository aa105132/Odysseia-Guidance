"""桌游真实 ASGI 联机测试：四引擎、房间服务和 SQLite 灵石账本一起运行。"""

import asyncio
import importlib.util
import sqlite3
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Request


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "src" / "chat" / "features" / "games" / "blackjack-web"
USER_IDS = tuple(str(123456789012345678 + index) for index in range(9))
INITIAL_BALANCE = 2000


class FakeClock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def advance(self, seconds=61):
        self.value += seconds


class DatabaseCoins:
    """余额查询使用与实际桌游钱包相同的临时 SQLite 文件。"""

    def __init__(self, path):
        self.path = str(path)

    async def get_balance(self, user_id):
        with sqlite3.connect(self.path) as connection:
            row = connection.execute("SELECT balance FROM user_coins WHERE user_id = ?", (int(user_id),)).fetchone()
        return row[0] if row else None


def _load_module(monkeypatch, name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api(monkeypatch, tmp_path):
    db_path = tmp_path / "table_api.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE user_coins (user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL)")
        connection.execute("CREATE TABLE coin_transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, reason TEXT NOT NULL)")
        connection.executemany("INSERT INTO user_coins VALUES (?, ?)", [(int(uid), INITIAL_BALANCE) for uid in USER_IDS])

    coins = DatabaseCoins(db_path)
    dependencies = {
        "src.chat.features.odysseia_coin.service.coin_service": {"coin_service": coins},
        "src.chat.features.games.services.blackjack_service": {"blackjack_service": SimpleNamespace()},
        "src.chat.utils.database": {"chat_db_manager": SimpleNamespace(db_path=str(db_path), update_blackjack_net_win_loss=AsyncMock())},
        "src.dashboard.service_registry": {"service_registry": SimpleNamespace(bot=None)},
    }
    for name, attributes in dependencies.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.syspath_prepend(str(ROOT))
    _load_module(monkeypatch, "multiplayer_service", WEB_DIR / "multiplayer_service.py")
    module = _load_module(monkeypatch, "tables_api_under_test", WEB_DIR / "app.py")
    # ASGI 测试不得读取开发机凭据访问真实 Discord；资料回填测试单独提供模拟传输。
    monkeypatch.setattr(module, "_resolve_discord_bot_token", lambda: "")
    clock = FakeClock()
    module.table_service = module._table_module.TableService(clock=clock)
    module.table_wallet = module._wallet_module.TableWallet(str(db_path))
    # 仅固定洗牌种子，真正的规则、AI 和钱包代码均不替换。
    monkeypatch.setattr(module._table_module.random, "SystemRandom", lambda: SimpleNamespace(randrange=lambda _: 42))

    async def test_profile(request: Request):
        uid = request.headers["X-Test-User"]
        return {"user_id": int(uid), "username": f"玩家{uid[-2:]}", "avatar_url": "/character/normal.webp", "is_dev": False}

    module.app.dependency_overrides[module.get_current_user_profile] = test_profile
    return SimpleNamespace(module=module, clock=clock, db_path=db_path, coins=coins)


async def _clients(stack, api, user_ids):
    clients = {}
    for uid in user_ids:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.module.app, raise_app_exceptions=False),
            base_url="http://table.test",
            headers={"X-Test-User": uid},
        )
        clients[uid] = await stack.enter_async_context(client)
    return clients


async def _post(client, endpoint, **payload):
    return await client.post(f"/api/tables/{endpoint}", json=payload)


def _room(response):
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    return payload["room"]


async def _create_and_join(clients, game_type, room_tier="beginner", **settings):
    ids = list(clients)
    room = _room(await _post(clients[ids[0]], "create", game_type=game_type, mode="multi", include_yueyue=False, room_tier=room_tier, **settings))
    for uid in ids[1:]:
        room = _room(await _post(clients[uid], "join", room_id=room["room_id"]))
    return room


async def _ready_and_start(clients, room_id):
    for client in clients.values():
        _room(await _post(client, "ready", room_id=room_id, ready=True))
    return _room(await _post(next(iter(clients.values())), "start", room_id=room_id))


async def _finish_through_requests(api, clients, room):
    room_id = room["room_id"]
    for _ in range(650):
        if room["state"] == "finished":
            return room
        uid = room["game"]["current_player_id"]
        member = next(player for player in room["players"] if player["user_id"] == uid)
        if uid in clients and member["connected"] and not member["is_bot"]:
            suggestion = api.module.table_service._room(room_id).engine.suggest_action(uid)
            room = _room(await _post(clients[uid], "action", room_id=room_id, expected_revision=room["revision"], **suggestion))
        else:
            api.clock.advance()
            room = _room(await next(iter(clients.values())).get(f"/api/tables/{room_id}"))
    pytest.fail(f"{room['game_type']} 在 650 次合法动作后仍未结束")


def _balances(api, ids):
    with sqlite3.connect(api.db_path) as connection:
        rows = dict(connection.execute("SELECT user_id, balance FROM user_coins"))
    return {uid: rows[int(uid)] for uid in ids}


def _ledger(api):
    with sqlite3.connect(api.db_path) as connection:
        return connection.execute("SELECT user_id, stake, payout, status FROM table_game_escrow ORDER BY user_id").fetchall()


def test_auto_start_reserves_once_when_last_player_gets_ready(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:2]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, "texas")
            rid = room["room_id"]
            configured = _room(await _post(clients[ids[0]], "settings", room_id=rid, auto_start_when_ready=True))
            assert configured["auto_start_when_ready"] is True
            waiting = _room(await _post(clients[ids[0]], "ready", room_id=rid, ready=True))
            assert waiting["state"] == "waiting"
            results = await asyncio.gather(*[
                _post(clients[ids[1]], "ready", room_id=rid, ready=True) for _ in range(2)
            ])
            assert sorted(result.status_code for result in results) == [200, 400]
            playing = next(_room(result) for result in results if result.status_code == 200)
            assert playing["state"] == "playing"
            assert playing["round_number"] == 1
            assert _balances(api, ids) == {uid: INITIAL_BALANCE - 100 for uid in ids}
            assert len(_ledger(api)) == 2
            finished = await _finish_through_requests(api, clients, playing)
            assert finished["settlement_status"] == "settled"
            assert all(not player["is_ready"] for player in finished["players"])
            for client in clients.values():
                room = _room(await _post(client, "ready", room_id=rid, ready=True))
            assert room["state"] == "playing"
            assert room["round_number"] == 2
            assert len(_ledger(api)) == 4
    asyncio.run(scenario())


def test_auto_start_keeps_reserved_round_when_post_start_sync_fails(api, monkeypatch):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:2]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, "texas", auto_start_when_ready=True)
            rid = room["room_id"]
            _room(await _post(clients[ids[0]], "ready", room_id=rid, ready=True))
            original = api.module._settle_table

            async def fail_after_reserve(room_id):
                if api.module.table_service._room(room_id).state == "playing":
                    raise RuntimeError("模拟冻结成功后的同步异常")
                return await original(room_id)

            monkeypatch.setattr(api.module, "_settle_table", fail_after_reserve)
            response = await _post(clients[ids[1]], "ready", room_id=rid, ready=True)
            assert response.status_code == 500
            current = api.module.table_service._room(rid)
            assert current.state == "playing"
            assert current.escrow_key
            assert current.round_number == 1
            assert len(_ledger(api)) == 2
            assert _balances(api, ids) == {uid: INITIAL_BALANCE - 100 for uid in ids}
            monkeypatch.setattr(api.module, "_settle_table", original)
            recovered = _room(await clients[ids[0]].get(f"/api/tables/{rid}"))
            assert recovered["state"] == "playing"
            assert recovered["round_number"] == 1
    asyncio.run(scenario())


def test_auto_start_insufficient_balance_rolls_back_ready_and_escrow(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:2]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, "texas", auto_start_when_ready=True)
            rid = room["room_id"]
            _room(await _post(clients[ids[0]], "ready", room_id=rid, ready=True))
            with sqlite3.connect(api.db_path) as connection:
                connection.execute("UPDATE user_coins SET balance = 50 WHERE user_id = ?", (int(ids[1]),))
            failed = await _post(clients[ids[1]], "ready", room_id=rid, ready=True)
            assert failed.status_code == 402
            waiting = _room(await clients[ids[0]].get(f"/api/tables/{rid}"))
            assert waiting["state"] == "waiting"
            assert waiting["round_number"] == 0
            assert [player["is_ready"] for player in waiting["players"]] == [True, False]
            with sqlite3.connect(api.db_path) as connection:
                escrow_exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'table_game_escrow'"
                ).fetchone()
            assert not escrow_exists or _ledger(api) == []
            assert _balances(api, ids) == {ids[0]: INITIAL_BALANCE, ids[1]: 50}
    asyncio.run(scenario())


def test_enabling_auto_start_for_ready_room_starts_and_disabling_keeps_manual_mode(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:2]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, "texas")
            rid = room["room_id"]
            for client in clients.values():
                waiting = _room(await _post(client, "ready", room_id=rid, ready=True))
                assert waiting["state"] == "waiting"
            denied = await _post(clients[ids[1]], "settings", room_id=rid, auto_start_when_ready=True)
            assert denied.status_code == 403
            playing = _room(await _post(clients[ids[0]], "settings", room_id=rid, auto_start_when_ready=True))
            assert playing["state"] == "playing"
            await _finish_through_requests(api, clients, playing)
            _room(await _post(clients[ids[0]], "settings", room_id=rid, auto_start_when_ready=False))
            for client in clients.values():
                waiting = _room(await _post(client, "ready", room_id=rid, ready=True))
            assert waiting["state"] == "finished"
            assert waiting["auto_start_when_ready"] is False
            assert waiting["round_number"] == 1
            playing = _room(await _post(clients[ids[0]], "start", room_id=rid))
            assert playing["round_number"] == 2
    asyncio.run(scenario())


def test_kick_requires_host_revokes_access_and_preserves_settled_balance(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:2]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, "texas")
            rid = room["room_id"]
            denied = await _post(clients[ids[1]], "kick", room_id=rid, target_user_id=ids[0])
            assert denied.status_code == 403
            room = await _ready_and_start(clients, rid)
            denied = await _post(clients[ids[0]], "kick", room_id=rid, target_user_id=ids[1])
            assert denied.status_code == 400
            await _finish_through_requests(api, clients, room)
            balances = _balances(api, ids)
            kicked = _room(await _post(clients[ids[0]], "kick", room_id=rid, target_user_id=ids[1]))
            assert [player["user_id"] for player in kicked["players"]] == [ids[0]]
            assert _balances(api, ids) == balances
            denied = await clients[ids[1]].get(f"/api/tables/{rid}")
            assert denied.status_code == 200
            assert denied.json()["room"] is None
            assert denied.json()["room_exit_reason"] == "kicked"
            denied = await _post(clients[ids[1]], "ready", room_id=rid, ready=True)
            assert denied.status_code == 200
            assert denied.json()["room"] is None
            assert denied.json()["room_exit_reason"] == "kicked"
    asyncio.run(scenario())


@pytest.mark.parametrize("game_type,count", [("texas", 2), ("texas", 8), ("golden_flower", 2), ("landlord", 3), ("mahjong", 4)])
def test_real_clients_finish_each_game_with_private_hands_and_atomic_settlement(api, game_type, count):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:count]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, game_type)
            room = await _ready_and_start(clients, room["room_id"])
            assert room["stake"] == 100
            assert room["settlement_status"] == "reserved"
            assert _balances(api, ids) == {uid: INITIAL_BALANCE - 100 for uid in ids}
            for uid, client in clients.items():
                snapshot = _room(await client.get(f"/api/tables/{room['room_id']}"))
                assert snapshot["revision"] == room["revision"]
                assert snapshot["game"]["current_player_id"] == room["game"]["current_player_id"]
                for player in snapshot["game"]["players"]:
                    assert isinstance(player["user_id"], str)
                    if player["user_id"] != uid or game_type == "golden_flower":
                        assert player["hand"] == []
                    else:
                        assert player["hand"]
            finished = await _finish_through_requests(api, clients, room)
            assert finished["settlement_status"] == "settled"
            actual = finished["actual_settlement"]
            assert sum(actual.values()) == 0
            assert _balances(api, ids) == {uid: INITIAL_BALANCE + actual[uid] for uid in ids}
            rows = _ledger(api)
            assert len(rows) == count
            assert all(row[1] == 100 and row[3] == "settled" for row in rows)
            balances = _balances(api, ids)
            for client in clients.values():
                for _ in range(2):
                    repeated = _room(await client.get(f"/api/tables/{room['room_id']}"))
                    assert repeated["actual_settlement"] == actual
            assert _balances(api, ids) == balances
            with sqlite3.connect(api.db_path) as connection:
                assert connection.execute("SELECT SUM(amount) FROM coin_transactions").fetchone()[0] == 0

    asyncio.run(scenario())


def test_custom_loss_limit_500_is_reserved_and_refunded_from_actual_poker_result(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:2]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, "texas", room_tier="custom", loss_limit=500)
            room = await _ready_and_start(clients, room["room_id"])
            assert room["game"]["buy_in"] == 500
            assert [player["stack"] for player in room["game"]["players"]] == [499, 498]
            assert _balances(api, ids) == {uid: 1500 for uid in ids}
            room = _room(await _post(clients[ids[0]], "action", room_id=room["room_id"], action="fold", expected_revision=room["revision"]))
            assert room["actual_settlement"] == {ids[0]: -1, ids[1]: 1}
            assert _balances(api, ids) == {ids[0]: 1999, ids[1]: 2001}
            assert [row[1] for row in _ledger(api)] == [500, 500]

    asyncio.run(scenario())


def test_one_player_insufficient_balance_rolls_back_every_wallet_and_room(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:2]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, "texas", room_tier="custom", loss_limit=500)
            for client in clients.values():
                _room(await _post(client, "ready", room_id=room["room_id"], ready=True))
            before = _room(await clients[ids[0]].get(f"/api/tables/{room['room_id']}"))
            with sqlite3.connect(api.db_path) as connection:
                connection.execute("UPDATE user_coins SET balance=499 WHERE user_id=?", (int(ids[1]),))
            response = await _post(clients[ids[0]], "start", room_id=room["room_id"])
            assert response.status_code == 402, response.text
            assert ids[1] in response.json()["detail"]
            after = _room(await clients[ids[0]].get(f"/api/tables/{room['room_id']}"))
            assert after == before
            assert _balances(api, ids) == {ids[0]: INITIAL_BALANCE, ids[1]: 499}
            with sqlite3.connect(api.db_path) as connection:
                assert connection.execute("SELECT COUNT(*) FROM coin_transactions").fetchone()[0] == 0
                escrow_exists = connection.execute("SELECT name FROM sqlite_master WHERE name='table_game_escrow'").fetchone()
                if escrow_exists:
                    assert connection.execute("SELECT COUNT(*) FROM table_game_escrow").fetchone()[0] == 0

    asyncio.run(scenario())


def test_concurrent_stale_actions_only_execute_once_and_poll_matches(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:2]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, "texas")
            room = await _ready_and_start(clients, room["room_id"])
            responses = await asyncio.gather(*(
                _post(clients[ids[0]], "action", room_id=room["room_id"], action="call", expected_revision=room["revision"])
                for _ in range(2)
            ))
            assert sorted(response.status_code for response in responses) == [200, 409]
            accepted = _room(next(response for response in responses if response.status_code == 200))
            current = _room(await clients[ids[0]].get(f"/api/tables/{room['room_id']}"))
            assert current == accepted
            assert current["game"]["pot"] == 4
            assert current["game"]["current_player_id"] == ids[1]
            assert _balances(api, ids) == {uid: 1900 for uid in ids}

    asyncio.run(scenario())


def test_leaving_host_transfers_control_and_playing_seat_reconnects(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:3]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, "texas")
            room = await _ready_and_start(clients, room["room_id"])
            room_id = room["room_id"]
            response = await _post(clients[ids[0]], "leave", room_id=room_id)
            assert response.status_code == 200 and response.json()["room"] is None
            snapshot = _room(await clients[ids[1]].get(f"/api/tables/{room_id}"))
            assert snapshot["host_user_id"] == ids[1]
            assert snapshot["players"][0]["connected"] is False
            denied = await _post(clients[ids[0]], "action", room_id=room_id, action="call")
            assert denied.status_code == 403
            api.clock.advance(1)
            progressed = _room(await clients[ids[1]].get(f"/api/tables/{room_id}"))
            assert progressed["revision"] > snapshot["revision"]
            rejoined = _room(await _post(clients[ids[0]], "join", room_id=room_id))
            assert rejoined["players"][0]["connected"] is True
            assert len(rejoined["players"]) == 3
            finished = await _finish_through_requests(api, clients, rejoined)
            assert finished["settlement_status"] == "settled"
            assert sum(_balances(api, ids).values()) == INITIAL_BALANCE * 3

    asyncio.run(scenario())


@pytest.mark.parametrize("game_type,count", [("texas", 2), ("golden_flower", 2), ("landlord", 3), ("mahjong", 4)])
def test_solo_yueyue_and_companions_finish_using_clock_driven_turns(api, game_type, count):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:1])
            client = clients[USER_IDS[0]]
            room = _room(await _post(client, "create", game_type=game_type, mode="solo", include_yueyue=True, room_tier="beginner"))
            assert len(room["players"]) == count
            assert room["include_yueyue"]
            room = await _ready_and_start(clients, room["room_id"])
            finished = await _finish_through_requests(api, clients, room)
            assert finished["settlement_status"] == "settled"
            assert len(_ledger(api)) == 1
            assert _balances(api, USER_IDS[:1])[USER_IDS[0]] == INITIAL_BALANCE + finished["actual_settlement"][USER_IDS[0]]
            assert sum(finished["actual_settlement"].values()) == 0

    asyncio.run(scenario())


def test_outsider_cannot_view_or_act_and_nonhost_cannot_reconfigure(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:3])
            members = {uid: clients[uid] for uid in USER_IDS[:2]}
            room = await _create_and_join(members, "texas")
            outsider = clients[USER_IDS[2]]
            assert (await outsider.get(f"/api/tables/{room['room_id']}")).status_code == 403
            for endpoint, payload in [("ready", {"ready": True}), ("action", {"action": "fold"}), ("leave", {})]:
                response = await _post(outsider, endpoint, room_id=room["room_id"], **payload)
                assert response.status_code == 403
            guest = clients[USER_IDS[1]]
            for endpoint, payload in [("start", {}), ("settings", {"base_stake": 5, "loss_limit": 500}), ("bots", {"operation": "add", "count": 1})]:
                response = await _post(guest, endpoint, room_id=room["room_id"], **payload)
                assert response.status_code == 403
            assert all(balance == INITIAL_BALANCE for balance in _balances(api, USER_IDS).values())

    asyncio.run(scenario())


def test_custom_settings_require_new_ready_and_do_not_charge_until_start(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:2])
            room = await _create_and_join(clients, "texas", room_tier="custom")
            for client in clients.values():
                _room(await _post(client, "ready", room_id=room["room_id"], ready=True))
            room = _room(await _post(clients[USER_IDS[0]], "settings", room_id=room["room_id"], base_stake=5, loss_limit=500))
            assert room["buy_in"] == 500
            assert all(not player["is_ready"] for player in room["players"])
            assert _balances(api, USER_IDS[:2]) == {uid: INITIAL_BALANCE for uid in USER_IDS[:2]}
            response = await _post(clients[USER_IDS[0]], "start", room_id=room["room_id"])
            assert response.status_code == 400
            room = await _ready_and_start(clients, room["room_id"])
            assert room["game"]["buy_in"] == 500
            response = await _post(clients[USER_IDS[0]], "settings", room_id=room["room_id"], base_stake=10, loss_limit=1000)
            assert response.status_code == 400
            assert _balances(api, USER_IDS[:2]) == {uid: INITIAL_BALANCE - 500 for uid in USER_IDS[:2]}

    asyncio.run(scenario())


def test_waiting_host_leave_transfers_host_without_charging(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:2])
            room = await _create_and_join(clients, "texas")
            response = await _post(clients[USER_IDS[0]], "leave", room_id=room["room_id"])
            assert response.status_code == 200
            room = _room(await clients[USER_IDS[1]].get(f"/api/tables/{room['room_id']}"))
            assert room["host_user_id"] == USER_IDS[1]
            assert len(room["players"]) == 1
            assert _balances(api, USER_IDS[:2]) == {uid: INITIAL_BALANCE for uid in USER_IDS[:2]}

    asyncio.run(scenario())


@pytest.mark.parametrize("tier,entry,limit,base", [
    ("beginner", 100, 100, 1),
    ("intermediate", 1000, 500, 5),
    ("advanced", 5000, 2000, 20),
])
def test_tier_entry_is_checked_for_creation_join_and_atomic_start(api, tier, entry, limit, base):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:2]
            clients = await _clients(stack, api, ids)
            with sqlite3.connect(api.db_path) as connection:
                connection.execute("UPDATE user_coins SET balance=?", (entry - 1,))
            request = dict(game_type="texas", mode="multi", include_yueyue=False, room_tier=tier)
            rejected = await _post(clients[ids[0]], "create", **request)
            assert rejected.status_code == 402
            assert not api.module.table_service.rooms
            with sqlite3.connect(api.db_path) as connection:
                connection.execute("UPDATE user_coins SET balance=? WHERE user_id=?", (entry, int(ids[0])))
            room = _room(await _post(clients[ids[0]], "create", **request))
            assert (room["base_stake"], room["entry_min"], room["loss_limit"]) == (base, entry, limit)
            denied = await _post(clients[ids[1]], "join", room_id=room["room_id"])
            assert denied.status_code == 402
            assert len(api.module.table_service._room(room["room_id"]).players) == 1
            with sqlite3.connect(api.db_path) as connection:
                connection.execute("UPDATE user_coins SET balance=? WHERE user_id=?", (entry, int(ids[1])))
            _room(await _post(clients[ids[1]], "join", room_id=room["room_id"]))
            for client in clients.values():
                _room(await _post(client, "ready", room_id=room["room_id"], ready=True))
            before = _room(await clients[ids[0]].get(f"/api/tables/{room['room_id']}"))
            with sqlite3.connect(api.db_path) as connection:
                connection.execute("UPDATE user_coins SET balance=? WHERE user_id=?", (entry - 1, int(ids[1])))
            denied = await _post(clients[ids[0]], "start", room_id=room["room_id"])
            assert denied.status_code == 402
            assert _room(await clients[ids[0]].get(f"/api/tables/{room['room_id']}")) == before
            assert _balances(api, ids) == {ids[0]: entry, ids[1]: entry - 1}
            with sqlite3.connect(api.db_path) as connection:
                assert connection.execute("SELECT COUNT(*) FROM coin_transactions").fetchone()[0] == 0
                connection.execute("UPDATE user_coins SET balance=? WHERE user_id=?", (entry, int(ids[1])))
            room = _room(await _post(clients[ids[0]], "start", room_id=room["room_id"]))
            assert _balances(api, ids) == {uid: entry - limit for uid in ids}
            assert [player["stack"] for player in room["game"]["players"]] == [limit - base, limit - 2 * base]
            # 已冻结余额的玩家仍能恢复原房间，不受新玩家准入条件阻挡。
            restored = _room(await _post(clients[ids[0]], "create", **request))
            assert restored["room_id"] == room["room_id"]
            rejoined = _room(await _post(clients[ids[1]], "join", room_id=room["room_id"]))
            assert rejoined["round_number"] == 1
            assert _balances(api, ids) == {uid: entry - limit for uid in ids}
            finished = _room(await _post(clients[ids[0]], "action", room_id=room["room_id"], action="fold"))
            assert finished["actual_settlement"] == {ids[0]: -base, ids[1]: base}
            assert _balances(api, ids) == {ids[0]: entry - base, ids[1]: entry + base}

    asyncio.run(scenario())


@pytest.mark.parametrize("fields,status", [
    ({"buy_in": 500}, 422),
    ({"room_tier": "beginner", "base_stake": 2}, 400),
    ({"room_tier": "advanced", "loss_limit": 100}, 400),
    ({"room_tier": "custom", "base_stake": 20, "loss_limit": 199}, 400),
    ({"room_tier": "custom", "base_stake": True}, 422),
    ({"room_tier": "custom", "base_stake": 1.5}, 422),
    ({"room_tier": "custom", "loss_limit": 2**53}, 422),
])
def test_invalid_tier_terms_and_old_buy_in_field_are_rejected_without_room(api, fields, status):
    async def scenario():
        async with AsyncExitStack() as stack:
            client = (await _clients(stack, api, USER_IDS[:1]))[USER_IDS[0]]
            response = await _post(client, "create", game_type="texas", mode="multi", **fields)
            assert response.status_code == status, response.text
            assert not api.module.table_service.rooms
            assert _balances(api, USER_IDS[:1])[USER_IDS[0]] == INITIAL_BALANCE

    asyncio.run(scenario())


def test_custom_settings_check_every_human_balance_before_resetting_ready(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:2]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, "texas", room_tier="custom")
            for client in clients.values():
                _room(await _post(client, "ready", room_id=room["room_id"], ready=True))
            before = _room(await clients[ids[0]].get(f"/api/tables/{room['room_id']}"))
            with sqlite3.connect(api.db_path) as connection:
                connection.execute("UPDATE user_coins SET balance=499 WHERE user_id=?", (int(ids[1]),))
            denied = await _post(clients[ids[0]], "settings", room_id=room["room_id"], base_stake=5, loss_limit=500)
            assert denied.status_code == 402
            assert _room(await clients[ids[0]].get(f"/api/tables/{room['room_id']}")) == before
            assert _balances(api, ids) == {ids[0]: INITIAL_BALANCE, ids[1]: 499}
            old = await _post(clients[ids[0]], "buy-in", room_id=room["room_id"], buy_in=500)
            assert old.status_code in (404, 405)

    asyncio.run(scenario())


def test_bot_count_operations_preserve_seats_allow_solo_removal_and_never_charge(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            uid = USER_IDS[0]
            client = (await _clients(stack, api, [uid]))[uid]
            room = _room(await _post(client, "create", game_type="mahjong", mode="solo", room_tier="beginner"))
            rid = room["room_id"]
            original_bots = [player for player in room["players"] if player["is_bot"]]
            for player in original_bots:
                room = _room(await _post(client, "bots", room_id=rid, operation="remove", bot_id=player["user_id"]))
            assert [player["user_id"] for player in room["players"]] == [uid]
            room = _room(await _post(client, "bots", room_id=rid, operation="add", count=1))
            assert len(room["players"]) == 2 and room["include_yueyue"]
            kept = room["players"][:]
            added = _room(await _post(client, "bots", room_id=rid, operation="add", count=1))
            assert added["players"][:2] == kept
            assert len(added["players"]) == 3
            assert added["players"][-1]["user_id"] not in {p["user_id"] for p in original_bots}
            for fields, expected in [
                ({"operation": "add", "count": 2}, 400),
                ({"operation": "add", "count": 0}, 422),
                ({"operation": "add", "count": True}, 422),
                ({"operation": "remove", "bot_id": uid}, 400),
                ({"include_yueyue": True, "fill_bots": True}, 422),
            ]:
                response = await _post(client, "bots", room_id=rid, **fields)
                assert response.status_code == expected
                assert _room(await client.get(f"/api/tables/{rid}")) == added
            _room(await _post(client, "ready", room_id=rid, ready=True))
            assert (await _post(client, "start", room_id=rid)).status_code == 400
            assert _balances(api, [uid])[uid] == INITIAL_BALANCE

    asyncio.run(scenario())
