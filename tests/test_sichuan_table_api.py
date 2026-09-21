"""四川血战的真实 ASGI、房间服务与临时 SQLite 钱包集成回归。"""

import asyncio
import sqlite3
from collections import Counter
from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest

from test_table_multiplayer_api import (
    INITIAL_BALANCE,
    USER_IDS,
    _balances,
    _clients,
    _create_and_join,
    _ledger,
    _post,
    _ready_and_start,
    _room,
    api,
)


GAME_TYPE = "sichuan_mahjong"


def _transactions(api):
    with sqlite3.connect(api.db_path) as connection:
        return connection.execute(
            "SELECT user_id, amount, reason FROM coin_transactions ORDER BY id"
        ).fetchall()


async def _choose_missing_suits(clients, room):
    choices = {}
    for _ in range(4):
        uid = room["game"]["current_player_id"]
        view = _room(await clients[uid].get(f"/api/tables/{room['room_id']}"))
        own = next(player for player in view["game"]["players"] if player["user_id"] == uid)
        suit = own["hand"][0][0]
        choices[uid] = suit
        room = _room(await _post(
            clients[uid], "action", room_id=room["room_id"], action="dingque",
            suit=suit, expected_revision=view["revision"],
        ))
    assert room["game"]["phase"] != "dingque"
    return room, choices


async def _finish(api, clients, room):
    """通过真实HTTP动作或时钟托管走完整局，同时防止中途胡牌提前到账。"""
    room_id = room["room_id"]
    frozen = _balances(api, clients)
    initial_transactions = _transactions(api)
    for _ in range(1000):
        if room["state"] == "finished":
            return room
        assert room["settlement_status"] == "reserved"
        assert room["actual_settlement"] == {}
        assert _balances(api, clients) == frozen
        assert _transactions(api) == initial_transactions
        uid = room["game"]["current_player_id"]
        member = next(player for player in room["players"] if player["user_id"] == uid)
        if uid in clients and member["connected"] and not member["is_bot"]:
            suggestion = api.module.table_service._room(room_id).engine.suggest_action(uid)
            room = _room(await _post(
                clients[uid], "action", room_id=room_id,
                expected_revision=room["revision"], **suggestion,
            ))
        else:
            api.clock.advance()
            room = _room(await next(iter(clients.values())).get(f"/api/tables/{room_id}"))
    pytest.fail("四川血战1000次合法动作后仍未结束")


def test_sichuan_is_separate_four_player_mode_and_unknown_mode_is_rejected(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:3])
            normal = _room(await _post(clients[USER_IDS[0]], "create", game_type="mahjong", mode="multi", include_yueyue=False))
            sichuan = _room(await _post(clients[USER_IDS[1]], "create", game_type=GAME_TYPE, mode="multi", include_yueyue=False))
            assert normal["game_type"] == "mahjong"
            assert sichuan["game_type"] == GAME_TYPE
            assert normal["room_id"] != sichuan["room_id"]
            assert sichuan["min_players"] == sichuan["max_players"] == 4
            rejected = await _post(clients[USER_IDS[2]], "create", game_type="sichuan_unknown", mode="multi")
            assert rejected.status_code == 400
            assert len(api.module.table_service.rooms) == 2
            assert _balances(api, clients) == {uid: INITIAL_BALANCE for uid in clients}
            assert _transactions(api) == []
    asyncio.run(scenario())


@pytest.mark.parametrize("fields", [
    {}, {"suit": None}, {"suit": ""}, {"suit": "z"}, {"suit": "m1"},
    {"suit": "M"}, {"suit": 1}, {"suit": True}, {"suit": ["m"]},
])
def test_invalid_dingque_payload_is_rejected_before_room_changes(api, fields):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:1])
            client = next(iter(clients.values()))
            before = _room(await _post(client, "create", game_type=GAME_TYPE, mode="solo"))
            response = await _post(client, "action", room_id=before["room_id"], action="dingque", **fields)
            assert response.status_code == 422, response.text
            assert _room(await client.get(f"/api/tables/{before['room_id']}")) == before
            assert _transactions(api) == []
    asyncio.run(scenario())


def test_missing_suit_field_cannot_be_silently_used_on_other_actions(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            client = next(iter((await _clients(stack, api, USER_IDS[:1])).values()))
            room = _room(await _post(client, "create", game_type="mahjong", mode="solo"))
            response = await _post(client, "action", room_id=room["room_id"], action="discard", tile="m1", suit="m")
            assert response.status_code == 422
            assert _room(await client.get(f"/api/tables/{room['room_id']}")) == room
    asyncio.run(scenario())


def test_four_real_clients_choose_private_suits_and_must_discard_missing_suit(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:4])
            room = await _create_and_join(clients, GAME_TYPE)
            room = await _ready_and_start(clients, room["room_id"])
            assert room["game"]["mahjong_variant"] == "sichuan"
            assert room["game"]["phase"] == "dingque"
            assert _balances(api, clients) == {uid: INITIAL_BALANCE - 100 for uid in clients}
            choices = {}
            for index in range(4):
                uid = room["game"]["current_player_id"]
                for viewer, client in clients.items():
                    snapshot = _room(await client.get(f"/api/tables/{room['room_id']}"))
                    assert snapshot["revision"] == room["revision"]
                    options = snapshot["game"].get("missing_suit_options", [])
                    assert options == (["m", "p", "s"] if uid == viewer else [])
                    for player in snapshot["game"]["players"]:
                        if player["user_id"] != viewer:
                            assert player["hand"] == []
                            assert not player.get("missing_suit")
                view = _room(await clients[uid].get(f"/api/tables/{room['room_id']}"))
                own = next(player for player in view["game"]["players"] if player["user_id"] == uid)
                choices[uid] = own["hand"][0][0]
                room = _room(await _post(clients[uid], "action", room_id=room["room_id"], action="dingque", suit=choices[uid], expected_revision=room["revision"]))
                if index < 3:
                    assert room["game"]["phase"] == "dingque"
            assert room["game"]["phase"] != "dingque"
            assert {player["user_id"]: player["missing_suit"] for player in room["game"]["players"]} == choices
            current = room["game"]["current_player_id"]
            before = _room(await clients[current].get(f"/api/tables/{room['room_id']}"))
            own = next(player for player in before["game"]["players"] if player["user_id"] == current)
            forbidden = next(tile for tile in own["hand"] if tile[0] != choices[current])
            allowed = next(tile for tile in own["hand"] if tile[0] == choices[current])
            response = await _post(clients[current], "action", room_id=room["room_id"], action="discard", tile=forbidden, expected_revision=room["revision"])
            assert response.status_code == 400, response.text
            assert _room(await clients[current].get(f"/api/tables/{room['room_id']}")) == before
            after = _room(await _post(clients[current], "action", room_id=room["room_id"], action="discard", tile=allowed, expected_revision=room["revision"]))
            assert after["revision"] > before["revision"]
            assert _balances(api, clients) == {uid: INITIAL_BALANCE - 100 for uid in clients}
    asyncio.run(scenario())


def test_concurrent_dingque_executes_once_and_reconnect_preserves_turn_and_escrow(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:4])
            with sqlite3.connect(api.db_path) as connection:
                connection.execute("UPDATE user_coins SET balance=100")
            room = await _create_and_join(clients, GAME_TYPE)
            room = await _ready_and_start(clients, room["room_id"])
            uid = room["game"]["current_player_id"]
            request = dict(room_id=room["room_id"], action="dingque", suit="m", expected_revision=room["revision"])
            responses = await asyncio.gather(*(_post(clients[uid], "action", **request) for _ in range(2)))
            assert sorted(response.status_code for response in responses) == [200, 409]
            accepted = _room(next(response for response in responses if response.status_code == 200))
            deadline = accepted["turn_deadline"]
            api.clock.advance(2)
            restored = _room(await _post(clients[uid], "create", game_type=GAME_TYPE, mode="multi"))
            assert restored["room_id"] == room["room_id"]
            assert restored["turn_deadline"] == deadline
            assert restored["game"] == accepted["game"]
            rejoined = _room(await _post(clients[uid], "join", room_id=room["room_id"]))
            assert rejoined["turn_deadline"] == deadline
            assert rejoined["round_number"] == 1
            assert _balances(api, clients) == {uid: 0 for uid in clients}
            assert len(_ledger(api)) == 4
            assert len(_transactions(api)) == 4
    asyncio.run(scenario())


@pytest.mark.parametrize("seed", [7, 42, 121])
def test_real_multiplayer_full_round_settles_once_and_preserves_total_balance(api, monkeypatch, seed):
    monkeypatch.setattr(api.module._table_module.random, "SystemRandom", lambda: SimpleNamespace(randrange=lambda _: seed))
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:4])
            room = await _create_and_join(clients, GAME_TYPE, room_tier="custom", base_stake=5, loss_limit=100)
            room = await _ready_and_start(clients, room["room_id"])
            finished = await _finish(api, clients, room)
            assert finished["game"]["finished"]
            assert finished["settlement_status"] == "settled"
            actual = finished["actual_settlement"]
            assert set(actual) == set(clients)
            assert sum(actual.values()) == 0
            assert min(actual.values()) >= -100
            assert _balances(api, clients) == {uid: INITIAL_BALANCE + actual[uid] for uid in clients}
            assert all(row[1] == 100 and row[3] == "settled" for row in _ledger(api))
            transactions = _transactions(api)
            for client in clients.values():
                assert _room(await client.get(f"/api/tables/{room['room_id']}"))["actual_settlement"] == actual
            assert _transactions(api) == transactions
            assert sum(row[1] for row in transactions) == 0
    asyncio.run(scenario())


def test_solo_ai_dingque_and_disconnected_human_complete_without_extra_wallet_charges(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:1])
            client = next(iter(clients.values()))
            room = _room(await _post(client, "create", game_type=GAME_TYPE, mode="solo"))
            assert len(room["players"]) == 4
            room = await _ready_and_start(clients, room["room_id"])
            assert room["game"]["phase"] == "dingque"
            leave = await _post(client, "leave", room_id=room["room_id"])
            assert leave.status_code == 200
            room = _room(await client.get(f"/api/tables/{room['room_id']}"))
            assert not next(player for player in room["players"] if not player["is_bot"])["connected"]
            finished = await _finish(api, clients, room)
            assert finished["settlement_status"] == "settled"
            assert len(_ledger(api)) == 1
            assert sum(finished["actual_settlement"].values()) == 0
            assert _balances(api, clients)[USER_IDS[0]] == INITIAL_BALANCE + finished["actual_settlement"][USER_IDS[0]]
    asyncio.run(scenario())


def test_sichuan_entry_and_atomic_four_player_reserve_rollback(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:4])
            with sqlite3.connect(api.db_path) as connection:
                connection.execute("UPDATE user_coins SET balance=99 WHERE user_id=?", (int(USER_IDS[0]),))
            rejected = await _post(clients[USER_IDS[0]], "create", game_type=GAME_TYPE, mode="solo")
            assert rejected.status_code == 402
            assert not api.module.table_service.rooms
            with sqlite3.connect(api.db_path) as connection:
                connection.execute("UPDATE user_coins SET balance=2000")
            room = await _create_and_join(clients, GAME_TYPE, room_tier="intermediate")
            for client in clients.values():
                _room(await _post(client, "ready", room_id=room["room_id"], ready=True))
            before = _room(await clients[USER_IDS[0]].get(f"/api/tables/{room['room_id']}"))
            with sqlite3.connect(api.db_path) as connection:
                connection.execute("UPDATE user_coins SET balance=999 WHERE user_id=?", (int(USER_IDS[3]),))
            rejected = await _post(clients[USER_IDS[0]], "start", room_id=room["room_id"])
            assert rejected.status_code == 402
            assert _room(await clients[USER_IDS[0]].get(f"/api/tables/{room['room_id']}")) == before
            assert _balances(api, clients) == {uid: 999 if uid == USER_IDS[3] else 2000 for uid in clients}
            assert _transactions(api) == []
    asyncio.run(scenario())


def _install_legal_hands(api, room_id, hands, missing, draws):
    """保留完整108张实体牌，仅固定牌局牌面来覆盖低概率多胡结算。"""
    from importlib import import_module

    module = import_module("src.chat.features.games.blackjack-web.sichuan_mahjong")
    engine = api.module.table_service._room(room_id).engine
    remaining = Counter({tile: 4 for tile in module.TILES})
    for hand in hands.values():
        remaining.subtract(hand)
    remaining.subtract(draws)
    assert min(remaining.values()) >= 0
    engine.hands = {uid: engine._sorted(hand) for uid, hand in hands.items()}
    engine.missing_suits = dict(missing)
    engine.wall = list(remaining.elements()) + list(reversed(draws))
    assert sum(map(len, engine.hands.values())) + len(engine.wall) == 108


def test_first_two_self_draws_remain_escrowed_third_settles_all_once_with_loss_cap(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:4]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, GAME_TYPE, room_tier="custom", base_stake=10, loss_limit=100)
            room = await _ready_and_start(clients, room["room_id"])
            room, _ = await _choose_missing_suits(clients, room)
            patterns = ["1", "2", "3", "1", "2", "3", "4", "5", "6", "7", "8", "9", "5", "5"]
            hands = {uid: [suit + number for number in patterns] for uid, suit in zip(ids, "mps")}
            hands[ids[1]].remove("p5")
            hands[ids[2]].remove("s5")
            hands[ids[3]] = ["m4", "m4", "m4", "m6", "m6", "m6", "p4", "p4", "p4", "p6", "p6", "p6", "s4"]
            _install_legal_hands(api, room["room_id"], hands, dict(zip(ids, ["p", "s", "m", "s"])), ["p5", "s5"])
            frozen = _balances(api, ids)
            transactions = _transactions(api)
            event_ids = []
            for index, uid in enumerate(ids[:3], start=1):
                before = _room(await clients[uid].get(f"/api/tables/{room['room_id']}"))
                assert before["game"]["current_player_id"] == uid
                assert "win" in before["game"]["legal_actions"]
                request = dict(room_id=room["room_id"], action="win", expected_revision=before["revision"])
                if index == 3:
                    responses = await asyncio.gather(*(_post(clients[uid], "action", **request) for _ in range(2)))
                    assert sorted(response.status_code for response in responses) == [200, 409]
                    room = _room(next(response for response in responses if response.status_code == 200))
                else:
                    room = _room(await _post(clients[uid], "action", **request))
                assert room["game"]["winners"] == list(ids[:index])
                events = room["game"]["win_events"]
                assert [event["id"] for event in events[:-1]] == event_ids
                event_ids = [event["id"] for event in events]
                assert len(set(event_ids)) == index
                if index < 3:
                    assert room["state"] == "playing"
                    assert not room["game"]["finished"]
                    assert room["settlement_status"] == "reserved"
                    assert room["actual_settlement"] == {}
                    assert _balances(api, ids) == frozen
                    assert _transactions(api) == transactions
                    spectator = _room(await clients[ids[3]].get(f"/api/tables/{room['room_id']}"))
                    for player in spectator["game"]["players"]:
                        if player["user_id"] in ids[:index]:
                            assert player["has_won"]
                            assert player["hand"] == []
                    denied = await _post(clients[uid], "action", room_id=room["room_id"], action="discard", tile=hands[uid][0], expected_revision=room["revision"])
                    assert denied.status_code == 400
            assert room["state"] == "finished"
            assert room["settlement_status"] == "settled"
            theory = {player["user_id"]: player["score_delta"] for player in room["game"]["players"]}
            assert theory == dict(zip(ids, [240, 80, -80, -240]))
            assert room["actual_settlement"] == dict(zip(ids, [135, 45, -80, -100]))
            assert _balances(api, ids) == {uid: INITIAL_BALANCE + room["actual_settlement"][uid] for uid in ids}
            settled_transactions = _transactions(api)
            assert len(settled_transactions) == 7
            for client in clients.values():
                _room(await client.get(f"/api/tables/{room['room_id']}"))
            assert _transactions(api) == settled_transactions
            assert sum(row[1] for row in settled_transactions) == 0
    asyncio.run(scenario())


def test_one_discard_three_winners_waits_for_all_responses_before_wallet_settlement(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:4]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, GAME_TYPE, room_tier="custom", base_stake=10, loss_limit=100)
            room = await _ready_and_start(clients, room["room_id"])
            room, _ = await _choose_missing_suits(clients, room)
            triplets = [("m1", "m2", "m3", "p1"), ("m4", "m6", "m7", "p2"), ("m8", "m9", "p3", "p4")]
            hands = {uid: [tile for tile in group for _ in range(3)] + ["m5"] for uid, group in zip(ids[1:], triplets)}
            hands[ids[0]] = ["m5", "p5", "p6", "p7", "p8", "p9", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"]
            _install_legal_hands(api, room["room_id"], hands, dict(zip(ids, ["m", "s", "s", "s"])), [])
            room = _room(await _post(clients[ids[0]], "action", room_id=room["room_id"], action="discard", tile="m5", expected_revision=room["revision"]))
            assert room["game"]["phase"] == "reaction"
            frozen = _balances(api, ids)
            for index, uid in enumerate(ids[1:], start=1):
                assert room["game"]["current_player_id"] == uid
                room = _room(await _post(clients[uid], "action", room_id=room["room_id"], action="win", expected_revision=room["revision"]))
                assert len(room["game"]["winners"]) == index
                if index < 3:
                    assert room["state"] == "playing"
                    assert room["settlement_status"] == "reserved"
                    assert _balances(api, ids) == frozen
            assert room["state"] == "finished"
            assert room["settlement_status"] == "settled"
            assert room["actual_settlement"] == dict(zip(ids, [-60, 20, 20, 20]))
            assert [event["source_id"] for event in room["game"]["win_events"]] == [ids[0]] * 3
            assert _balances(api, ids) == {uid: INITIAL_BALANCE + room["actual_settlement"][uid] for uid in ids}
            assert sum(row[1] for row in _transactions(api)) == 0
    asyncio.run(scenario())


def test_sichuan_extreme_base_is_rejected_at_creation_and_settings_without_mutation(api):
    from importlib import import_module

    rules = import_module("src.chat.features.games.blackjack-web.sichuan_mahjong")
    too_large = rules.MAX_BASE_STAKE + 1
    async def scenario():
        async with AsyncExitStack() as stack:
            client = next(iter((await _clients(stack, api, USER_IDS[:1])).values()))
            with sqlite3.connect(api.db_path) as connection:
                connection.execute("UPDATE user_coins SET balance=?", (10 * too_large,))
            rejected = await _post(client, "create", game_type=GAME_TYPE, mode="solo", room_tier="custom", base_stake=too_large, loss_limit=10 * too_large)
            assert rejected.status_code == 400, rejected.text
            assert "精度" in rejected.json()["detail"]
            assert not api.module.table_service.rooms
            before = _room(await _post(client, "create", game_type=GAME_TYPE, mode="solo", room_tier="custom"))
            rejected = await _post(client, "settings", room_id=before["room_id"], base_stake=too_large, loss_limit=10 * too_large)
            assert rejected.status_code == 400
            assert _room(await client.get(f"/api/tables/{before['room_id']}")) == before
            assert _transactions(api) == []
    asyncio.run(scenario())


def test_kong_and_exhausted_wall_keep_theory_until_flow_check_and_refund_once(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:4]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, GAME_TYPE)
            room = await _ready_and_start(clients, room["room_id"])
            room, _ = await _choose_missing_suits(clients, room)
            hands = {
                ids[0]: ["m2"] * 4 + ["m1", "m4", "m7", "p1", "p3", "p5", "p7", "p9", "m9", "m6"],
                ids[1]: ["m3", "m4", "m5", "m6", "m7", "m8", "p1", "p2", "p3", "p7", "p8", "p9", "p5"],
                ids[2]: ["m1", "m3", "m5", "m7", "m9", "p1", "p3", "p5", "p7", "p9", "m4", "m6", "m8"],
                ids[3]: ["m1", "m3", "m5", "m7", "m9", "p1", "p3", "p5", "p7", "p9", "m4", "m6", "s1"],
            }
            _install_legal_hands(api, room["room_id"], hands, {uid: "s" for uid in ids}, [])
            engine = api.module.table_service._room(room["room_id"]).engine
            # 构造接近摸空的合法实体牌局：其余牌放入公开弃牌，留一张作杠后补牌。
            unused = list(engine.wall)
            unused.remove("s9")
            engine.discards[ids[3]] = unused
            engine.wall = ["s9"]
            before_transactions = _transactions(api)
            room = _room(await _post(clients[ids[0]], "action", room_id=room["room_id"], action="kong", tile="m2", expected_revision=room["revision"]))
            assert room["state"] == "playing"
            assert room["settlement_status"] == "reserved"
            assert room["actual_settlement"] == {}
            assert [player["score_delta"] for player in room["game"]["players"]] == [6, -2, -2, -2]
            assert _transactions(api) == before_transactions
            assert _balances(api, ids) == {uid: INITIAL_BALANCE - 100 for uid in ids}
            room = _room(await _post(clients[ids[0]], "action", room_id=room["room_id"], action="discard", tile="s9", expected_revision=room["revision"]))
            assert room["state"] == "finished"
            assert room["settlement_status"] == "settled"
            flow = room["game"]["flow_details"]
            assert flow["flower_pigs"] == [ids[3]]
            assert set(flow["ready_players"]) == {ids[1]}
            assert set(flow["not_ready_players"]) == {ids[0], ids[2]}
            assert len(flow["kong_refunds"]) == 3
            assert room["actual_settlement"] == dict(zip(ids, [31, 34, 31, -96]))
            assert _balances(api, ids) == {uid: INITIAL_BALANCE + room["actual_settlement"][uid] for uid in ids}
            transactions = _transactions(api)
            for client in clients.values():
                _room(await client.get(f"/api/tables/{room['room_id']}"))
            assert _transactions(api) == transactions
            assert sum(row[1] for row in transactions) == 0
    asyncio.run(scenario())
