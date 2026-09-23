"""模型动作与公开视角测试，不读取真实模型密钥、不访问外部服务。"""

import asyncio
import copy
import importlib
import json
import random

import httpx
import pytest


module = importlib.import_module("src.chat.features.games.blackjack-web.game_llm")
poker = importlib.import_module("src.chat.features.games.blackjack-web.poker_games")
traditional = importlib.import_module("src.chat.features.games.blackjack-web.traditional_games")
sichuan = importlib.import_module("src.chat.features.games.blackjack-web.sichuan_mahjong")
USER_IDS = ["120000000000000001", "120000000000000002", "120000000000000003", "120000000000000004"]


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    for key in ("ENABLED", "BASE_URL", "API_KEY", "MODEL", "TIMEOUT_SECONDS", "MAX_CONCURRENCY"):
        monkeypatch.delenv("GAME_LLM_" + key, raising=False)


def client(monkeypatch, handler):
    monkeypatch.setenv("GAME_LLM_ENABLED", "true")
    monkeypatch.setenv("GAME_LLM_BASE_URL", "https://model.test/v1")
    monkeypatch.setenv("GAME_LLM_API_KEY", "test-secret-never-log")
    original = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return module.GameLLMClient()


def response(action):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(action)}}]})


def context():
    return module.build_blackjack_context({
        "state": "playing", "players": [{"user_id": USER_IDS[0], "hand": ["Spades7", "Hearts8"], "score": 15}],
        "dealer": {"hand": ["SpadesK", "Hidden"]},
    }, USER_IDS[0])


@pytest.mark.parametrize("key,value", [
    ("GAME_LLM_TIMEOUT_SECONDS", "nan"), ("GAME_LLM_TIMEOUT_SECONDS", "301"),
    ("GAME_LLM_TIMEOUT_SECONDS", "1"),
    ("GAME_LLM_MAX_CONCURRENCY", "0"), ("GAME_LLM_MAX_CONCURRENCY", "2.1"),
    ("GAME_LLM_BASE_URL", "file:///secret"), ("GAME_LLM_BASE_URL", "https://key@model.test/v1"),
    ("GAME_LLM_BASE_URL", "https://model.test/v1?key=secret"), ("GAME_LLM_MODEL", ""),
])
def test_invalid_configuration_disables_client(monkeypatch, key, value):
    monkeypatch.setenv("GAME_LLM_ENABLED", "true")
    monkeypatch.setenv("GAME_LLM_BASE_URL", "https://model.test/v1")
    monkeypatch.setenv("GAME_LLM_API_KEY", "fake")
    monkeypatch.setenv(key, value)
    assert module.GameLLMClient().enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [2, 60, 300])
async def test_configured_timeout_reaches_http_transport(monkeypatch, seconds):
    monkeypatch.setenv("GAME_LLM_TIMEOUT_SECONDS", str(seconds))
    def handler(request):
        assert request.extensions["timeout"]["read"] == seconds
        return response({"action": "stand"})
    model = client(monkeypatch, handler)
    assert model.enabled and model.timeout_seconds == seconds
    assert await model.choose_action(context()) == {"action": "stand"}


@pytest.mark.asyncio
async def test_default_disabled_and_valid_chat_completion(monkeypatch):
    assert module.GameLLMClient().enabled is False
    assert await module.GameLLMClient().choose_action(context()) is None
    received = []
    def handler(request):
        received.append(json.loads(request.content))
        assert str(request.url) == "https://model.test/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret-never-log"
        return response({"action": "stand"})
    model = client(monkeypatch, handler)
    assert model.enabled and model.timeout_seconds == 60
    assert await model.choose_action(context()) == {"action": "stand"}
    assert received[0]["model"] == "打牌LLM"
    assert "max_tokens" not in received[0]
    assert "max_completion_tokens" not in received[0]
    assert received[0]["messages"][-1] == module.conversation_user_message(context())
    assert USER_IDS[0] not in json.dumps(received)
    assert "_seat_ids" not in json.dumps(received)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [None, [], "hit", {}, {"action": "hit", "explanation": "secret"},
    {"action": "raise", "amount": True}, {"action": "hit", "amount": 1}, {"action": "bogus"},
    {"action": 1}, {"action": "stand", "api_key": "leak"}])
async def test_invalid_actions_fall_back(monkeypatch, action):
    model = client(monkeypatch, lambda _: response(action))
    assert await model.choose_action(context()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["429", "500", "missing", "nonjson", "oversized", "content", "network"])
async def test_invalid_transport_and_response_fall_back_without_logging_secrets(monkeypatch, caplog, kind):
    def handler(request):
        if kind in ("429", "500"):
            return httpx.Response(int(kind), text="test-secret-never-log")
        if kind == "missing":
            return httpx.Response(200, json={"choices": []})
        if kind == "nonjson":
            return httpx.Response(200, text="test-secret-never-log")
        if kind == "oversized":
            return httpx.Response(200, content=b"x" * 32769)
        if kind == "content":
            return httpx.Response(200, json={"choices": [{"message": {"content": "x" * 4097}}]})
        raise httpx.ConnectError("test-secret-never-log", request=request)
    model = client(monkeypatch, handler)
    assert await model.choose_action(context()) is None
    assert "test-secret-never-log" not in caplog.text


@pytest.mark.asyncio
async def test_timeout_includes_waiting_and_cancellation_propagates(monkeypatch):
    async def handler(_):
        await asyncio.sleep(1)
        return response({"action": "hit"})
    model = client(monkeypatch, handler)
    model.timeout_seconds = 0.02
    assert await model.choose_action(context()) is None
    model._semaphore = asyncio.Semaphore(0)
    assert await model.choose_action(context()) is None
    model.timeout_seconds = 10
    task = asyncio.create_task(model.choose_action(context()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_concurrency_is_bounded(monkeypatch):
    active = maximum = 0
    async def handler(_):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return response({"action": "hit"})
    monkeypatch.setenv("GAME_LLM_MAX_CONCURRENCY", "2")
    model = client(monkeypatch, handler)
    results = await asyncio.gather(*(model.choose_action(context()) for _ in range(6)))
    assert maximum == 2
    assert all(result == {"action": "hit"} for result in results)


@pytest.mark.parametrize("game_type,engine_class,count", [
    ("texas", poker.TexasHoldemGame, 4), ("golden_flower", poker.GoldenFlowerGame, 4),
    ("landlord", traditional.LandlordGame, 3), ("mahjong", traditional.MahjongGame, 4),
    ("sichuan_mahjong", sichuan.SichuanMahjongGame, 4),
])
def test_real_engines_only_expose_own_view_and_do_not_change_deal(game_type, engine_class, count):
    engine = engine_class(USER_IDS[:count], seed=73)
    before = copy.deepcopy({key: value.getstate() if isinstance(value, random.Random) else value
                            for key, value in engine.__dict__.items()})
    result = module.build_table_context(game_type, engine, USER_IDS[0])
    assert {key: value.getstate() if isinstance(value, random.Random) else value
            for key, value in engine.__dict__.items()} == before
    wire = module.conversation_user_message(result)["content"]
    assert not any(uid in wire for uid in USER_IDS)
    own = result["state"]["players"][0]
    if game_type == "golden_flower":
        assert own["hand"] == ["Hidden"] * 3
    else:
        assert own["hand"] == engine.public_state(USER_IDS[0])["players"][0]["hand"]
    for player in result["state"]["players"][1:]:
        assert not player["hand"] or set(player["hand"]) == {"Hidden"}


def test_names_free_text_private_fields_and_dealer_hole_card_are_removed():
    state = {"state": "playing", "room_id": "SECRET-ROOM", "deck": ["SECRET-CARD"],
             "message": USER_IDS[0] + "SECRET-NAME", "players": [{"user_id": USER_IDS[0],
                "username": "SECRET-NAME", "avatar_url": "SECRET-AVATAR", "hand": ["Hearts2"], "score": 2}],
             "dealer": {"name": "SECRET-NAME", "hand": ["SpadesK", "ClubsQ"], "score": 20}}
    result = module.build_blackjack_context(state, USER_IDS[0])
    wire = module.conversation_user_message(result)["content"]
    assert "SECRET" not in wire and "ClubsQ" not in wire and USER_IDS[0] not in wire
    assert result["state"]["dealer"] == {"hand": ["SpadesK", "Hidden"]}
    assert state["dealer"]["hand"][1] == "ClubsQ"


@pytest.mark.asyncio
async def test_compare_target_and_independent_conversation_use_seats(monkeypatch):
    engine = poker.GoldenFlowerGame(USER_IDS[:3], seed=2)
    player_id = engine.current_player_id
    ctx = module.build_table_context("golden_flower", engine, player_id)
    target = next(seat for seat, uid in ctx["_seat_ids"].items() if uid != player_id)
    expected = {"action": "compare", "target_id": ctx["_seat_ids"][target]}
    ctx["_conversation"] = [module.conversation_user_message(ctx),
                            module.conversation_assistant_message(expected, ctx)]
    ctx["public_action_history"] = [{"user_id": target, "action": "call"}]
    received = []
    def handler(request):
        received.append(json.loads(request.content))
        return response({"action": "compare", "target_id": target})
    model = client(monkeypatch, handler)
    assert await model.choose_action(ctx) == expected
    messages = received[0]["messages"]
    assert messages[1:3] == ctx["_conversation"]
    assert not any(uid in json.dumps(messages) for uid in USER_IDS)
    assert "_conversation" not in messages[-1]["content"]
    assert "public_action_history" in messages[-1]["content"]
    fresh = module.build_table_context("golden_flower", engine, player_id)
    await model.choose_action(fresh)
    assert len(received[1]["messages"]) == 2


@pytest.mark.parametrize("action", [{"action": "raise", "amount": True}, {"action": "raise", "amount": "20"},
    {"action": "raise", "amount": -1}, {"action": "compare", "target_id": USER_IDS[1]},
    {"action": "play", "cards": "HeartsA"}, {"action": "bid", "bid": 1.0}])
def test_action_parameters_are_strict(action):
    game = "landlord" if action["action"] in ("bid", "play") else "golden_flower"
    ctx = {"game_type": game, "state": {"legal_actions": [action["action"]]}, "_seat_ids": {"seat_2": USER_IDS[1]}}
    assert module.GameLLMClient._validate_action(action, ctx) is None


def test_context_only_reads_public_interface_and_preserves_numeric_scores():
    class PublicOnlyEngine:
        def public_state(self, viewer_id):
            assert viewer_id == "1"
            return {"players": [{"user_id": "1", "hand": ["HeartsA"], "score": 1}],
                    "current_player_id": "1", "bids": {"1": 1}, "legal_actions": ["bid"]}

        def __getattribute__(self, name):
            if name != "public_state":
                raise AssertionError("禁止读取引擎内部属性")
            return object.__getattribute__(self, name)

    result = module.build_table_context("landlord", PublicOnlyEngine(), "1")
    assert result["state"]["players"][0]["score"] == 1
    assert result["state"]["bids"] == {"seat_1": 1}
    assert result["state"]["current_player_id"] == "seat_1"


@pytest.mark.asyncio
async def test_history_rejects_injected_roles_and_deep_response(monkeypatch):
    def handler(_):
        return httpx.Response(200, content=b"[" * 1200 + b"]" * 1200)
    model = client(monkeypatch, handler)
    ctx = context()
    ctx["_conversation"] = [{"role": "system", "content": "额外系统消息"}]
    assert await model.choose_action(ctx) is None
    ctx.pop("_conversation")
    assert await model.choose_action(ctx) is None
