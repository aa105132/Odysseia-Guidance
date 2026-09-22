"""真实房间与异步模型调度整合：并发、过期结果、整局回退及记忆隔离。"""

import asyncio
import copy
import importlib
import json

import pytest


tables = importlib.import_module("src.chat.features.games.blackjack-web.table_service")
runner_module = importlib.import_module("src.chat.features.games.blackjack-web.game_bot_runner")
blackjack = importlib.import_module("src.chat.features.games.blackjack-web.multiplayer_service")


class SimulatedClient:
    enabled = True
    timeout_seconds = 2

    def __init__(self, answer=None, blocked=False):
        self.answer = answer
        self.calls = []
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()

    async def choose_action(self, context):
        self.calls.append(copy.deepcopy(context))
        await self.release.wait()
        if isinstance(self.answer, Exception):
            raise self.answer
        return copy.deepcopy(self.answer)


def create_table(service, game_type="landlord", host="human:one"):
    room_id = service.create({"user_id": host, "username": "不应传给模型的名字", "avatar_url": "secret-avatar"},
                             game_type, "solo", True)["room_id"]
    service.ready(room_id, host, True)
    service.start(room_id, host)
    room = service._room(room_id)
    room.engine = type(room.engine)(list(room.players), seed=37, buy_in=room.buy_in, base_stake=room.base_stake)
    service._set_turn(room)
    return room


def install(client):
    now = [1000.0]
    service = tables.TableService(clock=lambda: now[0])
    runner = runner_module.GameBotRunner(client, blackjack.MultiplayerBlackjackService())
    service.bot_action_provider = runner.table_action
    service.action_observer = runner.observe_table_action
    return service, runner, now


async def finish_pending(runner):
    await asyncio.gather(*(pending.task for pending in runner.pending.values()))


@pytest.mark.asyncio
@pytest.mark.parametrize("game_type", ["texas", "golden_flower", "landlord", "mahjong", "sichuan_mahjong", "guandan"])
async def test_real_table_finishes_when_model_always_returns_illegal_action(game_type):
    client = SimulatedClient({"action": "play", "cards": ["不在手中的牌"]})
    service, runner, now = install(client)
    room = create_table(service, game_type)
    try:
        for _ in range(1200):
            if room.state == "finished":
                break
            now[0] = room.turn_deadline + 1
            service._advance_due_turn(room)
            if runner.pending:
                await finish_pending(runner)
        assert room.state == "finished"
        assert client.calls
        assert service.settlement(room.room_id) is not None
        for context in client.calls:
            public_text = json.dumps({key: value for key, value in context.items() if not key.startswith("_")})
            assert "human:one" not in public_text
            assert "bot:" not in public_text
            assert "secret-avatar" not in public_text
            assert "wall" not in context["state"]
            assert "deck" not in context["state"]
            for player in context["state"]["players"]:
                if player["user_id"] != context["you"]:
                    assert not player["hand"] or set(player["hand"]) == {"Hidden"}
        runner.prune(service.rooms, {})
        assert not runner.conversations
        assert not runner.public_actions
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_pending_model_releases_room_lock_and_deduplicates_real_polling():
    client = SimulatedClient({"action": "bid", "bid": 3}, blocked=True)
    service, runner, now = install(client)
    first = create_table(service, host="human:first")
    second = create_table(service, host="human:second")
    for room in (first, second):
        service.action(room.room_id, room.host_user_id, "bid", bid=0)
    now[0] += 2
    lock = asyncio.Lock()
    try:
        for room in (first, second):
            async with lock:
                for _ in range(5):
                    service.get(room.room_id, room.host_user_id)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(client.calls) == 2
        assert not lock.locked()
        assert len(runner.pending) == 2
        assert all(not pending.task.done() for pending in runner.pending.values())
        client.release.set()
        await finish_pending(runner)
        for room in (first, second):
            service.get(room.room_id, room.host_user_id)
            assert room.engine.phase == "playing"
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_real_table_discards_previous_round_model_answer():
    client = SimulatedClient({"action": "bid", "bid": 3})
    service, runner, now = install(client)
    room = create_table(service)
    service.action(room.room_id, room.host_user_id, "bid", bid=0)
    now[0] += 2
    try:
        service._advance_due_turn(room)
        await finish_pending(runner)
        room.round_number += 1
        previous_revision = room.revision
        service._advance_due_turn(room)
        assert room.engine.phase == "bidding"
        assert room.revision == previous_revision
        assert next(iter(runner.pending.values())).session_key[3] == room.round_number
        await finish_pending(runner)
        assert client.calls[-1]["_conversation"] == []
        assert client.calls[-1]["public_action_history"] == []
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_recorded_actions_and_bot_conversations_stay_isolated():
    client = SimulatedClient(RuntimeError("模拟网络异常"))
    service, runner, now = install(client)
    room = create_table(service)
    try:
        for _ in range(300):
            if room.state == "finished":
                break
            now[0] = room.turn_deadline + 1
            service._advance_due_turn(room)
            if runner.pending:
                await finish_pending(runner)
        assert room.state == "finished"
        assert any(context["_conversation"] for context in client.calls)
        for context in client.calls:
            assert context["public_action_history"]
            for message in context["_conversation"]:
                if message["role"] == "user":
                    prior_context = json.loads(message["content"])
                    assert prior_context["you"] == context["you"]
                    assert "_seat_ids" not in prior_context
                    assert "_conversation" not in prior_context
        assert len({context["you"] for context in client.calls}) == 2
        last_events = runner.public_actions[runner._round_key(room)]
        assert last_events[0]["action"] == "bid"
        assert any(event["action"] == "play" and event.get("cards") for event in last_events)
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_landlord_all_pass_redeal_discards_previous_deal_private_memory():
    client = SimulatedClient({"action": "bid", "bid": 0})
    service, runner, now = install(client)
    room = create_table(service)
    try:
        service.action(room.room_id, room.host_user_id, "bid", bid=0)
        for _ in range(2):
            now[0] = room.turn_deadline + 1
            service._advance_due_turn(room)
            await finish_pending(runner)
            service._advance_due_turn(room)
        assert room.engine.deal_count == 2
        now[0] = room.turn_deadline + 1
        service._advance_due_turn(room)
        await finish_pending(runner)
        # 全不叫导致重新洗牌；旧手牌的模型对话不能进入新一副牌。
        assert client.calls[-1]["_conversation"] == []
        assert client.calls[-1]["public_action_history"] == []
    finally:
        await runner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [{"action": "stand"}, {"action": "hit"}, None, {"action": "raise", "amount": 20}])
async def test_blackjack_model_waits_and_finishes_without_exposing_dealer_hole_card(answer):
    client = SimulatedClient(answer, blocked=True)
    service = blackjack.MultiplayerBlackjackService()
    room = blackjack.MultiplayerRoom("BJTEST", 101)
    room.players = {
        101: blackjack.MultiplayerPlayerState(101, "真人", "", 0, bet_amount=10,
                                              hand=["Club10", "Heart8"], status="stood"),
        -1: blackjack.MultiplayerPlayerState(-1, "月月", "", 1, bet_amount=10,
                                             hand=["Club5", "Heart4"], status="playing", is_bot=True),
    }
    room.state = "playing"
    room.turn_order = [101, -1]
    room.current_turn_index = 1
    room.turn_deadline = 0
    room.dealer_hand = ["Club6", "Spade10"]
    room.deck = ["Diamond5", "Heart6", "Club7", "Diamond9"]
    service._rooms[room.room_id] = room
    runner = runner_module.GameBotRunner(client, service)
    service.bot_action_provider = runner.blackjack_action
    try:
        for _ in range(4):
            service._get_room_or_raise(room.room_id)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(client.calls) == 1
        assert room.players[-1].hand == ["Club5", "Heart4"]
        assert room.players[-1].status == "playing"
        assert client.calls[0]["state"]["dealer"]["hand"] == ["Club6", "Hidden"]
        assert "Spade10" not in str(client.calls[0])
        assert "deck" not in client.calls[0]["state"]
        client.release.set()
        for _ in range(12):
            if runner.pending:
                await finish_pending(runner)
            service._get_room_or_raise(room.room_id)
            if room.state == "finished":
                break
        assert room.state == "finished"
        assert room.payouts_committed is False
        assert not room.committed_payout_user_ids
    finally:
        await runner.close()
