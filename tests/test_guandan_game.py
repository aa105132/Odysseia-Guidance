"""掼蛋牌型、通配、接风、升级贡还贡及完整对局回归。"""

from collections import Counter
from copy import deepcopy
import importlib
import time

import pytest


m = importlib.import_module("src.chat.features.games.blackjack-web.guandan_game")
tables = importlib.import_module("src.chat.features.games.blackjack-web.table_service")


def cards(*values):
    used = Counter()
    result = []
    for value in values:
        if value.startswith(("Club", "Diamond", "Heart", "Spade", "Joker")):
            base = value
        else:
            base = m.SUITS[(used[value] // 2) % 4] + value
        key = base
        result.append(f"{base}#{used[key] % 2}")
        used[key] += 1
        if value != base:
            used[value] += 1
    return result


@pytest.mark.parametrize("hand,kind", [
    (("3",), "single"), (("A", "A"), "pair"), (("4", "4", "4"), "triple"),
    (("4", "4", "4", "7", "7"), "full_house"),
    (("ClubA", "Diamond2", "Club3", "Club4", "Club5"), "straight"),
    (("Club10", "DiamondJ", "ClubQ", "ClubK", "ClubA"), "straight"),
    (("A", "A", "2", "2", "3", "3"), "pair_straight"),
    (("A", "A", "A", "2", "2", "2"), "triple_straight"),
    (("3", "3", "3", "3"), "bomb"),
    (("ClubA", "Club2", "Club3", "Club4", "Club5"), "straight_flush"),
    (("JokerSmall", "JokerSmall", "JokerBig", "JokerBig"), "rocket"),
])
def test_common_patterns(hand, kind):
    assert kind in {pattern.kind for pattern in m.classify_guandan_cards(cards(*hand), "7")}


def test_wild_replaces_rank_and_suit_but_never_joker():
    assert any(p.kind == "pair" and p.rank == 14 for p in m.classify_guandan_cards(cards("Heart7", "ClubA"), "7"))
    assert any(p.kind == "straight_flush" for p in m.classify_guandan_cards(cards("Heart7", "Club3", "Club4", "Club5", "Club6"), "7"))
    for hand in (cards("Heart7", "JokerBig"), cards("JokerSmall", "JokerBig")):
        with pytest.raises(ValueError):
            m.classify_guandan_cards(hand, "7")
    pair = m.classify_guandan_cards(cards("Heart7", "Heart7"), "7")[0]
    assert pair.kind == "pair" and pair.rank == 15
    patterns = m.classify_guandan_cards(cards("Heart7", "Club3", "Club4", "Club5", "Club6"), "7")
    assert {pattern.kind for pattern in patterns} == {"straight", "straight_flush"}


def test_nine_and_ten_card_bombs_use_wilds():
    natural = [f"{suit}3#{pack}" for suit in m.SUITS for pack in (0, 1)]
    for number in (1, 2):
        hand = natural + [f"Heart7#{pack}" for pack in range(number)]
        pattern = m.classify_guandan_cards(hand, "7")[0]
        assert pattern.kind == "bomb" and pattern.size == 8 + number


def test_bomb_order_and_level_strength():
    bomb4 = m.GuandanPattern("bomb", 15, 4)
    bomb5 = m.GuandanPattern("bomb", 2, 5)
    flush = m.GuandanPattern("straight_flush", 5, 5)
    bomb6 = m.GuandanPattern("bomb", 2, 6)
    rocket = m.GuandanPattern("rocket", 17, 4)
    assert all(m.guandan_beats(b, a) for a, b in zip([bomb4, bomb5, flush, bomb6], [bomb5, flush, bomb6, rocket]))
    assert m.classify_guandan_cards(cards("Club7"), "7")[0].rank > m.classify_guandan_cards(cards("ClubA"), "7")[0].rank
    assert not m.guandan_beats(m.GuandanPattern("pair", 17, 2), m.GuandanPattern("single", 2, 1))


@pytest.mark.parametrize("hand", [[], ["Club2#9"], ["Club2#0", "Club2#0"], cards("K", "A", "2", "3", "4"), cards("3", "4", "5", "6")])
def test_invalid_cards_are_rejected(hand):
    with pytest.raises(ValueError):
        m.classify_guandan_cards(hand, "9")


def test_deal_privacy_and_candidate_legality():
    game = m.GuandanGame(["a", "b", "c", "d"], seed=3)
    assert len(set(sum(game.hands.values(), []))) == 108
    assert all(len(hand) == 27 for hand in game.hands.values())
    current = game.current_player_id
    state = game.public_state(current)
    assert sum(bool(p["hand"]) for p in state["players"]) == 1
    assert state["level"] == "2" and state["team_levels"] == ["2", "2"]
    for option in state["play_options"]:
        clone = deepcopy(game)
        clone.act(current, "play", cards=option["cards"], combo=option["combo"])


def test_finished_player_last_play_can_be_beaten_before_partner_takes_wind():
    game = m.GuandanGame(["a", "b", "c", "d"], seed=1)
    game.hands = {"a": cards("3"), "b": cards("4", "8"), "c": cards("5", "9"), "d": cards("6", "10")}
    game.turn_index = 0
    game.act("a", "play", cards=cards("3"))
    assert game.current_player_id == "b"
    game.act("b", "play", cards=cards("4"))
    assert game.last_play["user_id"] == "b"
    game.act("c", "pass")
    game.act("d", "pass")
    assert game.current_player_id == "b" and game.last_play is None


def test_partner_receives_wind_only_after_every_remaining_player_passes():
    game = m.GuandanGame(["a", "b", "c", "d"], seed=1)
    game.hands = {"a": cards("3"), "b": cards("4", "8"), "c": cards("5", "9"), "d": cards("6", "10")}
    game.turn_index = 0
    game.act("a", "play", cards=cards("3"))
    for uid in ("b", "c", "d"):
        game.act(uid, "pass")
    assert game.current_player_id == "c" and game.last_play is None


@pytest.mark.parametrize("order,gain", [(["a", "c", "b", "d"], 3), (["a", "b", "c", "d"], 2), (["a", "b", "d", "c"], 1)])
def test_upgrade_and_zero_sum_settlement(order, gain):
    game = m.GuandanGame(["a", "b", "c", "d"], seed=1, buy_in=100, base_stake=10)
    game.finish_order = order
    game._finish()
    assert game.level_gain == gain and game.team_levels[0] == m.RANKS[gain]
    assert sum(game.settlement().values()) == 0
    assert all(abs(value) <= game.buy_in for value in game.scores.values())


def test_ace_requires_current_declarer_and_teammate_not_last():
    for order, passed in ((["a", "c", "b", "d"], True), (["a", "b", "c", "d"], True), (["a", "b", "d", "c"], False)):
        game = m.GuandanGame(["a", "b", "c", "d"], seed=1)
        game.level = "A"
        game.team_levels = ["A", "7"]
        game.finish_order = order
        game._finish()
        assert game.match_finished is passed
    game = m.GuandanGame(["a", "b", "c", "d"], seed=1)
    game.level, game.team_levels, game.declarer_team = "7", ["A", "7"], 1
    game.finish_order = ["a", "c", "b", "d"]
    game._finish()
    assert not game.match_finished


def tribute_game(hands):
    game = m.GuandanGame(["a", "b", "c", "d"], seed=1)
    game.hands = hands
    game.tribute_events = []
    return game


def test_double_tribute_both_opponents_one_big_joker_resists():
    game = tribute_game({"a": cards("3"), "c": cards("4"), "b": cards("JokerBig", "5"), "d": ["JokerBig#1", "Club6#0"]})
    before = deepcopy(game.hands)
    game._tribute(["a", "c", "b", "d"])
    assert game.hands == before
    assert game.tribute_events[0]["kind"] == "resist" and game.current_player_id == "a"


def test_single_tribute_uses_largest_nonwild_and_returns_smallest_legal_card():
    game = tribute_game({"a": cards("3", "4", "J"), "b": cards("5"), "c": cards("6"), "d": cards("Heart2", "A", "7")})
    game._tribute(["a", "b", "c", "d"])
    assert game.tribute_events[0]["card"] == "ClubA#0"
    assert game.tribute_events[1]["card"] == "Club3#0"
    assert game.current_player_id == "d"
    assert "Heart2#0" in game.hands["d"]


def test_illegal_action_does_not_mutate_game():
    game = m.GuandanGame(["a", "b", "c", "d"], seed=1)
    uid = game.current_player_id
    before = deepcopy(game.public_state(uid))
    with pytest.raises(ValueError):
        game.act(uid, "play", cards=["Club2#4"])
    assert game.public_state(uid) == before


def test_multiple_wild_interpretations_choose_lowest_that_beats_or_explicit_combo():
    hand = cards("Heart4", "Heart4", "8", "8", "9", "9")
    patterns = m.classify_guandan_cards(hand, "4")
    assert {pattern.kind for pattern in patterns} == {"pair_straight", "triple_straight"}
    game = m.GuandanGame(["a", "b", "c", "d"], seed=1)
    game.level = "4"
    game.hands["a"] = hand + cards("ClubK")
    game.turn_index = 0
    game.last_pattern = m.GuandanPattern("pair_straight", 9, 6)
    game.last_play = {"user_id": "d", "cards": [], **game.last_pattern.to_dict()}
    game.act("a", "play", cards=hand)
    assert game.last_pattern.kind == "pair_straight" and game.last_pattern.rank == 10


def test_double_tribute_assigns_higher_card_to_first_and_chooses_donor_to_lead():
    game = tribute_game({"a": cards("3", "4"), "c": cards("5", "6"),
                         "b": cards("JokerSmall", "7"), "d": cards("A", "8")})
    game._tribute(["a", "c", "b", "d"])
    events = [event for event in game.tribute_events if event["kind"] == "tribute"]
    assert events[0]["user_id"] == "b" and events[0]["target_id"] == "a"
    assert events[1]["user_id"] == "d" and events[1]["target_id"] == "c"
    assert game.current_player_id == "b"


def test_single_tribute_to_partner_still_occurs_for_first_fourth_finish():
    game = tribute_game({"a": cards("3", "4"), "b": cards("5"), "d": cards("6"), "c": cards("A", "7")})
    game._tribute(["a", "b", "d", "c"])
    assert game.tribute_events[0]["user_id"] == "c" and game.tribute_events[0]["target_id"] == "a"


def test_bot_uses_only_own_hand_and_opponent_counts():
    class HiddenHand:
        def __len__(self):
            return 27

        def __iter__(self):
            raise AssertionError("不能读取对手或队友暗牌")

    game = m.GuandanGame(["a", "b", "c", "d"], seed=7)
    uid = game.current_player_id
    expected = game.suggest_action(uid)
    for other in game.player_ids:
        if other != uid:
            game.hands[other] = HiddenHand()
    assert game.suggest_action(uid) == expected


def test_finished_match_or_changed_seats_resets_levels():
    prior = {"player_ids": ["a", "b", "c", "d"], "team_levels": ["A", "9"],
             "declarer_team": 0, "finish_order": ["a", "c", "b", "d"], "match_finished": True}
    assert m.GuandanGame(prior["player_ids"], seed=1, match_state=prior).team_levels == ["2", "2"]
    prior["match_finished"] = False
    assert m.GuandanGame(["a", "b", "c", "new"], seed=1, match_state=prior).team_levels == ["2", "2"]


def test_same_room_preserves_seats_level_and_tribute_across_rounds():
    service = tables.TableService(clock=lambda: 1000)
    host = {"user_id": "h", "username": "房主", "avatar_url": ""}
    rid = service.create(host, "guandan", "solo", True)["room_id"]
    service.ready(rid, "h", True)
    service.start(rid, "h")
    room = service._room(rid)
    ids = list(room.engine.player_ids)
    room.engine.finish_order = [ids[0], ids[2], ids[1], ids[3]]
    room.engine._finish()
    service._set_turn(room)
    room.settlement_status = "settled"
    service.ready(rid, "h", True)
    service.start(rid, "h")
    assert room.engine.player_ids == ids
    assert room.engine.team_levels == ["5", "2"]
    assert room.engine.level == "5"
    assert room.engine.tribute_events


def test_snapshot_last_public_action_has_stable_id_and_no_unplayed_hand():
    service = tables.TableService(clock=lambda: 1000)
    host = {"user_id": "h", "username": "房主", "avatar_url": ""}
    rid = service.create(host, "guandan", "solo", True)["room_id"]
    service.ready(rid, "h", True)
    assert service.start(rid, "h")["last_public_action"] is None
    room = service._room(rid)
    uid = room.engine.current_player_id
    payload = room.engine.suggest_action(uid)
    action = payload.pop("action")
    room.engine.act(uid, action, **payload)
    service._observe_action(room, uid, action, payload)
    event = service._snapshot(room, "h")["last_public_action"]
    assert event["id"] == "1:0:1"
    assert set(event) == {"id", "user_id", "action", "time", "cards", "combo", "kind", "name"}
    assert event["cards"] == payload["cards"]
    event["cards"].clear()
    assert room.public_action_history[-1]["cards"]


@pytest.mark.parametrize("batch", range(5))
def test_one_hundred_complete_bot_games_per_batch(batch):
    started = time.monotonic()
    for seed in range(batch * 100, (batch + 1) * 100):
        game = m.GuandanGame(["a", "b", "c", "d"], seed=seed)
        for _ in range(700):
            if game.finished:
                break
            uid = game.current_player_id
            game.act(uid, **game.suggest_action(uid))
        assert game.finished, seed
        assert len(set(game.finish_order)) == 4
        assert sum(game.scores.values()) == 0
    assert time.monotonic() - started < 30
