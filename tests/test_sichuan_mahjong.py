"""四川血战规则、隐私、胡牌续局、流局算账和完整对局回归。"""

from collections import Counter
from copy import deepcopy
import importlib

import pytest


rules = importlib.import_module("src.chat.features.games.blackjack-web.sichuan_mahjong")
SichuanMahjongGame = rules.SichuanMahjongGame
IDS = ["a", "b", "c", "d"]
WAITING = ["m1", "m2", "m3", "m4", "m5", "m6", "p2", "p3", "p4", "p7", "p8", "p9", "p5"]
SPARSE = ["m1", "m3", "m5", "m7", "m9", "p1", "p3", "p5", "p7", "p9", "p9", "m9", "m1"]


def fixed_game(player_ids=IDS):
    game = SichuanMahjongGame(player_ids, seed=42)
    for uid in player_ids:
        game.act(uid, "dingque", suit="s")
    game.hands = {uid: list(SPARSE) for uid in player_ids}
    game.hands[player_ids[0]].append("p5")
    game.wall = ["m8", "p8", "m6", "p6", "m4", "p4"]
    return game


def pass_reactions(game):
    while game.phase == "reaction":
        game.act(game.current_player_id, "pass")


def test_deal_is_108_tiles_and_dingque_is_private_until_complete():
    game = SichuanMahjongGame(IDS, seed=42)
    assert [len(game.hands[uid]) for uid in IDS] == [14, 13, 13, 13]
    assert len(game.wall) == 55
    assert Counter(sum(game.hands.values(), []) + game.wall) == Counter({tile: 4 for tile in rules.TILES})
    assert game.public_state("a")["legal_actions"] == ["dingque"]
    assert game.public_state("a")["missing_suit_options"] == ["m", "p", "s"]
    game.act("a", "dingque", suit="s")
    own = game.public_state("a")
    other = game.public_state("b")
    assert own["players"][0]["missing_suit"] == "s"
    assert other["players"][0]["missing_suit"] is None
    assert other["players"][0]["hand"] == []
    for uid in IDS[1:]:
        game.act(uid, "dingque", suit="m")
    assert game.current_player_id == "a"
    assert game.phase == "playing"
    assert [player["missing_suit"] for player in game.public_state("d")["players"]] == ["s", "m", "m", "m"]


@pytest.mark.parametrize("suit", [None, "z", "", 1, ["m"]])
def test_invalid_dingque_is_atomic(suit):
    game = SichuanMahjongGame(IDS, seed=3)
    original = deepcopy(game.__dict__)
    with pytest.raises(ValueError):
        game.act("a", "dingque", suit=suit)
    assert game.public_state("a")["phase"] == "dingque"
    assert game.missing_suits == original["missing_suits"]
    assert game.hands == original["hands"]
    with pytest.raises(ValueError):
        game.act("b", "dingque", suit="m")


def test_missing_suit_must_be_discarded_and_disables_win_meld_and_kong():
    game = fixed_game()
    game.hands["a"][-1] = "s1"
    before = game.public_state("a")
    with pytest.raises(ValueError, match="定缺"):
        game.act("a", "discard", tile="m1")
    assert game.public_state("a") == before
    assert game.public_state("a")["legal_actions"] == ["discard"]
    game.hands["b"] = ["s2"] + ["s1"] * 3 + SPARSE[:9]
    game.act("a", "discard", tile="s1")
    assert game.current_player_id == "b"
    assert game.phase == "playing"
    assert game.public_state("b")["legal_actions"] == ["discard"]


def test_no_chow_for_next_player_even_with_sequence():
    game = fixed_game()
    game.hands["a"][-1] = "m2"
    game.hands["b"] = ["m1", "m3"] + SPARSE[2:]
    game.act("a", "discard", tile="m2")
    assert game.phase == "playing"
    assert game.current_player_id == "b"
    assert "chow" not in game.public_state("b")["legal_actions"]
    with pytest.raises(ValueError):
        game.act("b", "chow", tiles=["m1", "m3"])


def test_self_draw_only_charges_unwon_players_and_does_not_finish_early():
    game = fixed_game()
    game.hands["a"] = WAITING + ["p5"]
    game.act("a", "win")
    assert not game.finished
    assert game.winners == ["a"]
    assert game.current_player_id == "b"
    assert game.scores == {"a": 6, "b": -2, "c": -2, "d": -2}
    assert game.public_state("b")["settlement"] == {}
    assert game.public_state("b")["players"][0]["hand"] == []
    assert game.public_state("b")["players"][0]["has_won"]
    assert game.public_state("a")["legal_actions"] == []
    assert game.win_events[0] == {"id": 1, "user_id": "a", "source_id": None, "kind": "self_draw",
                                  "fan": 2, "label": "平胡·自摸", "amount": 6, "tile": None}
    game.hands["b"] = WAITING + ["p5"]
    game.act("b", "win")
    assert game.scores == {"a": 6, "b": 2, "c": -4, "d": -4}
    assert not game.finished
    game.hands["c"] = WAITING + ["p5"]
    game.act("c", "win")
    assert game.finished
    assert game.winners == ["a", "b", "c"]
    assert game.current_player_id is None
    assert game.scores == {"a": 6, "b": 2, "c": -2, "d": -6}
    assert game.public_state("d")["settlement"] == game.scores
    assert [event["id"] for event in game.win_events] == [1, 2, 3]


def test_one_discard_can_win_three_hands_without_copying_the_physical_tile():
    game = fixed_game()
    for uid in IDS[1:]:
        game.hands[uid] = list(WAITING)
    before_count = Counter(sum(game.hands.values(), []) + game.wall)
    game.act("a", "discard", tile="p5")
    assert game.current_player_id == "b"
    for uid in IDS[1:]:
        game.act(uid, "win")
    assert game.finished
    assert game.winners == ["b", "c", "d"]
    assert game.scores == {"a": -3, "b": 1, "c": 1, "d": 1}
    assert game.discards["a"][-1] == "p5"
    assert all(len(game.hands[uid]) == 13 for uid in IDS[1:])
    assert Counter(sum(game.hands.values(), []) + game.wall + sum(game.discards.values(), [])) == before_count


def test_winning_response_can_be_declined_and_pung_is_cancelled_after_any_win():
    game = fixed_game()
    game.hands["b"] = list(WAITING)
    game.hands["c"] = list(WAITING)
    game.hands["d"] = ["p5", "p5"] + SPARSE[:11]
    game.act("a", "discard", tile="p5")
    game.act("b", "pass")
    game.act("c", "win")
    assert game.winners == ["c"]
    assert game.current_player_id == "b"
    assert game.phase == "playing"
    assert not game.melds["d"]


@pytest.mark.parametrize("offset", range(4))
@pytest.mark.parametrize("kind", ["discard", "rob_kong"])
@pytest.mark.parametrize(
    "winning_positions,already_won_positions,next_position",
    [
        ((2,), (), 1),
        ((3,), (), 1),
        ((1, 3), (), 2),
        ((2,), (1,), 3),
        ((2, 3), (1,), None),
    ],
)
def test_reaction_continuation_uses_source_and_skips_winners(
    offset, kind, winning_positions, already_won_positions, next_position
):
    """锁定本房接续约定；这些局面可区分放炮者下家与最后胡家下家。"""
    ids = IDS[offset:] + IDS[:offset]
    source = ids[0]
    game = fixed_game(ids)
    game.winners = [ids[position] for position in already_won_positions]
    for position in winning_positions:
        game.hands[ids[position]] = list(WAITING)
    if kind == "rob_kong":
        game.melds[source] = [{"type": "pung", "tiles": ["p5"] * 3, "concealed": False,
                               "from_user_id": ids[-1]}]
        game.hands[source] = ["p5"] + SPARSE[:10]
    original_wall = list(game.wall)
    original_hands = deepcopy(game.hands)
    game.act(source, "kong" if kind == "rob_kong" else "discard", tile="p5")
    for index, position in enumerate(winning_positions):
        assert game.phase == "reaction"
        assert game.current_player_id == ids[position]
        game.act(ids[position], "win")
        if index < len(winning_positions) - 1:
            assert game.wall == original_wall
    expected_winners = [ids[position] for position in already_won_positions + winning_positions]
    assert game.winners == expected_winners
    assert sum(game.scores.values()) == 0
    assert not game.reaction_queue
    assert game.reaction_kind is None
    assert game.pending_kong is None
    assert game.discards[source][-1] == "p5"
    for position in winning_positions:
        assert game.hands[ids[position]] == original_hands[ids[position]]
    if next_position is None:
        assert game.finished
        assert game.current_player_id is None
        assert game.wall == original_wall
    else:
        assert not game.finished
        assert game.phase == "playing"
        assert game.current_player_id == ids[next_position]
        assert game.wall == original_wall[:-1]
        assert len(game.hands[ids[next_position]]) == len(original_hands[ids[next_position]]) + 1
        assert game.last_drawn_tile == original_wall[-1]
    if kind == "rob_kong":
        assert not game.kong_payments
        assert game.melds[source][0]["type"] == "pung"


@pytest.mark.parametrize("offset", range(4))
def test_normal_turns_follow_ring_and_skip_won_players(offset):
    ids = IDS[offset:] + IDS[:offset]
    game = SichuanMahjongGame(ids, seed=19)
    for uid in ids:
        game.act(uid, "dingque", **{key: value for key, value in game.suggest_action(uid).items() if key != "action"})
    first = ids[0]
    game.missing_suits[first] = "s"
    game.hands[first] = WAITING + ["p5"]
    game.act(first, "win")
    seen = []
    for uid in ids[1:]:
        assert game.current_player_id == uid
        seen.append(uid)
        suggestion = game.suggest_action(uid)
        if suggestion["action"] != "discard":
            candidates = [tile for tile in game.hands[uid] if tile[0] == game.missing_suits[uid]] or game.hands[uid]
            suggestion = {"action": "discard", "tile": candidates[0]}
        game.act(uid, **suggestion)
        pass_reactions(game)
    assert seen == ids[1:]
    assert game.current_player_id == ids[1]


def test_kong_charges_correct_players_and_keeps_concealed_tiles_private():
    game = fixed_game()
    game.hands["a"] = ["m2"] * 4 + SPARSE[:10]
    game.act("a", "kong", tile="m2")
    assert game.scores == {"a": 6, "b": -2, "c": -2, "d": -2}
    assert game.last_drawn_tile == "m8"
    assert game.public_state("b")["players"][0]["melds"][0]["tiles"] == []
    assert len(game.public_state("a")["players"][0]["melds"][0]["tiles"]) == 4


def test_direct_kong_only_charges_discarder():
    game = fixed_game()
    game.hands["a"][-1] = "m2"
    game.hands["b"] = ["m2"] * 3 + SPARSE[:10]
    game.act("a", "discard", tile="m2")
    game.act("b", "kong", tile="m2")
    assert game.scores == {"a": -2, "b": 2, "c": 0, "d": 0}
    assert game.current_player_id == "b"
    assert game.last_drawn_tile == "m8"


def setup_added_kong():
    game = fixed_game()
    game.melds["a"] = [{"type": "pung", "tiles": ["p5"] * 3, "concealed": False, "from_user_id": "d"}]
    game.hands["a"] = ["p5"] + SPARSE[:10]
    return game


def test_added_kong_collects_one_unit_and_won_players_do_not_pay():
    game = setup_added_kong()
    game.winners = ["d"]
    game.act("a", "kong", tile="p5")
    assert game.phase == "playing"
    assert game.scores == {"a": 2, "b": -1, "c": -1, "d": 0}
    assert game.melds["a"][0]["type"] == "kong"


def test_rob_added_kong_supports_multiple_winners_and_never_collects_kong_score():
    game = setup_added_kong()
    game.hands["b"] = list(WAITING)
    game.hands["c"] = list(WAITING)
    original_wall = list(game.wall)
    game.act("a", "kong", tile="p5")
    assert game.phase == "reaction"
    assert game.reaction_kind == "rob_kong"
    assert game.scores == dict.fromkeys(IDS, 0)
    assert game.melds["a"][0]["type"] == "pung"
    game.act("b", "win")
    game.act("c", "win")
    assert game.winners == ["b", "c"]
    assert not game.finished
    assert game.current_player_id == "d"
    assert game.scores == {"a": -4, "b": 2, "c": 2, "d": 0}
    assert not game.kong_payments
    assert game.melds["a"][0]["type"] == "pung"
    assert game.discards["a"][-1] == "p5"
    assert game.wall == original_wall[:-1]


def test_declining_rob_kong_commits_added_kong_once():
    game = setup_added_kong()
    game.hands["b"] = list(WAITING)
    game.act("a", "kong", tile="p5")
    game.act("b", "pass")
    assert game.current_player_id == "a"
    assert game.phase == "playing"
    assert game.scores == {"a": 3, "b": -1, "c": -1, "d": -1}
    assert game.melds["a"][0]["type"] == "kong"


def test_empty_wall_disables_new_kong_and_invalid_actions_are_atomic():
    game = setup_added_kong()
    game.wall = []
    before = game.public_state("a")
    for uid, action, payload in [("a", "kong", {"tile": "p5"}), ("b", "discard", {"tile": "m1"}),
                                 ("a", "discard", {"tile": "z1"}), ("a", "chow", {}), ("a", "win", {})]:
        with pytest.raises(ValueError):
            game.act(uid, action, **payload)
        assert game.public_state("a") == before


@pytest.mark.parametrize(("hand", "base", "fan"), [
    (WAITING + ["p5"], "平胡", 1),
    (["m1"] * 3 + ["m4"] * 3 + ["p2"] * 3 + ["p8"] * 3 + ["p5"] * 2, "对对胡", 2),
    (["m1"] * 3 + ["m4"] * 3 + ["m7"] * 3 + ["m8"] * 3 + ["m5"] * 2, "清对", 4),
    (["m2"] * 3 + ["m5"] * 3 + ["p2"] * 3 + ["p8"] * 3 + ["p5"] * 2, "将对", 4),
    (["m1", "m2", "m3", "m7", "m8", "m9", "p1", "p2", "p3", "p7", "p8", "p9", "p1", "p1"], "带幺九", 3),
    (["m1", "m2", "m3"] * 2 + ["m7", "m8", "m9"] + ["m9"] * 3 + ["m1"] * 2, "清幺九", 6),
    (["m1"] * 2 + ["m3"] * 2 + ["m5"] * 2 + ["p1"] * 2 + ["p3"] * 2 + ["p5"] * 2 + ["p7"] * 2, "七对", 3),
    (["m1"] * 4 + ["m3"] * 2 + ["m5"] * 2 + ["p1"] * 2 + ["p3"] * 2 + ["p5"] * 2, "龙七对", 5),
    (["m1"] * 4 + ["m3"] * 2 + ["m4"] * 2 + ["m5"] * 2 + ["m7"] * 2 + ["m9"] * 2, "清龙七对", 6),
])
def test_scoring_patterns_roots_and_seven_pair_quads(hand, base, fan):
    value = rules.score_hand(hand)
    assert value["base_label"] == base
    assert value["fan"] == fan
    assert value["multiplier"] == 2 ** (fan - 1)


def test_additional_roots_are_counted_once_and_fan_is_capped():
    hand = ["m1"] * 4 + ["p3"] * 4 + ["p5"] * 2 + ["m7"] * 2 + ["m9"] * 2
    value = rules.score_hand(hand, self_draw=True, after_kong=True)
    assert value["roots"] == 2
    assert "1根" in value["label"]
    assert value["fan"] == 6 and value["capped"]
    assert value["multiplier"] == 32


def test_discard_after_kong_and_rob_kong_have_separate_bonus():
    hand = WAITING + ["p5"]
    assert rules.score_hand(hand, kong_discard=True)["fan"] == 2
    assert rules.score_hand(hand, rob_kong=True)["fan"] == 2
    melds = [{"type": "pung", "tiles": [tile] * 3} for tile in ["m1", "m4", "p2", "p8"]]
    assert "金钩钓" in rules.score_hand(["p5"] * 2, melds)["label"]


def test_kong_win_and_kong_discard_score_real_round_context():
    game = fixed_game()
    game.hands["a"] = ["m2"] * 4 + ["m4", "m5", "m6", "p2", "p3", "p4", "p7", "p8", "p9", "p5"]
    game.wall[0] = "p5"
    game.act("a", "kong", tile="m2")
    game.act("a", "win")
    assert game.win_events[0]["fan"] == 4
    assert game.win_events[0]["label"] == "平胡·1根·自摸·杠上花"
    assert game.win_events[0]["amount"] == 24
    assert game.win_events[0]["tile"] == "p5"
    assert game.scores == {"a": 30, "b": -10, "c": -10, "d": -10}

    game = fixed_game()
    game.hands["a"] = ["m2"] * 4 + SPARSE[:10]
    game.hands["b"] = list(WAITING)
    game.wall[0] = "p5"
    game.act("a", "kong", tile="m2")
    game.act("a", "discard", tile="p5")
    game.act("b", "win")
    assert game.win_events[0]["fan"] == 2
    assert game.win_events[0]["label"] == "平胡·杠上炮"
    assert game.scores["a"] == 4
    assert game.public_state("a")["players"][1]["winning_tile"] == "p5"


def test_dead_wait_with_fifth_copy_is_not_ready_and_max_ready_fan_is_used():
    game = fixed_game()
    game.melds["b"] = [{"type": "pung", "tiles": ["m1"] * 3, "concealed": False}]
    game.hands["b"] = ["m1", "m4", "m5", "m6", "p2", "p3", "p4", "p7", "p8", "p9"]
    assert game._ready_value("b") is None
    game.melds["b"] = []
    game.hands["b"] = ["m1"] * 4 + ["m3"] * 2 + ["m5"] * 2 + ["p1"] * 2 + ["p3"] * 2 + ["p5"]
    assert game._ready_value("b")["multiplier"] == 16


def test_snapshot_event_changes_do_not_mutate_engine_state():
    game = fixed_game()
    game.hands["a"] = WAITING + ["p5"]
    game.act("a", "win")
    state = game.public_state("b")
    state["win_events"][0]["fan"] = 100
    state["players"][0]["discards"].append("m1")
    assert game.win_events[0]["fan"] == 2
    assert not game.discards["a"]


def test_safe_amount_limits_prevent_javascript_integer_overflow():
    with pytest.raises(ValueError, match="精度"):
        SichuanMahjongGame(IDS, buy_in=rules.MAX_BUY_IN + 1)
    with pytest.raises(ValueError, match="精度"):
        SichuanMahjongGame(IDS, buy_in=rules.MAX_BUY_IN, base_stake=rules.MAX_BASE_STAKE + 1)
    game = SichuanMahjongGame(IDS, buy_in=rules.MAX_BUY_IN, base_stake=rules.MAX_BASE_STAKE)
    for uid in IDS:
        game.act(uid, "dingque", suit="s")
    game.hands["a"] = ["m1"] * 4 + ["m3"] * 2 + ["m4"] * 2 + ["m5"] * 2 + ["m7"] * 2 + ["m9"] * 2
    game.act("a", "win")
    assert abs(game.scores["a"]) <= 2**53 - 1
    assert all(abs(player["stack"]) <= 2**53 - 1 for player in game.public_state("a")["players"])


@pytest.mark.parametrize("hand", [WAITING + ["s1"], ["m1"] * 5 + SPARSE[:9], ["z1"] * 14, WAITING])
def test_invalid_or_three_suit_hand_cannot_win(hand):
    assert not rules.is_sichuan_win(hand)


def test_flowers_ready_compensation_and_kong_refund_are_zero_sum():
    game = fixed_game()
    game.hands["a"] = list(SPARSE)
    game.hands["b"] = list(WAITING)
    game.hands["c"] = list(SPARSE)
    game.hands["d"] = SPARSE[:12] + ["s1"]
    game._pay_kong("a", "concealed")
    assert game.scores["a"] == 6
    game._finish_draw()
    assert game.finished
    assert game.flow_details["flower_pigs"] == ["d"]
    assert set(game.flow_details["ready_players"]) == {"b"}
    assert set(game.flow_details["not_ready_players"]) == {"a", "c"}
    assert len(game.flow_details["kong_refunds"]) == 3
    assert game.scores == {"a": 31, "b": 34, "c": 31, "d": -96}
    assert sum(game.scores.values()) == 0
    before = dict(game.scores)
    with pytest.raises(ValueError):
        game.act("a", "discard", tile="m1")
    assert game.scores == before


def test_ready_player_keeps_kong_score_and_winner_is_excluded_from_ready_compensation():
    game = fixed_game()
    game.winners = ["a"]
    game.hands["b"] = list(WAITING)
    game.hands["c"] = list(SPARSE)
    game.hands["d"] = list(SPARSE)
    game._pay_kong("b", "added")
    game._finish_draw()
    assert game.scores == {"a": 0, "b": 4, "c": -2, "d": -2}
    assert not game.flow_details["kong_refunds"]


def test_public_snapshot_is_defensive_and_ai_ignores_opponents_hands():
    game = SichuanMahjongGame(IDS, seed=32)
    choice = game.suggest_action("a")
    game.hands["b"], game.hands["c"] = game.hands["c"], game.hands["b"]
    assert game.suggest_action("a") == choice
    for uid in IDS:
        game.act(uid, **game.suggest_action(uid))
    choice = game.suggest_action("a")
    game.hands["b"], game.hands["c"] = game.hands["c"], game.hands["b"]
    assert game.suggest_action("a") == choice
    state = game.public_state("a")
    state["players"][0]["hand"].clear()
    assert len(game.hands["a"]) == 14


@pytest.mark.parametrize("seed", range(30))
def test_ai_full_games_terminate_conserve_tiles_and_scores(seed):
    game = SichuanMahjongGame(IDS, seed=seed, base_stake=2)
    expected = Counter({tile: 4 for tile in rules.TILES})
    for _ in range(700):
        owned = sum(game.hands.values(), []) + game.wall + sum(game.discards.values(), [])
        owned += [tile for uid in IDS for meld in game.melds[uid] for tile in meld["tiles"]]
        assert Counter(owned) == expected
        assert sum(game.scores.values()) == 0
        assert len(game.winners) == len(set(game.winners))
        if game.finished:
            break
        assert game.current_player_id not in game.winners
        uid = game.current_player_id
        suggestion = game.suggest_action(uid)
        assert suggestion["action"] in game.public_state(uid)["legal_actions"]
        game.act(uid, **suggestion)
    assert game.finished
    assert not game.public_state("a")["legal_actions"]
    assert len(game.winners) <= 3
    assert sum(game.public_state("a")["settlement"].values()) == 0


@pytest.mark.parametrize("ids", [["a", "b", "c"], ["a", "a", "c", "d"], ["", "b", "c", "d"]])
def test_exact_distinct_player_count_is_required(ids):
    with pytest.raises(ValueError):
        SichuanMahjongGame(ids)
