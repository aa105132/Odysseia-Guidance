"""房间大厅摘要：覆盖真实接口、加入规则、隐私和只读行为。"""

import asyncio
import copy
import httpx
from contextlib import AsyncExitStack
from unittest.mock import Mock

from test_table_multiplayer_api import USER_IDS, _clients, api


def test_directory_requires_authentication_even_from_loopback(api, monkeypatch):
    api.module.app.dependency_overrides.clear()
    monkeypatch.setenv("BLACKJACK_ALLOW_DEV_AUTH", "false")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.module.app, client=("127.0.0.1", 1234)), base_url="http://testserver") as client:
            response = await client.get("/api/rooms", headers={"X-Dev-User-Id": USER_IDS[0]})
            assert response.status_code == 401

    asyncio.run(scenario())


def test_directory_filters_expired_abandoned_room_without_removing_or_refunding(api):
    service = api.module.table_service
    snapshot = _table(api)
    room = service.rooms[snapshot["room_id"]]
    room.players[USER_IDS[0]].connected = False
    room.updated_at = service.clock() - service.EMPTY_ROOM_TTL - 1
    assert service.list_rooms(USER_IDS[0]) == []
    assert room.room_id in service.rooms
    room.settlement_status = "reserved"
    assert service.list_rooms(USER_IDS[0])[0]["is_member"] is True
    assert room.settlement_status == "reserved"


def _user(index):
    return {"user_id": USER_IDS[index], "username": f"房主{index}", "avatar_url": "/character/normal.webp"}


def _table(api, index=0, game_type="texas", mode="multi", **settings):
    return api.module.table_service.create(_user(index), game_type, mode, False, **settings)


def _blackjack(api, index=1):
    user = _user(index)
    return api.module.multiplayer_blackjack_service.create_room(
        int(user["user_id"]), user["username"], user["avatar_url"]
    )


def test_directory_combines_games_excludes_solo_and_exposes_only_summary(api):
    table = _table(api)
    blackjack = _blackjack(api)
    _table(api, index=2, game_type="mahjong", mode="solo")

    async def scenario():
        async with AsyncExitStack() as stack:
            client = (await _clients(stack, api, USER_IDS[3:4]))[USER_IDS[3]]
            response = await client.get("/api/rooms")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 2
            assert {room["room_id"] for room in payload["rooms"]} == {table["room_id"], blackjack["room_id"]}
            allowed = {
                "room_id", "game_type", "host_username", "host_avatar_url", "state",
                "player_count", "max_players", "room_tier", "base_stake", "entry_min",
                    "loss_limit", "turn_timeout_seconds", "is_member", "can_join", "updated_at",
            }
            assert all(set(room) == allowed and room["can_join"] for room in payload["rooms"])
            filtered = await client.get("/api/rooms", params={"game_type": "blackjack"})
            assert [room["room_id"] for room in filtered.json()["rooms"]] == [blackjack["room_id"]]
            invalid = await client.get("/api/rooms", params={"game_type": "unknown"})
            assert invalid.status_code == 400
            invalid_page = await client.get("/api/rooms", params={"limit": 201})
            assert invalid_page.status_code == 422

    asyncio.run(scenario())


def test_directory_does_not_advance_or_touch_game_and_wallet(api, monkeypatch):
    table = _table(api)
    blackjack = _blackjack(api)
    table_room = api.module.table_service.rooms[table["room_id"]]
    bj_room = api.module.multiplayer_blackjack_service._rooms[blackjack["room_id"]]
    table_room.state = "playing"
    table_room.turn_deadline = 0
    bj_room.state = "playing"
    bj_room.turn_deadline = 0
    before_table = copy.deepcopy(vars(table_room))
    before_bj = copy.deepcopy(vars(bj_room))
    for obj, method in [
        (api.module.table_service, "_advance_due_turn"),
        (api.module.table_service, "_snapshot"),
        (api.module.multiplayer_blackjack_service, "_to_room_state"),
        (api.module.multiplayer_blackjack_service, "_get_room_or_raise"),
        (api.module, "_ensure_user_balance"),
    ]:
        monkeypatch.setattr(obj, method, Mock(side_effect=AssertionError("列表不得读取私有牌局或修改余额")))

    async def scenario():
        async with AsyncExitStack() as stack:
            client = (await _clients(stack, api, USER_IDS[3:4]))[USER_IDS[3]]
            response = await client.get("/api/rooms")
            assert response.status_code == 200
            assert all(not room["can_join"] for room in response.json()["rooms"])

    asyncio.run(scenario())
    assert vars(table_room) == before_table
    assert vars(bj_room) == before_bj


def test_table_directory_matches_bot_replacement_and_member_reconnection(api):
    service = api.module.table_service
    room = _table(api, game_type="landlord")
    service.bots(room["room_id"], USER_IDS[0], "add", 2)
    public = service.list_rooms(USER_IDS[1])[0]
    assert public["player_count"] == public["max_players"] == 3
    assert public["can_join"] is True
    service.join(room["room_id"], _user(1))
    assert service.list_rooms(USER_IDS[2])[0]["can_join"] is False
    live = service.rooms[room["room_id"]]
    live.state = "playing"
    live.players[USER_IDS[1]].connected = False
    assert service.list_rooms(USER_IDS[1])[0]["can_join"] is True
    assert service.list_rooms(USER_IDS[1])[0]["is_member"] is True
    assert service.list_rooms(USER_IDS[2])[0]["can_join"] is False


def test_table_directory_hides_abandoned_rooms_and_prevents_joining_two_tables(api):
    service = api.module.table_service
    first = _table(api)
    second = _table(api, index=1)
    listing = {room["room_id"]: room for room in service.list_rooms(USER_IDS[0])}
    assert listing[first["room_id"]]["can_join"] is True
    assert listing[second["room_id"]]["can_join"] is False
    service.rooms[second["room_id"]].players[USER_IDS[1]].connected = False
    assert len(service.list_rooms(USER_IDS[0])) == 1
    assert len(service.list_rooms(USER_IDS[1])) == 2


def test_blackjack_directory_blocks_full_or_playing_rooms_but_allows_members(api):
    service = api.module.multiplayer_blackjack_service
    room = _blackjack(api, index=0)
    service.join_room(room["room_id"], int(USER_IDS[1]), "来客", "")
    service.configure_bot(room["room_id"], int(USER_IDS[0]), True)
    assert service.list_rooms(int(USER_IDS[2]))[0]["can_join"] is False
    assert service.list_rooms(int(USER_IDS[0]))[0]["can_join"] is True
    service._rooms[room["room_id"]].state = "playing"
    assert service.list_rooms(int(USER_IDS[0]))[0]["is_member"] is True


def test_directory_pagination_and_current_room_sorting(api):
    table = _table(api)
    _blackjack(api)

    async def scenario():
        async with AsyncExitStack() as stack:
            client = (await _clients(stack, api, USER_IDS[:1]))[USER_IDS[0]]
            first = (await client.get("/api/rooms", params={"limit": 1})).json()
            second = (await client.get("/api/rooms", params={"limit": 1, "offset": 1})).json()
            assert first["total"] == second["total"] == 2
            assert first["rooms"][0]["room_id"] == table["room_id"]
            assert first["rooms"][0]["is_member"] is True
            assert second["rooms"][0]["room_id"] != table["room_id"]

    asyncio.run(scenario())
