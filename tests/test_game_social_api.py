"""快捷聊天真实 ASGI 权限、游标及房间实例隔离回归。"""

import asyncio
import copy
from contextlib import AsyncExitStack

from test_table_multiplayer_api import api, USER_IDS, _clients, _create_and_join, _ready_and_start


async def prepare(stack, api, scope):
    clients = await _clients(stack, api, USER_IDS[:3])
    first, second, _ = clients.values()
    if scope == "table":
        room = await _create_and_join({uid: clients[uid] for uid in USER_IDS[:2]}, "texas")
        room_id = room["room_id"]
    else:
        created = await first.post("/api/multi/room/create", json={})
        room_id = created.json()["room"]["room_id"]
        joined = await second.post("/api/multi/room/join", json={"room_id": room_id})
        assert joined.status_code == 200
    return clients, room_id, f"/api/game-social/{scope}/{room_id}"


def test_table_and_blackjack_social_incremental_broadcast_permissions_and_limits(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            for scope in ("table", "blackjack"):
                clients, _, url = await prepare(stack, api, scope)
                first, second, outsider = clients.values()
                initial = await second.get(url)
                assert initial.status_code == 200 and initial.json()["events"] == []
                assert len(initial.json()["catalog"]["chat"]) == 11
                sent = await first.post(url, json={"kind": "chat", "item_id": "mm_or_gg"})
                assert sent.status_code == 200
                event = sent.json()["event"]
                assert event["text"] == "你是MM还是GG？"
                assert event["user_id"] == USER_IDS[0]
                assert (await second.get(url)).json()["events"] == []
                delta = (await second.get(url, params={"after": initial.json()["cursor"]})).json()
                assert delta["events"] == [event]
                assert (await second.get(url, params={"after": delta["cursor"]})).json()["events"] == []
                limited = await first.post(url, json={"kind": "chat", "item_id": "nice"})
                assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
                for method in ("GET", "POST"):
                    kwargs = {"json": {"kind": "chat", "item_id": "hello"}} if method == "POST" else {}
                    assert (await outsider.request(method, url, **kwargs)).status_code == 403
                wrong_target = await second.post(url, json={"kind": "interaction", "item_id": "tea", "target_id": USER_IDS[2]})
                assert wrong_target.status_code == 400
                assert (await second.post(url, json={"kind": "chat", "item_id": "任意文案"})).status_code == 400
                assert (await second.post(url, json={"kind": "chat", "item_id": "hello", "text": "注入文本"})).status_code == 422
                assert (await second.post(url, json={"kind": "interaction", "item_id": "flower", "target_id": USER_IDS[0]})).status_code == 200
    asyncio.run(scenario())


def test_room_round_changes_and_deepcopy_preserve_scope_but_same_code_new_instance_does_not(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients, room_id, url = await prepare(stack, api, "table")
            first = clients[USER_IDS[0]]
            sent = await first.post(url, json={"kind": "chat", "item_id": "hello"})
            event = sent.json()["event"]
            service = api.module.table_service
            original = service.rooms[room_id]
            original.round_number += 1
            service.rooms[room_id] = copy.deepcopy(original)
            assert (await first.get(url, params={"after": 0})).json()["events"] == [event]
            replacement = api.module._table_module.GameTable(room_id, "texas", USER_IDS[0], "multi", 100)
            replacement.players = copy.deepcopy(original.players)
            service.rooms[room_id] = replacement
            assert (await first.get(url, params={"after": 0})).json()["events"] == []
            new_event = (await first.post(url, json={"kind": "chat", "item_id": "again"})).json()["event"]
            assert new_event["event_id"] > event["event_id"]
    asyncio.run(scenario())


def test_player_who_left_running_table_cannot_send_read_or_receive_interactions(api):
    async def scenario():
        async with AsyncExitStack() as stack:
            clients, room_id, url = await prepare(stack, api, "table")
            participants = {uid: clients[uid] for uid in USER_IDS[:2]}
            await _ready_and_start(participants, room_id)
            # 服务层离房保留结算座位，社交权限不能把这个座位继续视作在线成员。
            api.module.table_service.leave(room_id, USER_IDS[0])
            first, second, _ = clients.values()
            assert (await first.get(url)).status_code == 403
            assert (await first.post(url, json={"kind": "chat", "item_id": "hello"})).status_code == 403
            assert (await second.post(url, json={"kind": "interaction", "item_id": "tea", "target_id": USER_IDS[0]})).status_code == 400
    asyncio.run(scenario())
