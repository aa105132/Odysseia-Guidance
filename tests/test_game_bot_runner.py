"""异步陪玩回归：请求去重、合法性、独立会话、公开记牌和过期答案。"""

import asyncio
import copy
import importlib
from types import SimpleNamespace

import pytest


module = importlib.import_module("src.chat.features.games.blackjack-web.game_bot_runner")
tables = importlib.import_module("src.chat.features.games.blackjack-web.table_service")
poker = importlib.import_module("src.chat.features.games.blackjack-web.poker_games")
traditional = importlib.import_module("src.chat.features.games.blackjack-web.traditional_games")
blackjack = importlib.import_module("src.chat.features.games.blackjack-web.multiplayer_service")


class Client:
    enabled = True
    timeout_seconds = 2

    def __init__(self, result=None):
        self.result = result
        self.calls = []
        self.release = asyncio.Event()

    async def choose_action(self, context):
        self.calls.append(copy.deepcopy(context))
        await self.release.wait()
        return copy.deepcopy(self.result)


def make_room(room_id="AAAAAA"):
    ids = ["bot:yueyue", "123456789012345678"]
    engine = poker.TexasHoldemGame(ids, seed=42, buy_in=100)
    return SimpleNamespace(room_id=room_id, round_number=1, revision=1,
                           turn_deadline=0, game_type="texas", state="playing", engine=engine)


async def complete(runner):
    await asyncio.gather(*(pending.task for pending in runner.pending.values()), return_exceptions=True)


@pytest.mark.asyncio
async def test_runner_uses_full_configured_model_timeout(monkeypatch):
    client = Client({"action": "call"})
    client.timeout_seconds = 60
    client.release.set()
    runner = module.GameBotRunner(client, blackjack.MultiplayerBlackjackService())
    captured = []
    original_wait_for = asyncio.wait_for
    async def wait_for(awaitable, timeout):
        captured.append(timeout)
        return await original_wait_for(awaitable, timeout)
    monkeypatch.setattr(module.asyncio, "wait_for", wait_for)
    assert await runner._choose({}) == {"action": "call"}
    assert captured == [60.5]
    await runner.close()


@pytest.mark.asyncio
async def test_polling_deduplicates_and_does_not_block_other_room():
    client = Client({"action": "call"})
    runner = module.GameBotRunner(client, blackjack.MultiplayerBlackjackService())
    first, second = make_room(), make_room("BBBBBB")
    for _ in range(10):
        assert runner.table_action(first, first.engine.current_player_id) is None
    assert runner.table_action(second, second.engine.current_player_id) is None
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(client.calls) == 2
    client.release.set()
    await complete(runner)
    assert runner.table_action(first, first.engine.current_player_id) == {"action": "call"}
    assert len(client.calls) == 2
    await runner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, {"action": "raise", "amount": -10}, {"action": "play", "cards": ["Fake"]}])
async def test_failed_or_illegal_action_falls_back_without_mutating_game(result):
    client = Client(result)
    runner = module.GameBotRunner(client, blackjack.MultiplayerBlackjackService())
    room = make_room()
    uid = room.engine.current_player_id
    before = room.engine.public_state(uid)
    assert runner.table_action(room, uid) is None
    client.release.set()
    await complete(runner)
    action = runner.table_action(room, uid)
    assert room.engine.public_state(uid) == before
    trial = copy.deepcopy(room.engine)
    trial.act(uid, action.pop("action"), **action)
    await runner.close()


@pytest.mark.asyncio
async def test_conversations_are_isolated_by_room_round_and_bot():
    client = Client({"action": "call"})
    client.release.set()
    runner = module.GameBotRunner(client, blackjack.MultiplayerBlackjackService())
    room = make_room()
    uid = room.engine.current_player_id
    runner.table_action(room, uid)
    await complete(runner)
    action = runner.table_action(room, uid)
    room.revision += 1
    runner.table_action(room, uid)
    await complete(runner)
    assert len(client.calls[-1]["_conversation"]) == 2
    assert str(action["action"]) in client.calls[-1]["_conversation"][-1]["content"]
    other = make_room("BBBBBB")
    runner.table_action(other, other.engine.current_player_id)
    await complete(runner)
    assert client.calls[-1]["_conversation"] == []
    # 同房间的另一个陪玩也不能收到月月的私有对话。
    other_id = next(p.user_id for p in room.engine.players if p.user_id != uid)
    room.revision += 1
    runner.table_action(room, other_id)
    await complete(runner)
    assert client.calls[-1]["_conversation"] == []
    room.round_number += 1
    room.revision += 1
    runner.table_action(room, uid)
    await complete(runner)
    assert client.calls[-1]["_conversation"] == []
    await runner.close()


@pytest.mark.asyncio
async def test_public_action_memory_keeps_early_cards_without_private_choices():
    client = Client()
    runner = module.GameBotRunner(client, blackjack.MultiplayerBlackjackService())
    room = make_room()
    uid = room.engine.current_player_id
    for index in range(30):
        runner.observe_table_action(room, uid, "play", {"cards": [f"public_{index}"]})
    runner.observe_table_action(room, uid, "look", {"cards": ["private_card"]})
    runner.observe_table_action(room, uid, "kong", {"tile": "private_kong"})
    runner.observe_table_action(room, uid, "dingque", {"suit": "private_suit"})
    runner.table_action(room, uid)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    events = client.calls[0]["public_action_history"]
    assert events[0]["cards"] == ["public_0"]
    assert len(events) == 33
    assert "private_" not in str(events)
    assert uid not in str(events)
    await runner.close()


@pytest.mark.asyncio
async def test_stale_result_is_cancelled_and_finished_room_discards_memory():
    client = Client({"action": "call"})
    runner = module.GameBotRunner(client, blackjack.MultiplayerBlackjackService())
    room = make_room()
    uid = room.engine.current_player_id
    runner.table_action(room, uid)
    old = next(iter(runner.pending.values())).task
    room.revision += 1
    assert runner.table_action(room, uid) is None
    await asyncio.sleep(0)
    assert old.cancelled()
    room.state = "finished"
    runner.prune({room.room_id: room}, {})
    assert not runner.pending
    assert not runner.conversations
    await runner.close()


@pytest.mark.asyncio
async def test_unexpected_client_error_falls_back_and_close_cancels_calls():
    client = Client()
    async def fail(context):
        raise RuntimeError("模拟网络适配层错误")
    client.choose_action = fail
    runner = module.GameBotRunner(client, blackjack.MultiplayerBlackjackService())
    room = make_room()
    uid = room.engine.current_player_id
    runner.table_action(room, uid)
    await complete(runner)
    assert runner.table_action(room, uid)["action"] in room.engine.public_state(uid)["legal_actions"]
    room.revision += 1
    runner.table_action(room, uid)
    await runner.close()
    assert runner.closed and not runner.pending and not runner.conversations


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [{"action": "stand"}, {"action": "raise", "amount": 100}, None])
async def test_blackjack_pending_uses_model_or_fallback_with_private_conversation(answer):
    client = Client(answer)
    service = blackjack.MultiplayerBlackjackService()
    runner = module.GameBotRunner(client, service)
    service.bot_action_provider = runner.blackjack_action
    state = service.create_room(1, "玩家", "")
    room = service._rooms[state["room_id"]]
    service.configure_bot(room.room_id, 1, True)
    bot = room.players[-1]
    room.state = "playing"
    room.turn_order = [-1]
    room.current_turn_index = 0
    room.turn_deadline = 0
    room.dealer_hand = ["Club10", "Diamond7"]
    room.deck = ["Heart2", "Spade3"]
    bot.hand = ["Heart10", "Club8"]
    bot.status = "playing"
    bot.bet_amount = 10
    service._get_room_or_raise(room.room_id)
    assert room.state == "playing" and bot.status == "playing"
    assert bot.hand == ["Heart10", "Club8"]
    client.release.set()
    await complete(runner)
    service._get_room_or_raise(room.room_id)
    assert room.state == "finished"
    assert client.calls[0]["state"]["dealer"]["hand"] == ["Club10", "Hidden"]
    assert "Diamond7" not in str(client.calls)
    assert len(next(iter(runner.conversations.values())).messages) == 2
    await runner.close()
