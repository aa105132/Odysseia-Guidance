"""21点房间等待时长真实请求与服务端计时验证。"""

from unittest.mock import patch

import pytest

from test_blackjack_multiplayer_api import api, _client, _request, _create_room, _start_round, HOST_ID, GUEST_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [15, 60, 300])
async def test_create_and_auto_join_use_requested_timeout_without_guest_override(api, seconds):
    async with _client(api) as client:
        response = await _request(client, "create", turn_timeout_seconds=seconds)
        assert response.status_code == 200
        assert response.json()["room"]["turn_timeout_seconds"] == seconds
        auto = await _request(client, "auto-join", session_key=f"session-{seconds}", turn_timeout_seconds=seconds)
        assert auto.status_code == 200
        room = auto.json()["room"]
        assert room["turn_timeout_seconds"] == seconds
        join = await _request(client, "auto-join", GUEST_ID, session_key=f"session-{seconds}", turn_timeout_seconds=90)
        assert join.status_code == 200
        assert join.json()["room"]["turn_timeout_seconds"] == seconds
        default = await _request(client, "create")
        assert default.json()["room"]["turn_timeout_seconds"] == 60


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [True, False, "60", 60.0, 14, 301, None])
async def test_create_auto_join_and_settings_reject_noninteger_or_out_of_range(api, seconds):
    async with _client(api) as client:
        room_id = await _create_room(client)
        for action, extra in (("create", {}), ("auto-join", {"session_key": "strict"}), ("settings", {"room_id": room_id})):
            response = await _request(client, action, turn_timeout_seconds=seconds, **extra)
            assert response.status_code == 422, response.text
        assert api.module.multiplayer_blackjack_service._rooms[room_id].turn_timeout_seconds == 60


@pytest.mark.asyncio
async def test_only_host_can_change_settings_and_real_players_must_ready_again(api):
    async with _client(api) as client:
        room_id = await _create_room(client)
        service = api.module.multiplayer_blackjack_service
        service.configure_bot(room_id, HOST_ID, True)
        for uid in (HOST_ID, GUEST_ID):
            await _request(client, "bet", uid, room_id=room_id, amount=100)
            await _request(client, "ready", uid, room_id=room_id, ready=True)
        refused = await _request(client, "settings", GUEST_ID, room_id=room_id, turn_timeout_seconds=120)
        assert refused.status_code == 403
        same = await _request(client, "settings", room_id=room_id, turn_timeout_seconds=60)
        assert all(player["is_ready"] for player in same.json()["room"]["players"])
        changed = await _request(client, "settings", room_id=room_id, turn_timeout_seconds=120)
        assert changed.status_code == 200
        assert all(player["is_ready"] == player["is_bot"] for player in changed.json()["room"]["players"])
        assert api.coins.balances[HOST_ID] == api.coins.balances[GUEST_ID] == 900


@pytest.mark.asyncio
async def test_configured_deadline_survives_reconnect_and_in_play_setting_is_rejected(api):
    async with _client(api) as client:
        room_id = await _create_room(client)
        await _request(client, "settings", room_id=room_id, turn_timeout_seconds=120)
        with patch.object(api.engine.time, "time", return_value=1000):
            room = await _start_round(client, room_id)
        assert room["turn_deadline"] == 1120
        with patch.object(api.engine.time, "time", return_value=1040):
            reconnect = await _request(client, "join", room_id=room_id)
            assert reconnect.status_code == 200
            assert reconnect.json()["room"]["turn_deadline"] == 1120
            refused = await _request(client, "settings", room_id=room_id, turn_timeout_seconds=15)
            assert refused.status_code == 400
        assert api.module.multiplayer_blackjack_service._rooms[room_id].turn_timeout_seconds == 120
