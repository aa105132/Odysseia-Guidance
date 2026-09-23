"""跨街及结算动作记忆保持原街下注金额，覆盖真人、模型和算法回退。"""

import asyncio
import copy
import importlib
import json

import pytest


tables = importlib.import_module("src.chat.features.games.blackjack-web.table_service")
poker = importlib.import_module("src.chat.features.games.blackjack-web.poker_games")
bots = importlib.import_module("src.chat.features.games.blackjack-web.game_bot_runner")
blackjack = importlib.import_module("src.chat.features.games.blackjack-web.multiplayer_service")


class Client:
    enabled = True
    timeout_seconds = 2

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def choose_action(self, context):
        self.calls.append(copy.deepcopy(context))
        return self.answer


def room_before_last_call(phase):
    now = [1000.0]
    service = tables.TableService(clock=lambda: now[0])
    rid = service.create({"user_id": "human:one", "username": "真人", "avatar_url": ""},
                         "texas", "multi", True)["room_id"]
    service.ready(rid, "human:one", True)
    service.start(rid, "human:one")
    room = service._room(rid)
    bot_id = next(uid for uid, player in room.players.items() if player.is_bot)
    # 真人先加注，月月最后跟齐；这样执行前后必定换街或结束。
    room.engine = poker.TexasHoldemGame([bot_id, "human:one"], seed=17)
    engine = room.engine
    engine.phase = phase
    board = ["Club2", "Diamond3", "Heart4", "Spade8", "Club9"]
    engine.community_cards = board[:{"preflop": 0, "flop": 3, "turn": 4, "river": 5}[phase]]
    engine._current_index = 0
    engine._pending = {bot_id}
    engine.current_bet = 4
    engine.min_raise = 2
    engine.players[0].hand = ["HeartA", "DiamondA"]
    engine.players[1].hand = ["HeartK", "DiamondK"]
    for index, player in enumerate(engine.players):
        player.round_bet = 2 if index == 0 else 4
        player.total_bet = 12 if index == 0 else 14
        player.stack = 100 - player.total_bet
    engine.deck = [card for card in poker.create_deck()
                   if card not in engine.community_cards + engine.players[0].hand + engine.players[1].hand]
    service._set_turn(room)
    return service, room, now, bot_id


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["human", "llm", "fallback", "timeout"])
@pytest.mark.parametrize("phase,next_phase", [("preflop", "flop"), ("flop", "turn"), ("turn", "river"), ("river", "finished")])
async def test_last_call_remembers_original_street_and_pre_payout_money(path, phase, next_phase):
    service, room, now, actor = room_before_last_call(phase)
    client = Client({"action": "call"} if path == "llm" else None)
    runner = bots.GameBotRunner(client, blackjack.MultiplayerBlackjackService())
    service.action_observer = runner.observe_table_action
    service.bot_action_provider = runner.table_action
    room.engine.suggest_action = lambda uid: {"action": "call"}
    try:
        if path in {"human", "timeout"}:
            room.players[actor].is_bot = False
            if path == "human":
                service.action(room.room_id, actor, "call")
            else:
                now[0] = room.turn_deadline
                service._advance_due_turn(room)
        else:
            now[0] = room.turn_deadline
            service._advance_due_turn(room)
            assert len(runner.pending) == 1
            assert room.public_action_history == []
            await asyncio.gather(*(pending.task for pending in runner.pending.values()))
            service._advance_due_turn(room)
        assert room.engine.phase == next_phase
        event = room.public_action_history[-1]
        expected = {"phase": phase, "pot": 28, "stack": 86, "round_bet": 4, "total_bet": 14}
        assert {key: event[key] for key in expected} == expected
        model_event = runner.public_actions[runner._round_key(room)][-1]
        assert {key: model_event[key] for key in expected} == expected
        assert model_event["seat"] == "seat_1"
        assert model_event["action"] == "call"
        assert not {"hand", "deck", "community_cards", "payout"} & model_event.keys()
        assert "human:" not in json.dumps(model_event)
        assert "bot:" not in json.dumps(model_event)
        assert "HeartA" not in json.dumps(model_event)
        if phase == "river":
            assert room.engine.players[0].stack == 114
            assert event["stack"] == 86
        else:
            assert room.engine.players[0].round_bet == 0
            assert event["round_bet"] == 4
            # 下一次模型请求拿到旧街的准确记录，而不是新街的假动作。
            room.revision += 1
            runner.table_action(room, room.engine.current_player_id)
            await asyncio.gather(*(pending.task for pending in runner.pending.values()))
            assert client.calls[-1]["public_action_history"][-1] == model_event
    finally:
        await runner.close()


def test_illegal_action_does_not_create_memory_or_change_pot():
    service, room, _, actor = room_before_last_call("preflop")
    room.players[actor].is_bot = False
    before = room.engine.public_state(actor)
    with pytest.raises(ValueError):
        service.action(room.room_id, actor, "raise", amount=3)
    assert room.public_action_history == []
    assert room.engine.public_state(actor) == before


def test_action_snapshot_and_observer_ignore_private_payload():
    service, room, _, actor = room_before_last_call("preflop")
    room.players[actor].is_bot = False
    captured = []
    service.action_observer = lambda *args: captured.append(args[-1])
    service.action(room.room_id, actor, "call", hand=["SecretHand"], phase="SecretPhase",
                   poker_action={"phase": "SecretOverride"})
    assert captured == [{"phase": "preflop", "pot": 28, "stack": 86, "total_bet": 14, "round_bet": 4}]
    assert "Secret" not in json.dumps(room.public_action_history)
