"""掼蛋真实 ASGI 回归：完整牌局、钱包、历史统计与牌型选择。"""

import asyncio
import sqlite3
from contextlib import AsyncExitStack

from test_table_multiplayer_api import (
    INITIAL_BALANCE,
    USER_IDS,
    _balances,
    _clients,
    _create_and_join,
    _finish_through_requests,
    _ledger,
    _post,
    _ready_and_start,
    _room,
    api,
)


async def _get_json(client, path):
    response = await client.get(path)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    return payload


def _transactions(api):
    with sqlite3.connect(api.db_path) as connection:
        return connection.execute(
            "SELECT user_id, amount, reason FROM coin_transactions ORDER BY id"
        ).fetchall()


def test_guandan_human_and_three_bots_complete_two_rounds_with_history_and_stats(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            uid, outsider = USER_IDS[:2]
            clients = await _clients(stack, api, [uid, outsider])
            human = {uid: clients[uid]}
            room = _room(await _post(
                clients[uid], "create", game_type="guandan", mode="solo",
                include_yueyue=True,
            ))
            rid = room["room_id"]
            assert room["min_players"] == room["max_players"] == 4
            assert sum(player["is_bot"] for player in room["players"]) == 3
            seats = [player["user_id"] for player in room["players"]]
            round_keys = []
            total_profit = 0
            prior_levels = None

            for round_number in (1, 2):
                started_at = api.clock()
                room = await _ready_and_start(human, rid)
                assert room["state"] == "playing"
                assert room["round_number"] == round_number
                assert room["settlement_status"] == "reserved"
                assert room["actual_settlement"] == {}
                assert [player["user_id"] for player in room["game"]["players"]] == seats
                assert _balances(api, [uid])[uid] == INITIAL_BALANCE + total_profit - room["stake"]
                key = api.module.table_service._room(rid).escrow_key
                assert key not in round_keys
                round_keys.append(key)
                if prior_levels is not None:
                    assert room["game"]["team_levels"] == prior_levels
                    assert room["game"]["tribute_events"]

                finished = await _finish_through_requests(api, human, room)
                assert finished["settlement_status"] == "settled"
                assert set(finished["actual_settlement"]) == set(seats)
                assert sum(finished["actual_settlement"].values()) == 0
                assert finished["game"]["settlement"] == finished["actual_settlement"]
                assert set(finished["game"]["finish_order"]) == set(seats)
                profit = finished["actual_settlement"][uid]
                total_profit += profit
                prior_levels = finished["game"]["team_levels"]
                assert _balances(api, [uid, outsider]) == {
                    uid: INITIAL_BALANCE + total_profit,
                    outsider: INITIAL_BALANCE,
                }
                ledger = _ledger(api)
                assert len(ledger) == round_number
                assert all(row[0] == int(uid) and row[3] == "settled" for row in ledger)
                assert sum(row[2] - row[1] for row in ledger) == total_profit

                history = await _get_json(clients[uid], "/api/tables/history?game_type=guandan")
                assert history["total"] == round_number
                assert {entry["round_key"] for entry in history["entries"]} == set(round_keys)
                entry = next(entry for entry in history["entries"] if entry["round_key"] == key)
                assert entry["game_type"] == "guandan" and entry["has_details"]
                assert entry["profit"] == profit
                assert entry["stake"] == finished["stake"]
                assert entry["payout"] == finished["stake"] + profit
                detail = (await _get_json(clients[uid], f"/api/tables/history/{key}"))["round"]["details"]
                assert detail["room_id"] == rid
                assert detail["started_at"] == started_at
                assert detail["history_truncated"] is False
                assert detail["final_state"]["finished"] is True
                assert detail["final_state"]["settlement"] == finished["actual_settlement"]
                assert sum(player["is_bot"] for player in detail["players"]) == 3
                assert detail["actions"]
                assert {action["action"] for action in detail["actions"]} <= {"play", "pass"}
                played_cards = [card for action in detail["actions"] for card in action.get("cards", [])]
                assert len(played_cards) == len(set(played_cards))
                assert len(played_cards) + sum(player["hand_count"] for player in detail["final_state"]["players"]) == 108
                assert (await clients[outsider].get(f"/api/tables/history/{key}")).status_code == 404

                stats = (await _get_json(clients[uid], "/api/tables/stats?game_type=guandan"))["stats"]
                assert stats["rounds"] == round_number
                assert stats["wins"] + stats["losses"] + stats["draws"] == round_number
                assert stats["net_profit"] == stats["today_profit"] == total_profit
                assert stats["average_profit"] == total_profit / round_number
                before_poll = _transactions(api)
                _room(await clients[uid].get(f"/api/tables/{rid}"))
                _room(await clients[uid].get(f"/api/tables/{rid}"))
                assert _transactions(api) == before_poll

            assert len(_transactions(api)) == 4
            assert (await _get_json(clients[uid], "/api/tables/stats?game_type=landlord"))["stats"]["rounds"] == 0
            assert (await _get_json(clients[uid], "/api/tables/history?game_type=landlord"))["total"] == 0
            assert (await _get_json(clients[outsider], "/api/tables/history?game_type=guandan"))["total"] == 0
            board = await _get_json(clients[uid], "/api/tables/leaderboard?game_type=guandan&period=all")
            assert [entry["user_id"] for entry in board["entries"]] == [uid]
            assert board["entries"][0]["net_profit"] == total_profit

    asyncio.run(asyncio.wait_for(scenario(), timeout=55))


def test_guandan_four_real_players_keep_hands_private_and_wallet_zero_sum(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            ids = USER_IDS[:4]
            clients = await _clients(stack, api, ids)
            room = await _create_and_join(clients, "guandan")
            room = await _ready_and_start(clients, room["room_id"])
            assert _balances(api, ids) == {uid: INITIAL_BALANCE - room["stake"] for uid in ids}
            for uid, client in clients.items():
                view = _room(await client.get(f"/api/tables/{room['room_id']}"))
                for player in view["game"]["players"]:
                    assert player["hand_count"] == 27
                    assert len(player["hand"]) == (27 if player["user_id"] == uid else 0)

            finished = await _finish_through_requests(api, clients, room)
            assert finished["settlement_status"] == "settled"
            assert sum(finished["actual_settlement"].values()) == 0
            balances = _balances(api, ids)
            assert sum(balances.values()) == INITIAL_BALANCE * 4
            assert balances == {uid: INITIAL_BALANCE + finished["actual_settlement"][uid] for uid in ids}
            assert len(_ledger(api)) == 4
            assert sum(row[2] - row[1] for row in _ledger(api)) == 0
            assert sum(row[1] for row in _transactions(api)) == 0
            for uid, client in clients.items():
                stats = (await _get_json(client, "/api/tables/stats?game_type=guandan"))["stats"]
                assert stats["rounds"] == 1
                assert stats["net_profit"] == finished["actual_settlement"][uid]

    asyncio.run(asyncio.wait_for(scenario(), timeout=55))


def test_guandan_manual_combo_reaches_engine_and_invalid_choice_is_atomic(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients = await _clients(stack, api, USER_IDS[:4])
            room = await _create_and_join(clients, "guandan")
            room = await _ready_and_start(clients, room["room_id"])
            uid = room["game"]["current_player_id"]
            view = _room(await clients[uid].get(f"/api/tables/{room['room_id']}"))
            by_cards = {}
            for candidate in view["game"]["play_options"]:
                by_cards.setdefault(tuple(candidate["cards"]), []).append(candidate)
            interpretations = next(group for group in by_cards.values() if len(group) > 1)
            # 同一组带逢人配的牌选择较强解释，确保接口没有默默丢弃 combo。
            option = interpretations[-1]
            assert option["combo"] != interpretations[0]["combo"]
            frozen = _balances(api, clients)
            before_transactions = _transactions(api)
            rejected = await _post(
                clients[uid], "action", room_id=room["room_id"], action="play",
                expected_revision=view["revision"], cards=option["cards"], combo="不存在的牌型",
            )
            assert rejected.status_code == 400
            unchanged = _room(await clients[uid].get(f"/api/tables/{room['room_id']}"))
            assert unchanged["revision"] == view["revision"]
            assert unchanged["game"] == view["game"]
            assert unchanged["last_public_action"] is None
            assert _balances(api, clients) == frozen
            assert _transactions(api) == before_transactions

            played = _room(await _post(
                clients[uid], "action", room_id=room["room_id"], action="play",
                expected_revision=view["revision"], cards=option["cards"], combo=option["combo"],
            ))
            assert played["game"]["last_play"]["combo"] == option["combo"]
            assert played["game"]["last_play"]["cards"] == option["cards"]
            own = next(player for player in played["game"]["players"] if player["user_id"] == uid)
            assert own["hand_count"] == 27 - len(option["cards"])
            assert played["last_public_action"]["cards"] == option["cards"]
            assert played["last_public_action"]["combo"] == option["combo"]
            assert played["last_public_action"]["user_id"] == uid
            assert _balances(api, clients) == frozen

    asyncio.run(asyncio.wait_for(scenario(), timeout=55))
