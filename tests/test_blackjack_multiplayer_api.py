"""真实 ASGI 牌局回归；账户隔离，单人持久化使用临时 SQLite，不连接 Discord。"""

import asyncio
import importlib.util
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Request


WEB_DIR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "chat"
    / "features"
    / "games"
    / "blackjack-web"
)
HOST_ID = 123456789012345678
GUEST_ID = 234567890123456789
THIRD_ID = 345678901234567890


class InMemoryCoins:
    """隔离账户并记录真实借贷结果，允许模拟一次派彩失败。"""

    def __init__(self):
        self.balances = defaultdict(lambda: 1000)
        self.entries = []
        self.fail_next_credit_for = None
        self.fail_next_balance_for = None

    async def get_balance(self, user_id):
        if self.fail_next_balance_for == int(user_id):
            self.fail_next_balance_for = None
            raise RuntimeError("模拟余额查询暂时不可用")
        return self.balances[int(user_id)]

    async def remove_coins(self, user_id, amount, reason):
        user_id = int(user_id)
        await asyncio.sleep(0)
        if self.balances[user_id] < amount:
            return None
        self.balances[user_id] -= amount
        self.entries.append((user_id, -amount, reason))
        return self.balances[user_id]

    async def add_coins(self, user_id, amount, reason):
        user_id = int(user_id)
        await asyncio.sleep(0)
        if self.fail_next_credit_for == user_id:
            self.fail_next_credit_for = None
            raise RuntimeError("模拟派彩数据库暂时不可用")
        self.balances[user_id] += amount
        self.entries.append((user_id, amount, reason))
        return self.balances[user_id]


def _load_module(monkeypatch, name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api(monkeypatch):
    coins = InMemoryCoins()
    database = SimpleNamespace(update_blackjack_net_win_loss=AsyncMock())
    # 仅替换外部服务；路由、请求校验、锁以及多人引擎均运行实际代码。
    dependencies = {
        "src.chat.features.odysseia_coin.service.coin_service": {
            "coin_service": coins
        },
        "src.chat.features.games.services.blackjack_service": {
            "blackjack_service": SimpleNamespace()
        },
        "src.chat.utils.database": {"chat_db_manager": database},
        "src.dashboard.service_registry": {
            "service_registry": SimpleNamespace(bot=None)
        },
    }
    for name, attributes in dependencies.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)

    engine = _load_module(
        monkeypatch, "multiplayer_service", WEB_DIR / "multiplayer_service.py"
    )
    module = _load_module(
        monkeypatch, "blackjack_web_api_under_test", WEB_DIR / "app.py"
    )

    async def test_profile(request: Request):
        user_id = int(request.headers.get("X-Test-User", str(HOST_ID)))
        return {
            "user_id": user_id,
            "username": f"测试玩家{str(user_id)[-4:]}",
            "avatar_url": "/character/normal.webp",
            "is_dev": False,
        }

    module.app.dependency_overrides[module.get_current_user_profile] = test_profile

    # 庄家 17 点，三个座位依次 19、18、16 点，避免测试依赖随机发牌。
    draw_order = [
        "Heart10", "Club7", "Spade10", "Diamond9",
        "Club10", "Heart8", "Diamond10", "Spade6",
    ]
    deck = [card for card in engine._create_deck() if card not in draw_order]
    deck.extend(reversed(draw_order))
    monkeypatch.setattr(engine, "_create_deck", lambda: list(deck))
    monkeypatch.setattr(engine.random, "shuffle", lambda cards: None)

    return SimpleNamespace(module=module, engine=engine, coins=coins, database=database)


def _client(api, client_host="127.0.0.1"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=api.module.app, raise_app_exceptions=False, client=(client_host, 12345)
        ),
        base_url="http://testserver",
    )


async def _request(client, action, user_id=HOST_ID, method="POST", **payload):
    kwargs = {"headers": {"X-Test-User": str(user_id)}}
    if method != "GET":
        kwargs["json"] = payload
    return await client.request(method, f"/api/multi/room/{action}", **kwargs)


async def _create_room(client, users=(HOST_ID, GUEST_ID)):
    response = await _request(client, "create", users[0])
    assert response.status_code == 200, response.text
    room_id = response.json()["room"]["room_id"]
    for user_id in users[1:]:
        response = await _request(client, "join", user_id, room_id=room_id)
        assert response.status_code == 200, response.text
    return room_id


async def _start_round(client, room_id, users=(HOST_ID, GUEST_ID)):
    for user_id in users:
        response = await _request(client, "bet", user_id, room_id=room_id, amount=100)
        assert response.status_code == 200, response.text
        response = await _request(client, "ready", user_id, room_id=room_id, ready=True)
        assert response.status_code == 200, response.text
    response = await _request(client, "start", users[0], room_id=room_id)
    assert response.status_code == 200, response.text
    return response.json()["room"]


async def _finish_round(client, room_id, users=(HOST_ID, GUEST_ID)):
    for user_id in users:
        response = await _request(client, "stand", user_id, room_id=room_id)
        assert response.status_code == 200, response.text
    return response.json()["room"]


def test_three_player_round_turn_rules_and_payouts(api):
    async def scenario():
        async with _client(api) as client:
            users = (HOST_ID, GUEST_ID, THIRD_ID)
            room_id = await _create_room(client, users)
            full = await _request(client, "join", 456789012345678901, room_id=room_id)
            assert full.status_code == 400
            room = await _start_round(client, room_id, users)
            assert room["dealer"]["hand"] == ["Heart10", "Hidden"]
            assert str(room["current_turn_user_id"]) == str(HOST_ID)
            wrong_turn = await _request(client, "hit", GUEST_ID, room_id=room_id)
            assert wrong_turn.status_code == 400
            room = await _finish_round(client, room_id, users)
            assert room["state"] == "finished"
            assert [p["result"] for p in room["players"]] == ["win", "win", "loss"]
            assert [api.coins.balances[uid] for uid in users] == [1100, 1100, 900]
            for user_id in users:
                response = await _request(client, room_id, user_id, method="GET")
                assert response.status_code == 200
            repeated = await _request(client, "stand", THIRD_ID, room_id=room_id)
            assert repeated.status_code == 400
            assert [api.coins.balances[uid] for uid in users] == [1100, 1100, 900]
            api.database.update_blackjack_net_win_loss.assert_awaited_once_with(-100)

    asyncio.run(scenario())


def test_discord_snowflakes_are_serialized_without_precision_loss(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client)
            room = await _start_round(client, room_id)
            assert room["host_user_id"] == str(HOST_ID)
            assert room["current_turn_user_id"] == str(HOST_ID)
            assert [p["user_id"] for p in room["players"]] == [str(HOST_ID), str(GUEST_ID)]

    asyncio.run(scenario())


def test_simultaneous_activity_join_shares_one_room_and_reconnects(api):
    async def scenario():
        async with _client(api) as client:
            responses = await asyncio.gather(
                *(
                    _request(client, "auto-join", uid, session_key="instance:test-activity")
                    for uid in (HOST_ID, GUEST_ID, THIRD_ID)
                )
            )
            assert [response.status_code for response in responses] == [200, 200, 200]
            room_ids = {response.json()["room"]["room_id"] for response in responses}
            assert len(room_ids) == 1
            reconnect = await _request(
                client, "auto-join", GUEST_ID, session_key="instance:test-activity"
            )
            assert reconnect.status_code == 200
            assert len(reconnect.json()["room"]["players"]) == 3
            assert reconnect.json()["room"]["room_id"] in room_ids

    asyncio.run(scenario())


def test_waiting_bet_change_and_leave_refund_exactly_once(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client)
            for amount in (100, 150, 75):
                response = await _request(client, "bet", room_id=room_id, amount=amount)
                assert response.status_code == 200, response.text
                assert response.json()["viewer_balance"] == 1000 - amount
            response = await _request(client, "leave", room_id=room_id)
            assert response.status_code == 200, response.text
            assert api.coins.balances[HOST_ID] == 1000
            repeated = await _request(client, "leave", room_id=room_id)
            assert repeated.status_code == 400
            assert api.coins.balances[HOST_ID] == 1000

    asyncio.run(scenario())


def test_insufficient_balance_does_not_change_existing_bet(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client)
            response = await _request(client, "bet", room_id=room_id, amount=100)
            assert response.status_code == 200
            rejected = await _request(client, "bet", room_id=room_id, amount=1001)
            assert rejected.status_code == 402
            state = await _request(client, room_id, method="GET")
            assert state.json()["room"]["players"][0]["bet_amount"] == 100
            assert api.coins.balances[HOST_ID] == 900

    asyncio.run(scenario())


def test_manual_bet_after_finished_round_deducts_the_full_new_stake(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client)
            await _start_round(client, room_id)
            await _finish_round(client, room_id)
            response = await _request(client, "bet", room_id=room_id, amount=75)
            assert response.status_code == 200, response.text
            assert response.json()["room"]["state"] == "waiting"
            assert response.json()["viewer_balance"] == 1025
            assert api.coins.balances[GUEST_ID] == 1100

    asyncio.run(scenario())


def test_balance_read_failure_does_not_refund_an_already_committed_bet(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client)
            api.coins.fail_next_balance_for = HOST_ID
            response = await _request(client, "bet", room_id=room_id, amount=100)
            assert response.status_code == 500
            state = await _request(client, room_id, method="GET")
            assert state.status_code == 200
            assert state.json()["room"]["players"][0]["bet_amount"] == 100
            assert state.json()["viewer_balance"] == 900

    asyncio.run(scenario())


def test_failed_bet_reduction_keeps_the_original_stake(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client)
            response = await _request(client, "bet", room_id=room_id, amount=100)
            assert response.status_code == 200
            api.coins.fail_next_credit_for = HOST_ID
            response = await _request(client, "bet", room_id=room_id, amount=75)
            assert response.status_code == 500
            state = await _request(client, room_id, method="GET")
            assert state.json()["room"]["players"][0]["bet_amount"] == 100
            assert state.json()["viewer_balance"] == 900

    asyncio.run(scenario())


def test_simultaneous_continue_ready_deducts_only_one_new_stake(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client)
            await _start_round(client, room_id)
            await _finish_round(client, room_id)
            responses = await asyncio.gather(
                _request(client, "continue-ready", room_id=room_id),
                _request(client, "continue-ready", room_id=room_id),
            )
            assert [response.status_code for response in responses] == [200, 200]
            assert api.coins.balances[HOST_ID] == 1000
            state = await _request(client, room_id, method="GET")
            host = state.json()["room"]["players"][0]
            assert host["bet_amount"] == 100
            assert host["is_ready"] is True

    asyncio.run(scenario())


def test_two_rounds_can_finish_and_settle_in_the_same_room(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client)
            await _start_round(client, room_id)
            await _finish_round(client, room_id)
            for user_id in (HOST_ID, GUEST_ID):
                response = await _request(client, "continue-ready", user_id, room_id=room_id)
                assert response.status_code == 200, response.text
            response = await _request(client, "start", room_id=room_id)
            assert response.status_code == 200, response.text
            await _finish_round(client, room_id)
            assert api.coins.balances[HOST_ID] == 1200
            assert api.coins.balances[GUEST_ID] == 1200
            assert api.database.update_blackjack_net_win_loss.await_count == 2

    asyncio.run(scenario())


def test_leaving_last_active_turn_finishes_and_pays_remaining_player(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client)
            await _start_round(client, room_id)
            response = await _request(client, "stand", room_id=room_id)
            assert response.status_code == 200
            response = await _request(client, "leave", GUEST_ID, room_id=room_id)
            assert response.status_code == 200, response.text
            assert response.json()["room"]["state"] == "finished"
            assert api.coins.balances[HOST_ID] == 1100
            assert api.coins.balances[GUEST_ID] == 900
            state = await _request(client, room_id, method="GET")
            assert state.status_code == 200
            assert api.coins.balances[HOST_ID] == 1100

    asyncio.run(scenario())


def test_poll_retries_failed_payout_without_paying_prior_players_twice(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client)
            await _start_round(client, room_id)
            response = await _request(client, "stand", room_id=room_id)
            assert response.status_code == 200
            api.coins.fail_next_credit_for = GUEST_ID
            failed = await _request(client, "stand", GUEST_ID, room_id=room_id)
            assert failed.status_code in (500, 503)
            assert api.coins.balances[HOST_ID] == 1100
            assert api.coins.balances[GUEST_ID] == 900
            for _ in range(2):
                recovered = await _request(client, room_id, GUEST_ID, method="GET")
                assert recovered.status_code == 200, recovered.text
            assert api.coins.balances[HOST_ID] == 1100
            assert api.coins.balances[GUEST_ID] == 1100
            api.database.update_blackjack_net_win_loss.assert_awaited_once_with(-200)

    asyncio.run(scenario())


def test_room_state_is_not_readable_by_non_members(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client)
            response = await _request(client, room_id, THIRD_ID, method="GET")
            assert response.status_code == 403

    asyncio.run(scenario())


def test_unbind_room_removes_all_activity_aliases(api):
    api.module._bind_session_room("instance:first", "ABC123")
    api.module._bind_session_room("channel:123:456", "ABC123")
    api.module._unbind_session_by_room("ABC123")
    assert "instance:first" not in api.module.activity_room_bindings
    assert "channel:123:456" not in api.module.activity_room_bindings
    assert "ABC123" not in api.module.room_activity_bindings


def test_reading_development_balance_never_creates_free_coins(api):
    async def scenario():
        uid = api.module.TEST_USER_ID
        api.coins.balances[uid] = 0
        for _ in range(2):
            assert await api.module._ensure_user_balance(uid) == 0
        assert api.coins.entries == []

    asyncio.run(scenario())


def test_bot_api_enforces_host_permissions_and_allows_add_remove(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client)
            outsider = await _request(client, "bot", THIRD_ID, room_id=room_id, include_yueyue=True)
            assert outsider.status_code == 403
            non_host = await _request(client, "bot", GUEST_ID, room_id=room_id, include_yueyue=True)
            assert non_host.status_code == 400
            assert "只有房主" in non_host.json()["detail"]
            state = await _request(client, room_id, method="GET")
            assert state.json()["room"]["include_yueyue"] is False
            added = await _request(client, "bot", room_id=room_id, include_yueyue=True)
            assert added.status_code == 200, added.text
            players = added.json()["room"]["players"]
            assert len(players) == 3
            assert players[-1]["user_id"] == "-1"
            assert players[-1]["is_bot"] is True
            assert players[-1]["username"] == "月月（陪玩）"
            repeated = await _request(client, "bot", room_id=room_id, include_yueyue=True)
            assert repeated.status_code == 200
            assert len(repeated.json()["room"]["players"]) == 3
            removed = await _request(client, "bot", room_id=room_id, include_yueyue=False)
            assert removed.status_code == 200
            assert removed.json()["room"]["include_yueyue"] is False
            assert all(not player["is_bot"] for player in removed.json()["room"]["players"])
            assert api.coins.entries == []

    asyncio.run(scenario())


def test_bot_api_completes_round_without_any_robot_wallet_entry(api):
    async def scenario():
        async with _client(api) as client:
            room_id = await _create_room(client, users=(HOST_ID,))
            added = await _request(client, "bot", room_id=room_id, include_yueyue=True)
            assert added.status_code == 200
            room = await _start_round(client, room_id, users=(HOST_ID,))
            bot = next(player for player in room["players"] if player["is_bot"])
            assert bot["bet_amount"] == 100
            assert bot["is_ready"] is True
            rejected = await _request(client, "bot", room_id=room_id, include_yueyue=False)
            assert rejected.status_code == 400
            assert "进行中" in rejected.json()["detail"]
            finished = await _request(client, "stand", room_id=room_id)
            assert finished.status_code == 200, finished.text
            assert finished.json()["room"]["state"] == "finished"
            assert [player["result"] for player in finished.json()["room"]["players"]] == ["win", "win"]
            assert finished.json()["viewer_balance"] == 1100
            assert [(uid, amount) for uid, amount, _ in api.coins.entries] == [(HOST_ID, -100), (HOST_ID, 200)]
            api.database.update_blackjack_net_win_loss.assert_awaited_once_with(-100)
            for _ in range(2):
                refreshed = await _request(client, room_id, method="GET")
                assert refreshed.status_code == 200
            removed = await _request(client, "bot", room_id=room_id, include_yueyue=False)
            assert removed.status_code == 200
            assert api.coins.balances[HOST_ID] == 1100
            assert len(api.coins.entries) == 2
            assert -1 not in api.coins.balances

    asyncio.run(scenario())


@pytest.fixture
def single_api(api, monkeypatch, tmp_path):
    db_path = tmp_path / "single-blackjack.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE blackjack_games (user_id INTEGER PRIMARY KEY, bet_amount INTEGER, "
            "game_state TEXT, deck TEXT, player_hand TEXT, dealer_hand TEXT)"
        )

    class IsolatedGameDatabase:
        """复用真实单人服务 SQL，仅将数据库适配到测试临时文件。"""

        _db_transaction = object()

        async def _execute(self, operation, query, params=(), commit=False, fetch=None):
            with sqlite3.connect(db_path) as connection:
                connection.row_factory = sqlite3.Row
                cursor = connection.execute(query, params)
                if fetch == "one":
                    row = cursor.fetchone()
                    return dict(row) if row else None
                if fetch == "all":
                    return [dict(row) for row in cursor.fetchall()]
                if commit:
                    connection.commit()

    single_module = _load_module(
        monkeypatch, "single_blackjack_service_under_test",
        WEB_DIR.parent / "services" / "blackjack_service.py",
    )
    single_service = single_module.BlackjackService(IsolatedGameDatabase())
    draw_order = ["Heart10", "Spade9", "Club10", "Diamond7", "Heart2"]
    deck = [card for card in single_service._create_deck() if card not in draw_order]
    deck.extend(reversed(draw_order))
    monkeypatch.setattr(single_service, "_create_deck", lambda: list(deck))
    monkeypatch.setattr(single_service, "_shuffle_deck", lambda cards: None)
    monkeypatch.setattr(api.module, "blackjack_service", single_service)

    async def test_user_id(request: Request):
        return int(request.headers.get("X-Test-User", str(HOST_ID)))

    api.module.app.dependency_overrides[api.module.get_current_user_id] = test_user_id
    api.single_service = single_service
    api.single_db_path = db_path
    return api


def test_current_single_game_restores_persisted_hand_without_new_charge(single_api):
    async def scenario():
        headers = {"X-Test-User": str(HOST_ID)}
        async with _client(single_api) as client:
            empty = await client.get("/api/game/current", headers=headers)
            assert empty.status_code == 200
            assert empty.json()["game"] is None
            started = await client.post("/api/game/start", json={"amount": 100}, headers=headers)
            assert started.status_code == 200, started.text
            initial = started.json()["game"]
            assert initial["game_state"] == "player_turn"
            assert initial["dealer_hand"] == ["Club10", "Hidden"]
            # 新的 HTTP 客户端模拟页面刷新，牌局从临时 SQLite 恢复。
        async with _client(single_api) as refreshed_client:
            current = await refreshed_client.get("/api/game/current", headers=headers)
            assert current.status_code == 200
            assert current.json()["game"] == initial
            assert current.json()["new_balance"] == 900
            other = await refreshed_client.get("/api/game/current", headers={"X-Test-User": str(GUEST_ID)})
            assert other.status_code == 200
            assert other.json()["game"] is None
            finished = await refreshed_client.post("/api/game/stand", headers=headers)
            assert finished.status_code == 200, finished.text
            assert finished.json()["game"]["game_state"] == "finished_win"
            assert finished.json()["new_balance"] == 1100
            current = await refreshed_client.get("/api/game/current", headers=headers)
            assert current.json()["game"] is None
            assert current.json()["new_balance"] == 1100
            assert [(uid, amount) for uid, amount, _ in single_api.coins.entries] == [(HOST_ID, -100), (HOST_ID, 200)]

    asyncio.run(scenario())


def test_duplicate_single_start_returns_conflict_without_charging_or_replacing_game(single_api):
    async def scenario():
        headers = {"X-Test-User": str(HOST_ID)}
        async with _client(single_api) as client:
            first = await client.post("/api/game/start", json={"amount": 100}, headers=headers)
            assert first.status_code == 200
            repeated = await client.post("/api/game/start", json={"amount": 250}, headers=headers)
            assert repeated.status_code == 409
            assert "未结束" in repeated.json()["detail"]
            current = await client.get("/api/game/current", headers=headers)
            assert current.json()["game"] == first.json()["game"]
            assert current.json()["new_balance"] == 900
            assert [(uid, amount) for uid, amount, _ in single_api.coins.entries] == [(HOST_ID, -100)]
            with sqlite3.connect(single_api.single_db_path) as connection:
                assert connection.execute("SELECT COUNT(*), MAX(bet_amount) FROM blackjack_games").fetchone() == (1, 100)

    asyncio.run(scenario())


def test_concurrent_single_starts_create_only_one_game_and_charge(single_api):
    async def scenario():
        headers = {"X-Test-User": str(HOST_ID)}
        async with _client(single_api) as client:
            responses = await asyncio.gather(*(
                client.post("/api/game/start", json={"amount": 100}, headers=headers)
                for _ in range(2)
            ))
            assert sorted(response.status_code for response in responses) == [200, 409]
            assert single_api.coins.balances[HOST_ID] == 900
            assert len(single_api.coins.entries) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("endpoint", ["/api/profile", "/api/game/current"])
@pytest.mark.parametrize(("client_host", "configured_client"), [
    ("203.0.113.10", ""), ("203.0.113.10", "123456789"), ("127.0.0.1", "123456789"),
])
def test_production_without_token_rejects_development_identity_headers(single_api, monkeypatch, endpoint, client_host, configured_client):
    single_api.module.app.dependency_overrides.clear()
    monkeypatch.setenv("BLACKJACK_ALLOW_DEV_AUTH", "false")
    monkeypatch.setenv("DISCORD_CLIENT_ID", configured_client)
    monkeypatch.delenv("VITE_DISCORD_CLIENT_ID", raising=False)

    async def scenario():
        async with _client(single_api, client_host) as client:
            response = await client.get(endpoint, headers={"X-Dev-User-Id": str(HOST_ID)})
            assert response.status_code == 401
            assert "Discord" in response.json()["detail"]
            assert single_api.coins.entries == []

    asyncio.run(scenario())


@pytest.mark.parametrize("client_host", ["127.0.0.1", "::1"])
def test_unconfigured_loopback_development_auth_uses_same_single_and_multi_identity(single_api, monkeypatch, client_host):
    single_api.module.app.dependency_overrides.clear()
    monkeypatch.delenv("BLACKJACK_ALLOW_DEV_AUTH", raising=False)
    monkeypatch.delenv("DISCORD_CLIENT_ID", raising=False)
    monkeypatch.delenv("VITE_DISCORD_CLIENT_ID", raising=False)
    headers = {"X-Dev-User-Id": str(HOST_ID), "X-Dev-Username": "LocalPlayer"}

    async def scenario():
        async with _client(single_api, client_host) as client:
            profile = await client.get("/api/profile", headers=headers)
            assert profile.status_code == 200, profile.text
            assert profile.json()["user_id"] == str(HOST_ID)
            assert profile.json()["username"] == "LocalPlayer"
            started = await client.post("/api/game/start", json={"amount": 100}, headers=headers)
            assert started.status_code == 200, started.text
            current = await client.get("/api/game/current", headers=headers)
            assert current.status_code == 200
            assert str(current.json()["game"]["user_id"]) == str(HOST_ID)
            profile = await client.get("/api/profile", headers=headers)
            assert profile.json()["balance"] == 900
            assert single_api.module.TEST_USER_ID not in single_api.coins.balances

    asyncio.run(scenario())


def test_explicit_development_flag_allows_configured_non_loopback_test_server(single_api, monkeypatch):
    single_api.module.app.dependency_overrides.clear()
    monkeypatch.setenv("BLACKJACK_ALLOW_DEV_AUTH", "true")
    monkeypatch.setenv("DISCORD_CLIENT_ID", "123456789")

    async def scenario():
        async with _client(single_api, "203.0.113.10") as client:
            profile = await client.get("/api/profile", headers={"X-Dev-User-Id": str(HOST_ID)})
            assert profile.status_code == 200
            assert profile.json()["user_id"] == str(HOST_ID)

    asyncio.run(scenario())


@pytest.mark.parametrize("user_id", ["-1", "0", "not-an-id", str(2**63)])
def test_development_auth_rejects_invalid_or_reserved_user_ids(single_api, monkeypatch, user_id):
    single_api.module.app.dependency_overrides.clear()
    monkeypatch.setenv("BLACKJACK_ALLOW_DEV_AUTH", "true")

    async def scenario():
        async with _client(single_api) as client:
            profile = await client.get("/api/profile", headers={"X-Dev-User-Id": user_id})
            assert profile.status_code == 400
            assert single_api.coins.entries == []

    asyncio.run(scenario())
