"""场次底分、单局上限与按人数增删陪玩的服务回归测试。"""

import copy
import importlib

import pytest


tables = importlib.import_module("src.chat.features.games.blackjack-web.table_service")
poker = importlib.import_module("src.chat.features.games.blackjack-web.poker_games")
traditional = importlib.import_module("src.chat.features.games.blackjack-web.traditional_games")
HOST = {"user_id": "1", "username": "房主", "avatar_url": ""}
GUEST = {"user_id": "2", "username": "访客", "avatar_url": ""}


def started_landlord_room():
    now = [1000.0]
    service = tables.TableService(clock=lambda: now[0])
    rid = service.create(HOST, "landlord", "solo", True)["room_id"]
    service.ready(rid, "1", True)
    service.start(rid, "1")
    return service, service._room(rid), now


def test_llm_pending_preserves_bot_turn_revision_and_deadline():
    service, room, now = started_landlord_room()
    service.action(room.room_id, "1", "bid", bid=0)
    requests = []
    service.bot_action_provider = lambda received, uid: requests.append((received.room_id, uid))
    before_state = copy.deepcopy(room.engine.public_state("1"))
    before_room = (room.revision, room.turn_deadline, room.updated_at, room.last_turn, room.state)
    now[0] = room.turn_deadline + 100
    for _ in range(3):
        service.get(room.room_id, "1")
    assert room.engine.public_state("1") == before_state
    assert (room.revision, room.turn_deadline, room.updated_at, room.last_turn, room.state) == before_room
    assert len(requests) == 3
    assert all(uid == room.engine.current_player_id for _, uid in requests)


def test_llm_ready_action_is_applied_once_and_observed_before_revision_update():
    service, room, now = started_landlord_room()
    service.action(room.room_id, "1", "bid", bid=0)
    uid = room.engine.current_player_id
    original_revision = room.revision
    suggestion = {"action": "bid", "bid": 3}
    service.bot_action_provider = lambda received, current: suggestion
    observed = []
    service.action_observer = lambda received, current, action, payload: observed.append(
        (current, action, payload, received.engine.landlord_id, received.revision))
    now[0] = room.turn_deadline
    service.get(room.room_id, "1")
    assert room.engine.landlord_id == uid
    assert observed == [(uid, "bid", {"bid": 3}, uid, original_revision)]
    assert room.revision == original_revision + 1
    assert suggestion == {"action": "bid", "bid": 3}
    service.get(room.room_id, "1")
    assert len(observed) == 1


def test_human_timeout_uses_algorithm_without_requesting_llm():
    service, room, now = started_landlord_room()
    service.bot_action_provider = lambda *_: pytest.fail("真人超时不应请求月月模型")
    expected = room.engine.suggest_action("1")
    observed = []
    service.action_observer = lambda *event: observed.append(event[1:])
    now[0] = room.turn_deadline
    service.get(room.room_id, "1")
    assert observed == [("1", expected["action"], {key: value for key, value in expected.items() if key != "action"})]


def test_successful_human_action_is_observed_but_invalid_action_is_not():
    service, room, _ = started_landlord_room()
    observed = []
    service.action_observer = lambda *event: observed.append(event[1:])
    with pytest.raises(ValueError):
        service.action(room.room_id, "1", "bid", bid=4)
    assert observed == []
    service.action(room.room_id, "1", "bid", bid=3)
    assert observed == [("1", "bid", {"bid": 3})]


def test_observer_failure_does_not_leave_successful_action_half_applied(caplog):
    service, room, _ = started_landlord_room()
    revision = room.revision

    def broken_observer(*_):
        raise RuntimeError("不可记录的模型响应或密钥")

    service.action_observer = broken_observer
    service.action(room.room_id, "1", "bid", bid=3)
    assert room.engine.phase == "playing"
    assert room.revision == revision + 1
    assert room.turn_deadline is not None
    assert "RuntimeError" in caplog.text
    assert "不可记录的模型响应或密钥" not in caplog.text


def test_unconfigured_bot_provider_keeps_algorithmic_play():
    service, room, now = started_landlord_room()
    assert service.bot_action_provider is None
    service.action(room.room_id, "1", "bid", bid=0)
    uid = room.engine.current_player_id
    expected = room.engine.suggest_action(uid)
    observed = []
    service.action_observer = lambda *event: observed.append(event[1:])
    now[0] = room.turn_deadline
    service.get(room.room_id, "1")
    assert observed == [(uid, expected["action"], {key: value for key, value in expected.items() if key != "action"})]


@pytest.mark.parametrize("tier,base,entry,limit", [
    ("beginner", 1, 100, 100),
    ("intermediate", 5, 1000, 500),
    ("advanced", 20, 5000, 2000),
    ("custom", 1, 100, 100),
])
def test_tiers_expose_real_entry_and_loss_terms(tier, base, entry, limit):
    service = tables.TableService(clock=lambda: 1000)
    room = service.create(HOST, "texas", "multi", False, room_tier=tier)
    assert (room["room_tier"], room["base_stake"], room["entry_min"], room["loss_limit"]) == (tier, base, entry, limit)
    assert room["buy_in"] == limit


@pytest.mark.parametrize("terms", [
    {"room_tier": "beginner", "base_stake": 2},
    {"room_tier": "advanced", "loss_limit": 100},
    {"room_tier": "unknown"},
    {"room_tier": "custom", "base_stake": 0},
    {"room_tier": "custom", "base_stake": True},
    {"room_tier": "custom", "base_stake": 1.5},
    {"room_tier": "custom", "base_stake": 20, "loss_limit": 199},
    {"room_tier": "custom", "loss_limit": 2**53},
])
def test_invalid_terms_do_not_create_room(terms):
    service = tables.TableService(clock=lambda: 1000)
    with pytest.raises(ValueError):
        service.create(HOST, "texas", "multi", False, **terms)
    assert service.rooms == {}


def test_only_custom_host_can_change_terms_and_all_humans_must_prepare_again():
    service = tables.TableService(clock=lambda: 1000)
    room = service.create(HOST, "texas", "multi", False, room_tier="custom")
    rid = room["room_id"]
    service.join(rid, GUEST)
    service.ready(rid, "1", True)
    service.ready(rid, "2", True)
    before = copy.deepcopy(service.get(rid, "1"))
    with pytest.raises(PermissionError):
        service.settings(rid, "2", 5, 500)
    assert service.get(rid, "1") == before
    changed = service.settings(rid, "1", 5, 500)
    assert (changed["base_stake"], changed["entry_min"], changed["loss_limit"]) == (5, 500, 500)
    assert all(not player["is_ready"] for player in changed["players"])
    service.ready(rid, "1", True)
    service.ready(rid, "2", True)
    service.start(rid, "1")
    before = copy.deepcopy(service.get(rid, "1"))
    with pytest.raises(ValueError):
        service.settings(rid, "1", 10, 1000)
    assert service.get(rid, "1") == before
    public_service = tables.TableService(clock=lambda: 1000)
    public_room = public_service.create(HOST, "texas", "multi", False)
    with pytest.raises(ValueError):
        public_service.settings(public_room["room_id"], "1", 5, 500)


def test_bot_addition_preserves_existing_players_and_stable_unique_ids():
    service = tables.TableService(clock=lambda: 1000)
    room = service.create(HOST, "texas", "multi", True)
    rid = room["room_id"]
    first = {player["user_id"]: player for player in room["players"]}
    added = service.bots(rid, "1", "add", count=2)
    assert len(added["players"]) == 4
    assert all(player == first[player["user_id"]] for player in added["players"] if player["user_id"] in first)
    bots = [player["user_id"] for player in added["players"] if player["is_bot"]]
    removed = service.bots(rid, "1", "remove", bot_id=bots[1])
    assert [player["user_id"] for player in removed["players"]] == ["1", bots[0], bots[2]]
    readded = service.bots(rid, "1", "add", count=1)
    assert len(readded["players"]) == 4
    new_id = readded["players"][-1]["user_id"]
    assert new_id not in bots
    assert len({player["username"] for player in readded["players"]}) == 4


def test_auto_start_setting_is_optional_and_available_for_fixed_tiers():
    service = tables.TableService(clock=lambda: 1000)
    room = service.create(HOST, "texas", "multi", False)
    rid = room["room_id"]
    assert room["auto_start_when_ready"] is False
    service.join(rid, GUEST)
    service.ready(rid, "1", True)
    service.ready(rid, "2", True)
    assert service.should_auto_start(rid) is False
    with pytest.raises(PermissionError):
        service.settings(rid, "2", auto_start_when_ready=True)
    enabled = service.settings(rid, "1", auto_start_when_ready=True)
    assert enabled["auto_start_when_ready"] is True
    assert all(player["is_ready"] for player in enabled["players"])
    assert service.should_auto_start(rid) is True
    service.ready(rid, "2", False)
    assert service.should_auto_start(rid) is False
    service.kick(rid, "1", "2")
    assert service.should_auto_start(rid) is False


def test_kick_checks_host_target_and_round_state():
    service = tables.TableService(clock=lambda: 1000)
    rid = service.create(HOST, "texas", "multi", True)["room_id"]
    service.join(rid, GUEST)
    before = copy.deepcopy(service.get(rid, "1"))
    with pytest.raises(PermissionError):
        service.kick(rid, "2", "1")
    for target in ("1", "bot:yueyue", "missing"):
        with pytest.raises(ValueError):
            service.kick(rid, "1", target)
    assert service.get(rid, "1") == before
    kicked = service.kick(rid, "1", "2")
    assert "2" not in [player["user_id"] for player in kicked["players"]]
    with pytest.raises(PermissionError):
        service.get(rid, "2")
    service.join(rid, GUEST)
    service.ready(rid, "1", True)
    service.ready(rid, "2", True)
    service.start(rid, "1")
    with pytest.raises(ValueError):
        service.kick(rid, "1", "2")


def test_solo_all_bots_can_be_removed_without_automatic_replacement():
    service = tables.TableService(clock=lambda: 1000)
    room = service.create(HOST, "landlord", "solo", True)
    rid = room["room_id"]
    for player in room["players"]:
        if player["is_bot"]:
            service.bots(rid, "1", "remove", bot_id=player["user_id"])
    assert len(service.get(rid, "1")["players"]) == 1
    service.ready(rid, "1", True)
    with pytest.raises(ValueError, match="需要"):
        service.start(rid, "1")
    room = service.bots(rid, "1", "add", count=1)
    assert len(room["players"]) == 2
    assert room["include_yueyue"]


@pytest.mark.parametrize("operation,fields", [
    ("add", {"count": 0}), ("add", {"count": True}),
    ("add", {"count": 8}), ("add", {"count": 1, "bot_id": "bot:yueyue"}),
    ("remove", {"bot_id": "1"}), ("remove", {"bot_id": "bot:missing"}),
    ("remove", {"bot_id": "bot:yueyue", "count": 1}), ("other", {}),
])
def test_invalid_bot_operations_do_not_change_any_state(operation, fields):
    service = tables.TableService(clock=lambda: 1000)
    room = service.create(HOST, "texas", "multi", True)
    rid = room["room_id"]
    before = copy.deepcopy(service._room(rid))
    with pytest.raises(ValueError):
        service.bots(rid, "1", operation, **fields)
    assert service._room(rid) == before


def test_guests_and_active_rounds_cannot_modify_bots():
    service = tables.TableService(clock=lambda: 1000)
    rid = service.create(HOST, "texas", "multi", True)["room_id"]
    service.join(rid, GUEST)
    with pytest.raises(PermissionError):
        service.bots(rid, "2", "add", count=1)
    service.ready(rid, "1", True)
    service.ready(rid, "2", True)
    service.start(rid, "1")
    before = copy.deepcopy(service.get(rid, "1"))
    with pytest.raises(ValueError):
        service.bots(rid, "1", "remove", bot_id="bot:yueyue")
    assert service.get(rid, "1") == before


@pytest.mark.parametrize("game_type,player_count", [("texas", 2), ("golden_flower", 2), ("landlord", 3), ("mahjong", 4)])
def test_selected_base_stake_reaches_each_real_engine(game_type, player_count):
    service = tables.TableService(clock=lambda: 1000)
    rid = service.create(HOST, game_type, "solo", True, room_tier="advanced")["room_id"]
    service.ready(rid, "1", True)
    room = service.start(rid, "1")
    engine = service._room(rid).engine
    assert len(room["players"]) == player_count
    assert engine.buy_in == 2000
    if game_type == "texas":
        assert (engine.SMALL_BLIND, engine.BIG_BLIND, engine.pot) == (20, 40, 60)
    elif game_type == "golden_flower":
        assert (engine.ANTE, engine.base_bet, engine.pot) == (20, 20, 40)
    else:
        assert engine.SCORE_UNIT == 20
        assert room["game"]["score_unit"] == 20


@pytest.mark.parametrize("engine,ids", [(poker.TexasHoldemGame, ["a", "b"]), (poker.GoldenFlowerGame, ["a", "b"]), (traditional.LandlordGame, ["a", "b", "c"]), (traditional.MahjongGame, ["a", "b", "c", "d"])])
@pytest.mark.parametrize("base", [0, -1, True, 1.5, 101, 2**53])
def test_engine_base_validation(engine, ids, base):
    with pytest.raises(ValueError):
        engine(ids, buy_in=1000, base_stake=base)


def test_large_custom_stakes_have_no_old_fixed_limit_and_do_not_change_other_rooms():
    service = tables.TableService(clock=lambda: 1000)
    room = service.create(HOST, "texas", "solo", True, room_tier="custom", base_stake=10000, loss_limit=200000)
    service.ready(room["room_id"], "1", True)
    high = service.start(room["room_id"], "1")
    assert high["game"]["small_blind"] == 10000
    assert high["game"]["big_blind"] == 20000
    assert high["game"]["pot"] == 30000
    standard = poker.TexasHoldemGame(["a", "b"])
    assert (standard.SMALL_BLIND, standard.BIG_BLIND) == (1, 2)


def test_traditional_base_stakes_scale_final_score_without_changing_multipliers():
    landlord = traditional.LandlordGame(["a", "b", "c"], seed=1, buy_in=500, base_stake=5)
    landlord.act("a", "bid", bid=3)
    landlord.hands["a"] = ["Club3", "Diamond3", "Heart3", "Spade3"]
    landlord.act("a", "play", cards=list(landlord.hands["a"]))
    assert landlord.multiplier == 4
    assert landlord.scores == {"a": 120, "b": -60, "c": -60}
    mahjong = traditional.MahjongGame(["a", "b", "c", "d"], seed=1, buy_in=500, base_stake=5)
    mahjong._finish_win("a")
    assert mahjong.scores == {"a": 15, "b": -5, "c": -5, "d": -5}
    assert traditional.LandlordGame(["a", "b", "c"]).SCORE_UNIT == 1
    assert traditional.MahjongGame(["a", "b", "c", "d"]).SCORE_UNIT == 1
