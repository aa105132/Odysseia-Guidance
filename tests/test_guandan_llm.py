"""掼蛋模型仅接收当前月月视角，保留双副牌身份和公开贡牌信息。"""

import copy
import importlib
import json

import pytest

from test_game_llm import client, response

llm = importlib.import_module("src.chat.features.games.blackjack-web.game_llm")
guandan = importlib.import_module("src.chat.features.games.blackjack-web.guandan_game")
IDS = ["human:secret", "bot:one", "bot:two", "bot:three"]


def test_model_candidates_are_bounded_and_varied_without_losing_hand_or_memory():
    game = guandan.GuandanGame(IDS, seed=42)
    uid = game.current_player_id
    original = game.public_state(uid)
    original["seat_actions"] = {IDS[1]: {"action": "play", "cards": ["Club3#0"]}}
    state = llm._context("guandan", original, uid)["state"]
    options = state["play_options"]
    assert 0 < len(options) <= 24
    assert {option["kind"] for option in options} == {option["kind"] for option in original["play_options"]}
    own = next(player for player in state["players"] if player["hand"])
    assert own["hand"] == game.hands[uid] and len(own["hand"]) == 27
    assert state["seat_actions"]["seat_2"]["cards"] == ["Club3#0"]
    assert len(original["play_options"]) == 80
    for option in options:
        copy.deepcopy(game).act(uid, "play", cards=option["cards"])


def test_context_keeps_public_tribute_and_masks_all_identities():
    game = guandan.GuandanGame(IDS, seed=21)
    uid = game.current_player_id
    game.tribute_events = [
        {"kind": "tribute", "user_id": IDS[0], "target_id": IDS[1], "card": "ClubA#1", "automatic": True},
        {"kind": "resist", "user_ids": IDS[2:], "automatic": True},
    ]
    game.finish_order = [IDS[2]]
    context = llm.build_table_context("guandan", game, uid)
    body = llm.conversation_user_message(context)["content"]
    assert all(identity not in body for identity in IDS)
    state = context["state"]
    assert state["tribute_events"][0] == {"kind": "tribute", "user_id": "seat_1", "target_id": "seat_2", "card": "ClubA#1", "automatic": True}
    assert state["tribute_events"][1]["user_ids"] == ["seat_3", "seat_4"]
    assert state["finish_order"] == ["seat_3"]
    assert state["play_options"] and len(body.encode()) < 65536
    for player in state["players"]:
        assert len(player["hand"]) == (27 if player["user_id"] == context["you"] else 0)
    assert all("#" in card for option in state["play_options"] for card in option["cards"])


@pytest.mark.asyncio
async def test_model_valid_action_uses_unique_cards_without_token_cap(monkeypatch):
    game = guandan.GuandanGame(IDS, seed=21)
    uid = game.current_player_id
    context = llm.build_table_context("guandan", game, uid)
    action = {"action": "play", "cards": context["state"]["play_options"][0]["cards"]}
    def handler(request):
        payload = json.loads(request.content)
        assert "max_tokens" not in payload and "max_completion_tokens" not in payload
        assert payload["reasoning_effort"] == "low"
        assert all(identity not in json.dumps(payload) for identity in IDS)
        return response(action)
    result = await client(monkeypatch, handler).choose_action(context)
    assert result == action
    payload = dict(result)
    copy.deepcopy(game).act(uid, payload.pop("action"), **payload)
