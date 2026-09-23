"""Jev 评估接入：协议、真实引擎候选、独立记忆与失败回退。"""

import asyncio
import copy
import importlib
import json
import random
from types import SimpleNamespace

import httpx
import pytest


llm = importlib.import_module("src.chat.features.games.blackjack-web.game_llm")
jev = importlib.import_module("src.chat.features.games.blackjack-web.game_jev")
poker = importlib.import_module("src.chat.features.games.blackjack-web.poker_games")
traditional = importlib.import_module("src.chat.features.games.blackjack-web.traditional_games")
guandan = importlib.import_module("src.chat.features.games.blackjack-web.guandan_game")
sichuan = importlib.import_module("src.chat.features.games.blackjack-web.sichuan_mahjong")
runner_module = importlib.import_module("src.chat.features.games.blackjack-web.game_bot_runner")
blackjack = importlib.import_module("src.chat.features.games.blackjack-web.multiplayer_service")


def texas_context():
    game = poker.TexasHoldemGame(["PRIVATE-BOT", "PRIVATE-HUMAN"], seed=42)
    return llm.build_table_context("texas", game, game.current_player_id)


def answer(candidates, choice=None, probabilities=None):
    choice = choice or next(iter(candidates))
    return {"answers": {"action": {"type": "choice", "choice": choice,
            "probabilities": probabilities or {name: int(name == choice) for name in candidates}}}}


def client(monkeypatch, handler, model="jev"):
    monkeypatch.setenv("GAME_LLM_ENABLED", "true")
    monkeypatch.setenv("GAME_LLM_BASE_URL", "https://bufan.test/v1/")
    monkeypatch.setenv("GAME_LLM_API_KEY", "SYNTHETIC-KEY")
    monkeypatch.setenv("GAME_LLM_MODEL", model)
    monkeypatch.delenv("GAME_LLM_TIMEOUT_SECONDS", raising=False)
    original = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return llm.GameLLMClient()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["jev", "typesafe-ai/jev"])
async def test_evaluations_request_uses_choices_context_and_full_independent_history(monkeypatch, model):
    context = texas_context()
    context["public_action_history"] = [{"seat": "seat_2", "action": "raise", "amount": 4}]
    context["_conversation"] = [{"role": "user", "content": '{"own_previous_hand":["HeartA","ClubQ"]}'},
                                {"role": "assistant", "content": '{"action":"call"}'}]
    captured = []

    def handler(request):
        payload = json.loads(request.content)
        captured.append(payload)
        assert str(request.url) == "https://bufan.test/v1/evaluations"
        assert request.headers["Authorization"] == "Bearer SYNTHETIC-KEY"
        return httpx.Response(200, json=answer(payload["questions"]["action"]["criteria"], "call"))

    model_client = client(monkeypatch, handler, model)
    assert await model_client.choose_action(context) == {"action": "call"}
    payload = captured[0]
    assert model_client.timeout_seconds == 60
    assert set(payload) == {"model", "state", "questions"}
    assert payload["model"] == model
    assert payload["state"]["conversation"] == context["_conversation"]
    assert payload["state"]["current"]["public_action_history"] == context["public_action_history"]
    assert "_conversation" not in payload["state"]["current"]
    assert "_seat_ids" not in json.dumps(payload)
    assert "PRIVATE-" not in json.dumps(payload)
    candidates = payload["questions"]["action"]["criteria"]
    assert {"call", "fold", "all_in"} <= set(candidates)
    assert sum(name.startswith("raise_") for name in candidates) >= 3
    assert "max_tokens" not in payload and "reasoning_effort" not in payload


@pytest.mark.asyncio
async def test_normal_model_still_uses_chat_completions(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert "messages" in payload and "questions" not in payload
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"action":"call"}'}}]})

    assert await client(monkeypatch, handler, "打牌LLM").choose_action(texas_context()) == {"action": "call"}


@pytest.mark.parametrize("edit", [
    lambda row: row.update(answers={}),
    lambda row: row["answers"].update(other={}),
    lambda row: row["answers"]["action"].update(type="boolean"),
    lambda row: row["answers"]["action"].update(choice="unknown"),
    lambda row: row["answers"]["action"].update(choice=[]),
    lambda row: row["answers"]["action"].update(explanation="extra"),
    lambda row: row["answers"]["action"].update(probabilities={"call": 1}),
    lambda row: row["answers"]["action"]["probabilities"].update(call=True),
    lambda row: row["answers"]["action"]["probabilities"].update(call=float("nan")),
    lambda row: row["answers"]["action"]["probabilities"].update(call=float("inf")),
    lambda row: row["answers"]["action"]["probabilities"].update(call=-0.1),
    lambda row: row["answers"]["action"]["probabilities"].update(call=1.1),
    lambda row: row["answers"]["action"]["probabilities"].update(call=0),
    lambda row: row["answers"]["action"]["probabilities"].update(fold=0.3),
])
def test_invalid_evaluation_answers_are_rejected(edit):
    candidates = {"call": {"action": "call"}, "fold": {"action": "fold"}}
    response = answer(candidates, "call")
    edit(response)
    with pytest.raises(ValueError):
        jev.selected_action(response, candidates)


def test_rounded_probabilities_can_sum_to_099_or_101_and_choice_is_not_resampled():
    candidates = {"call": {"action": "call"}, "raise": {"action": "raise", "amount": 4}, "fold": {"action": "fold"}}
    assert jev.selected_action(answer(candidates, "raise", {"call": .33, "raise": .33, "fold": .33}), candidates) == candidates["raise"]
    assert jev.selected_action(answer(candidates, "call", {"call": .34, "raise": .34, "fold": .33}), candidates) == candidates["call"]
    # 使用服务明确给出的 choice，不把低概率折叠为另一次随机抽样。
    assert jev.selected_action(answer(candidates, "call", {"call": .1, "raise": .8, "fold": .1}), candidates) == candidates["call"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "unknown", "duplicate", "oversized", "timeout"])
async def test_protocol_failure_returns_none_for_existing_algorithm_fallback(monkeypatch, failure):
    async def handler(request):
        if failure == "http":
            return httpx.Response(500)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic")
        if failure == "oversized":
            return httpx.Response(200, content=b"x" * (llm.MAX_RESPONSE_BYTES + 1))
        if failure == "duplicate":
            return httpx.Response(200, content=b'{"answers":{},"answers":{}}')
        criteria = json.loads(request.content)["questions"]["action"]["criteria"]
        return httpx.Response(200, json=answer(criteria, "unknown"))

    assert await client(monkeypatch, handler).choose_action(texas_context()) is None


@pytest.mark.asyncio
async def test_compare_selected_target_is_mapped_back_from_anonymous_seat(monkeypatch):
    game = poker.GoldenFlowerGame(["PRIVATE-BOT", "PRIVATE-HUMAN"], seed=42)
    context = llm.build_table_context("golden_flower", game, game.current_player_id)

    def handler(request):
        criteria = json.loads(request.content)["questions"]["action"]["criteria"]
        target = next(name for name in criteria if name.startswith("compare_"))
        return httpx.Response(200, json=answer(criteria, target))

    result = await client(monkeypatch, handler).choose_action(context)
    assert result == {"action": "compare", "target_id": "PRIVATE-HUMAN"}
    copy.deepcopy(game).act(game.current_player_id, **result)


@pytest.mark.parametrize("kind,game_class,count", [
    ("texas", poker.TexasHoldemGame, 8),
    ("golden_flower", poker.GoldenFlowerGame, 5),
    ("landlord", traditional.LandlordGame, 3),
    ("guandan", guandan.GuandanGame, 4),
    ("mahjong", traditional.MahjongGame, 4),
    ("sichuan_mahjong", sichuan.SichuanMahjongGame, 4),
])
def test_every_generated_candidate_is_legal_on_real_engine_turns(kind, game_class, count):
    game = game_class([f"PRIVATE-{index}" for index in range(count)], seed=42)
    choice_rng = random.Random(12)
    seen_actions = set()
    for _ in range(16):
        if game.finished:
            break
        uid = game.current_player_id
        before = copy.deepcopy(game.public_state(uid))
        context = llm.build_table_context(kind, game, uid)
        candidates = jev.build_action_candidates(context)
        assert 1 <= len(candidates) <= jev.MAX_CANDIDATES
        for candidate in candidates.values():
            action = llm.GameLLMClient._validate_action(candidate, context)
            assert action is not None
            copy.deepcopy(game).act(uid, **action)
            seen_actions.add(action["action"])
        assert game.public_state(uid) == before
        choice = choice_rng.choice(list(candidates.values()))
        # 在德州保留局面以覆盖后续街，在斗地主进入实际出牌阶段。
        if kind == "texas":
            choice = {"action": "check" if "check" in before["legal_actions"] else "call"}
        elif kind == "landlord" and "bid" in before["legal_actions"]:
            choice = {"action": "bid", "bid": 3}
        game.act(uid, **llm.GameLLMClient._validate_action(choice, context))
    assert seen_actions


def test_landlord_lead_choices_cover_diverse_shapes_high_low_and_finishing_play():
    game = traditional.LandlordGame(["a", "b", "c"], seed=4)
    game.act(game.current_player_id, "bid", bid=3)
    uid = game.current_player_id
    game.hands[uid] = ["Club3", "Diamond3", "Heart3", "Spade3", "Club4", "Diamond4", "Heart4",
                       "Club5", "Diamond5", "Club6", "Club7", "Club8", "JokerSmall", "JokerBig"]
    context = llm.build_table_context("landlord", game, uid)
    candidates = jev.build_action_candidates(context)
    patterns = [traditional.classify_landlord_cards(row["cards"]) for row in candidates.values()]
    assert {"single", "pair", "triple", "straight", "bomb", "rocket"} <= {pattern.kind for pattern in patterns}
    assert len(candidates) <= jev.MAX_PLAY_CANDIDATES
    game.hands[uid] = ["Club3", "Diamond4", "Heart5", "Spade6", "Club7"]
    choices = jev.build_action_candidates(llm.build_table_context("landlord", game, uid))
    assert any(len(row["cards"]) == 5 for row in choices.values())


def test_sichuan_discard_candidates_respect_required_missing_suit():
    game = sichuan.SichuanMahjongGame(["a", "b", "c", "d"], seed=5)
    while game.phase == "dingque":
        game.act(game.current_player_id, "dingque", suit="m")
    uid = game.current_player_id
    game.hands[uid] = ["m1", "m2", "p3", "p4", "p5", "p6", "p7", "p8", "p9", "s1", "s2", "s3", "s4", "s5"]
    candidates = jev.build_action_candidates(llm.build_table_context("sichuan_mahjong", game, uid))
    assert {row["tile"] for row in candidates.values() if row["action"] == "discard"} == {"m1", "m2"}


def test_blackjack_candidates_use_hit_and_stand_without_dealer_hole_card():
    context = llm.build_blackjack_context({"state": "playing", "players": [{"user_id": "PRIVATE", "hand": ["Spades7", "Hearts8"]}],
                                         "dealer": {"hand": ["SpadesK", "ClubsQ"]}}, "PRIVATE")
    assert jev.build_action_candidates(context) == {"hit": {"action": "hit"}, "stand": {"action": "stand"}}
    assert "ClubsQ" not in llm.conversation_user_message(context)["content"]


def test_mahjong_reaction_candidates_include_distinct_chow_pung_kong_and_pass():
    game = traditional.MahjongGame(["a", "b", "c", "d"], seed=6)
    game.phase = "reaction"
    game._current_id = "b"
    game.last_discard = {"user_id": "a", "tile": "m3"}
    game.discards["a"] = ["m3"]
    game.reaction_queue = [("b", ["chow", "pung", "kong"])]
    game.hands["b"] = ["m1", "m2", "m3", "m3", "m3", "m4", "m5", "p1", "p3", "p5", "p7", "s1", "s3"]
    context = llm.build_table_context("mahjong", game, "b")
    candidates = jev.build_action_candidates(context)
    assert {row["action"] for row in candidates.values()} == {"chow", "pung", "kong", "pass"}
    assert sum(row["action"] == "chow" for row in candidates.values()) == 3
    for row in candidates.values():
        copy.deepcopy(game).act("b", **row)


def test_landlord_candidates_use_only_own_cards_and_public_last_play():
    game = traditional.LandlordGame(["PRIVATE-A", "PRIVATE-B", "PRIVATE-C"], seed=13)
    game.act(game.current_player_id, "bid", bid=3)
    uid = game.current_player_id
    initial = llm.build_table_context("landlord", game, uid)
    expected = jev.build_action_candidates(initial)
    changed = copy.deepcopy(game)
    for other in changed.hands:
        if other != uid:
            changed.hands[other] = ["不许读取暗牌"] * len(changed.hands[other])
    changed.bottom_cards = list(game.bottom_cards)
    assert jev.build_action_candidates(llm.build_table_context("landlord", changed, uid)) == expected
    assert len(expected) > 1


@pytest.mark.parametrize("remaining", [1, 3, 5, 97])
def test_texas_short_stack_raise_candidates_are_real_engine_legal(remaining):
    game = poker.TexasHoldemGame(["a", "b", "c"], seed=14)
    uid = game.current_player_id
    game._player(uid).stack = remaining
    context = llm.build_table_context("texas", game, uid)
    for row in jev.build_action_candidates(context).values():
        copy.deepcopy(game).act(uid, **row)


@pytest.mark.asyncio
async def test_jev_21point_runner_executes_selected_stand_and_hides_hole_card(monkeypatch):
    captured = []

    def handler(request):
        payload = json.loads(request.content)
        captured.append(payload)
        return httpx.Response(200, json=answer(payload["questions"]["action"]["criteria"], "stand"))

    service = blackjack.MultiplayerBlackjackService()
    runner = runner_module.GameBotRunner(client(monkeypatch, handler), service)
    service.bot_action_provider = runner.blackjack_action
    room_id = service.create_room(1, "测试玩家", "")["room_id"]
    service.configure_bot(room_id, 1, True)
    room = service._rooms[room_id]
    room.state = "playing"
    room.turn_order = [-1]
    room.current_turn_index = 0
    room.turn_deadline = 0
    room.dealer_hand = ["Club10", "Diamond7"]
    room.deck = ["Heart2", "Spade3"]
    room.players[-1].hand = ["Heart10", "Club8"]
    room.players[-1].status = "playing"
    room.players[-1].bet_amount = 10
    try:
        service._get_room_or_raise(room_id)
        await asyncio.gather(*(row.task for row in runner.pending.values()))
        service._get_room_or_raise(room_id)
        assert room.state == "finished"
        assert captured[0]["state"]["current"]["state"]["dealer"]["hand"] == ["Club10", "Hidden"]
        assert "Diamond7" not in json.dumps(captured)
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_invalid_jev_answer_reaches_existing_local_algorithm(monkeypatch):
    def handler(_):
        return httpx.Response(200, json={"answers": {"action": {"type": "choice", "choice": "cheat", "probabilities": {"cheat": 1}}}})

    runner = runner_module.GameBotRunner(client(monkeypatch, handler), blackjack.MultiplayerBlackjackService())
    room = SimpleNamespace(room_id="FAIL", round_number=1, revision=1, turn_deadline=0, game_type="texas", state="playing",
                           engine=poker.TexasHoldemGame(["bot", "human"], seed=14))
    uid = room.engine.current_player_id
    expected = copy.deepcopy(room.engine).suggest_action(uid)
    try:
        assert runner.table_action(room, uid) is None
        await asyncio.gather(*(row.task for row in runner.pending.values()))
        assert runner.table_action(room, uid) == expected
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_real_runner_keeps_jev_history_separate_between_rooms(monkeypatch):
    captured = []

    def handler(request):
        payload = json.loads(request.content)
        captured.append(payload)
        return httpx.Response(200, json=answer(payload["questions"]["action"]["criteria"], "call"))

    runner = runner_module.GameBotRunner(client(monkeypatch, handler), blackjack.MultiplayerBlackjackService())
    first = SimpleNamespace(room_id="FIRST", round_number=1, revision=1, turn_deadline=0, game_type="golden_flower",
                            state="playing", engine=poker.GoldenFlowerGame(["PRIVATE-BOT", "PRIVATE-HUMAN"], seed=4))
    second = SimpleNamespace(room_id="SECOND", round_number=1, revision=1, turn_deadline=0, game_type="golden_flower",
                             state="playing", engine=poker.GoldenFlowerGame(["PRIVATE-BOT", "PRIVATE-HUMAN"], seed=8))
    try:
        uid = first.engine.current_player_id
        assert runner.table_action(first, uid) is None
        await asyncio.gather(*(row.task for row in runner.pending.values()))
        action = runner.table_action(first, uid)
        assert action == {"action": "call"}
        first.engine.act(uid, **action)
        runner.observe_table_action(first, uid, "call", {})
        first.engine.act(first.engine.current_player_id, "call")
        first.revision += 2
        assert runner.table_action(first, uid) is None
        assert runner.table_action(second, uid) is None
        await asyncio.gather(*(row.task for row in runner.pending.values()))
        histories = [row["state"]["conversation"] for row in captured]
        assert [len(history) for history in histories] == [0, 2, 0]
        assert captured[1]["state"]["current"]["public_action_history"]
        assert captured[2]["state"]["current"]["public_action_history"] == []
        assert "PRIVATE-" not in json.dumps(captured)
    finally:
        await runner.close()
