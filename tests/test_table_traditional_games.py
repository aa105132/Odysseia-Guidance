"""斗地主与麻将规则、状态保护、隐藏信息和机器人完整对局回归。"""

from collections import Counter
from copy import deepcopy
import importlib

import pytest


games = importlib.import_module("src.chat.features.games.blackjack-web.traditional_games")
LandlordGame = games.LandlordGame
MahjongGame = games.MahjongGame


def cards(*ranks):
    used = Counter()
    result = []
    for rank in ranks:
        if rank in ("JokerSmall", "JokerBig"):
            result.append(rank)
        else:
            result.append(games.POKER_SUITS[used[rank]] + rank)
            used[rank] += 1
    return result


@pytest.mark.parametrize(("ranks", "kind", "rank"), [
    (("3",), "single", 3),
    (("2", "2"), "pair", 15),
    (("A", "A", "A"), "triple", 14),
    (("4", "4", "4", "3"), "triple_single", 4),
    (("4", "4", "4", "3", "3"), "triple_pair", 4),
    (("10", "J", "Q", "K", "A"), "straight", 14),
    (("3", "3", "4", "4", "5", "5"), "pair_straight", 5),
    (("3", "3", "3", "4", "4", "4"), "airplane", 4),
    (("3", "3", "3", "4", "4", "4", "8", "9"), "airplane_single", 4),
    (("3", "3", "3", "4", "4", "4", "8", "8", "9", "9"), "airplane_pair", 4),
    (("3", "3", "3", "3", "8", "8"), "four_two_single", 3),
    (("3", "3", "3", "3", "8", "8", "9", "9"), "four_two_pair", 3),
    (("3", "3", "3", "3"), "bomb", 3),
    (("JokerSmall", "JokerBig"), "rocket", 17),
])
def test_landlord_patterns(ranks, kind, rank):
    pattern = games.classify_landlord_cards(cards(*ranks))
    assert pattern.kind == kind
    assert pattern.rank == rank


@pytest.mark.parametrize("hand", [
    [], ["Club3", "Club3"], ["NotACard"],
    cards("J", "Q", "K", "A", "2"),
    cards("A", "A", "2", "2", "3", "3"),
    cards("3", "3", "3", "5", "5", "5"),
    cards("3", "3", "4", "5"),
])
def test_landlord_rejects_invalid_patterns(hand):
    with pytest.raises(ValueError):
        games.classify_landlord_cards(hand)


def test_landlord_comparison_requires_same_shape_and_honors_bombs():
    classify = lambda *ranks: games.classify_landlord_cards(cards(*ranks))
    assert games.landlord_beats(classify("4", "4"), classify("3", "3"))
    assert not games.landlord_beats(classify("A"), classify("3", "3"))
    assert not games.landlord_beats(classify("4", "5", "6", "7", "8", "9"), classify("3", "4", "5", "6", "7"))
    assert games.landlord_beats(classify("3", "3", "3", "3"), classify("JokerBig"))
    assert games.landlord_beats(classify("JokerSmall", "JokerBig"), classify("2", "2", "2", "2"))
    assert not games.landlord_beats(classify("2", "2", "2", "2"), classify("JokerSmall", "JokerBig"))


def test_landlord_deal_bidding_and_opponent_privacy():
    game = LandlordGame(["a", "b", "c"], seed=12)
    state = game.public_state("a")
    assert [p["hand_count"] for p in state["players"]] == [17, 17, 17]
    assert state["bottom_cards"] == []
    assert state["players"][1]["hand"] == []
    assert state["players"][2]["hand"] == []
    game.act("a", "bid", bid=1)
    before = game.public_state("b")
    with pytest.raises(ValueError):
        game.act("b", "bid", bid=1)
    assert game.public_state("b") == before
    game.act("b", "bid", bid=2)
    game.act("c", "bid", bid=0)
    state = game.public_state("b")
    assert game.landlord_id == "b"
    assert game.current_player_id == "b"
    assert [p["hand_count"] for p in state["players"]] == [17, 20, 17]
    assert len(state["bottom_cards"]) == 3
    assert len(set(sum(game.hands.values(), []))) == 54


def test_all_pass_bidding_redeals_without_selecting_a_landlord():
    game = LandlordGame(["a", "b", "c"], seed=21)
    original = deepcopy(game.hands)
    for uid in ["a", "b", "c"]:
        game.act(uid, "bid", bid=0)
    assert game.phase == "bidding"
    assert game.landlord_id is None
    assert game.deal_count == 2
    assert game.hands != original
    assert game.current_player_id == "b"
    assert game.public_state("b")["seat_actions"] == {}


def test_landlord_bidding_seat_actions_clear_when_play_begins():
    game = LandlordGame(["a", "b", "c"], seed=21)
    assert game.public_state("a")["seat_actions"] == {}
    game.act("a", "bid", bid=1)
    game.act("b", "bid", bid=0)
    assert game.public_state("c")["seat_actions"] == {
        "a": {"action": "bid", "cards": [], "label": "1分", "bid": 1},
        "b": {"action": "bid", "cards": [], "label": "不叫", "bid": 0},
    }
    before = game.public_state("c")
    with pytest.raises(ValueError):
        game.act("c", "bid", bid=1)
    assert game.public_state("c") == before
    game.act("c", "bid", bid=2)
    assert game.phase == "playing"
    assert game.public_state("c")["seat_actions"] == {}


@pytest.fixture
def landlord_seat_actions_game():
    game = LandlordGame(["a", "b", "c"], seed=21)
    game.act("a", "bid", bid=3)
    game.hands = {
        "a": cards("3", "3", "7", "7", "9"),
        "b": cards("4", "4", "8", "8", "10"),
        "c": cards("5", "5", "6", "6", "J"),
    }
    return game


def test_landlord_seat_actions_keep_other_players_and_replace_own(landlord_seat_actions_game):
    game = landlord_seat_actions_game
    game.act("a", "play", cards=cards("3", "3"))
    game.act("b", "play", cards=cards("4", "4"))
    assert game.public_state("c")["seat_actions"] == {
        "a": {"action": "play", "cards": cards("3", "3"), "label": "对子"},
        "b": {"action": "play", "cards": cards("4", "4"), "label": "对子"},
    }
    game.act("c", "pass")
    game.act("a", "play", cards=cards("7", "7"))
    assert game.public_state("b")["seat_actions"] == {
        "a": {"action": "play", "cards": cards("7", "7"), "label": "对子"},
        "b": {"action": "play", "cards": cards("4", "4"), "label": "对子"},
        "c": {"action": "pass", "cards": [], "label": "不出"},
    }


def test_landlord_seat_actions_clear_only_after_valid_new_lead(landlord_seat_actions_game):
    game = landlord_seat_actions_game
    game.act("a", "play", cards=cards("3", "3"))
    game.act("b", "pass")
    game.act("c", "pass")
    before = game.public_state("a")
    assert before["last_play"] is None
    assert before["seat_actions"] == {
        "a": {"action": "play", "cards": cards("3", "3"), "label": "对子"},
        "b": {"action": "pass", "cards": [], "label": "不出"},
        "c": {"action": "pass", "cards": [], "label": "不出"},
    }
    for invalid in (
        {"action": "play", "cards": ["Club2"]},
        {"action": "play", "cards": cards("7", "9")},
        {"action": "pass"},
    ):
        with pytest.raises(ValueError):
            game.act("a", **invalid)
        assert game.public_state("a") == before
    game.act("a", "play", cards=cards("7", "7"))
    assert game.public_state("b")["seat_actions"] == {
        "a": {"action": "play", "cards": cards("7", "7"), "label": "对子"},
    }


def test_landlord_seat_actions_copy_public_data_without_exposing_hands(landlord_seat_actions_game):
    game = landlord_seat_actions_game
    played = cards("3", "3")
    game.act("a", "play", cards=played)
    expected = {"a": {"action": "play", "cards": cards("3", "3"), "label": "对子"}}
    played.append("JokerBig")
    state = game.public_state("b")
    assert state["seat_actions"] == expected
    assert state["players"][0]["hand"] == []
    assert state["players"][2]["hand"] == []
    state["seat_actions"]["a"]["cards"].clear()
    state["seat_actions"]["a"]["label"] = "已篡改"
    state["seat_actions"]["c"] = {"action": "pass", "cards": [], "label": "不出"}
    assert game.public_state("c")["seat_actions"] == expected
    assert game.last_play["cards"] == cards("3", "3")


def test_landlord_seat_actions_keep_final_trick_after_winning(landlord_seat_actions_game):
    game = landlord_seat_actions_game
    game.hands["b"] = cards("4", "4")
    game.act("a", "play", cards=cards("3", "3"))
    game.act("b", "play", cards=cards("4", "4"))
    assert game.finished
    assert game.public_state("c")["seat_actions"] == {
        "a": {"action": "play", "cards": cards("3", "3"), "label": "对子"},
        "b": {"action": "play", "cards": cards("4", "4"), "label": "对子"},
    }


def test_landlord_two_passes_restore_lead_and_invalid_play_is_atomic():
    game = LandlordGame(["a", "b", "c"], seed=21)
    game.act("a", "bid", bid=3)
    with pytest.raises(ValueError):
        game.act("a", "pass")
    first = game.hands["a"][0]
    game.act("a", "play", cards=[first])
    before = game.public_state("b")
    with pytest.raises(ValueError):
        game.act("b", "play", cards=[first])
    assert game.public_state("b") == before
    game.act("b", "pass")
    game.act("c", "pass")
    assert game.current_player_id == "a"
    assert game.last_play is None
    assert game.public_state("a")["legal_actions"] == ["play"]


def test_farmer_win_rewards_both_farmers_and_total_scores_are_zero():
    game = LandlordGame(["a", "b", "c"], seed=21)
    game.act("a", "bid", bid=2)
    game.act("b", "bid", bid=0)
    game.act("c", "bid", bid=0)
    game.hands = {"a": cards("3", "4"), "b": cards("A"), "c": cards("2")}
    game.act("a", "play", cards=["Club3"])
    game.act("b", "play", cards=["ClubA"])
    assert game.finished
    assert game.current_player_id is None
    assert game.winners == ["b", "c"]
    assert game.scores == {"a": -8, "b": 4, "c": 4}
    assert sum(game.scores.values()) == 0


def test_landlord_bomb_and_spring_each_double_score():
    game = LandlordGame(["a", "b", "c"], seed=21)
    game.act("a", "bid", bid=3)
    game.hands["a"] = cards("3", "3", "3", "3")
    game.act("a", "play", cards=list(game.hands["a"]))
    assert game.multiplier == 4
    assert game.scores == {"a": 24, "b": -12, "c": -12}


@pytest.mark.parametrize("winner", ["a", "b"])
def test_landlord_full_multiplier_is_not_capped_by_buy_in(winner):
    game = LandlordGame(["a", "b", "c"], seed=21)
    game.act("a", "bid", bid=3)
    game.multiplier = 64
    game._finish(winner)
    assert game.effective_stake == 384
    assert abs(game.scores["a"]) == 768
    assert abs(game.scores["b"]) == 384
    assert abs(game.scores["c"]) == 384
    assert sum(game.scores.values()) == 0
    state = game.public_state("a")
    assert state["settlement"] == game.scores
    for player in state["players"]:
        assert player["stack"] == 100 + player["score_delta"]
    assert any(player["stack"] < 0 for player in state["players"])


def test_traditional_games_expose_no_settlement_until_finished():
    for game in (LandlordGame(["a", "b", "c"], seed=1), MahjongGame(["a", "b", "c", "d"], seed=1)):
        state = game.public_state("a")
        assert state["settlement"] == {}
        assert all(player["score_delta"] == 0 and player["stack"] == 100 for player in state["players"])


@pytest.mark.parametrize("buy_in", [100, 250, 1000000])
def test_traditional_games_accept_custom_buy_in_without_rule_limit(buy_in):
    for game in (LandlordGame(["a", "b", "c"], seed=1, buy_in=buy_in), MahjongGame(["a", "b", "c", "d"], seed=1, buy_in=buy_in)):
        state = game.public_state("a")
        assert state["buy_in"] == buy_in
        assert all(player["stack"] == buy_in for player in state["players"])


@pytest.mark.parametrize("buy_in", [0, 99, -1, 100.5, True, 2**53])
def test_traditional_games_validate_buy_in_integer_and_minimum(buy_in):
    for engine, ids in ((LandlordGame, ["a", "b", "c"]), (MahjongGame, ["a", "b", "c", "d"])):
        with pytest.raises(ValueError):
            engine(ids, buy_in=buy_in)


@pytest.mark.parametrize("seed", range(20))
def test_landlord_ai_finishes_a_complete_legal_game(seed):
    game = LandlordGame(["a", "b", "c"], seed=seed)
    for _ in range(500):
        if game.finished:
            break
        uid = game.current_player_id
        suggestion = game.suggest_action(uid)
        game.act(uid, **suggestion)
    assert game.finished
    assert game.winners
    assert sum(game.scores.values()) == 0
    assert not game.public_state("a")["legal_actions"]


@pytest.mark.parametrize(("hand", "meld_count", "expected"), [
    (["m1", "m2", "m3", "p2", "p3", "p4", "s7", "s8", "s9", "z1", "z1", "z1", "z7", "z7"], 0, True),
    (["m1", "m1", "m2", "m2", "m3", "m3", "p4", "p4", "p5", "p5", "z1", "z1", "z7", "z7"], 0, True),
    (["m1", "m2", "m3", "p2", "p3", "p4", "z1", "z1", "z1", "z7", "z7"], 1, True),
    (["z1", "z1"], 4, True),
    (["z1", "z2"], 4, False),
    (["m1", "m2", "m3", "p2", "p3", "p4", "s7", "s8", "s9", "z1", "z2", "z3", "z7", "z7"], 0, False),
    (["z1"] * 5 + ["m1"] * 3 + ["p1"] * 3 + ["s1"] * 3, 0, False),
    (["m1", "m9", "p1", "p9", "s1", "s9", "z1", "z2", "z3", "z4", "z5", "z6", "z7", "m1"], 0, False),
    (["m1", "m1", "m1", "m1", "m2", "m2", "p3", "p3", "p4", "p4", "s5", "s5", "z6", "z6"], 0, False),
])
def test_mahjong_winning_shapes(hand, meld_count, expected):
    assert games.is_mahjong_win(hand, meld_count) is expected


def fixed_mahjong():
    game = MahjongGame(["a", "b", "c", "d"], seed=42)
    game.hands = {
        "a": ["m1", "m2", "m4", "m6", "m8", "p2", "p4", "p6", "p8", "s2", "s4", "s6", "z1", "z7"],
        "b": ["m3", "m5", "m7", "m9", "p1", "p3", "p5", "p7", "p9", "s1", "s3", "s5", "z2"],
        "c": ["m3", "m5", "m7", "m9", "p1", "p3", "p5", "p7", "p9", "s1", "s3", "s5", "z3"],
        "d": ["m3", "m5", "m7", "m9", "p1", "p3", "p5", "p7", "p9", "s1", "s3", "s5", "z4"],
    }
    game.wall = ["z6", "z5", "z4"]
    return game


def test_mahjong_initial_deal_is_complete_and_private():
    game = MahjongGame(["a", "b", "c", "d"], seed=42)
    assert [len(game.hands[uid]) for uid in game.player_ids] == [14, 13, 13, 13]
    assert len(game.wall) == 83
    assert Counter(sum(game.hands.values(), []) + game.wall) == Counter({tile: 4 for tile in games.MAHJONG_TILES})
    state = game.public_state("a")
    assert len(state["players"][0]["hand"]) == 14
    assert all(player["hand"] == [] for player in state["players"][1:])
    state["players"][0]["hand"].clear()
    assert len(game.hands["a"]) == 14


def test_mahjong_self_draw_and_fixed_scoring():
    game = fixed_mahjong()
    game.hands["a"] = ["m1", "m2", "m3", "p2", "p3", "p4", "s7", "s8", "s9", "z1", "z1", "z1", "z7", "z7"]
    assert "win" in game.public_state("a")["legal_actions"]
    game.act("a", "win")
    assert game.finished
    assert game.winners == ["a"]
    assert game.scores == {"a": 3, "b": -1, "c": -1, "d": -1}


def test_mahjong_discard_win_takes_priority_over_pung_and_chow():
    game = fixed_mahjong()
    game.hands["b"] = ["m1", "m1", "p1", "p3", "p5", "p7", "p9", "s1", "s3", "s5", "s7", "z2", "z3"]
    # 玩家 c 等待 m1 配对，胡牌响应必须排在玩家 b 的碰牌之前。
    game.hands["c"] = ["m2", "m3", "m4", "p2", "p3", "p4", "s7", "s8", "s9", "z1", "z1", "z1", "m1"]
    game.act("a", "discard", tile="m1")
    assert game.current_player_id == "c"
    assert game.public_state("c")["legal_actions"] == ["win", "pass"]
    before = game.public_state("b")
    with pytest.raises(ValueError):
        game.act("b", "pung", tile="m1")
    assert game.public_state("b") == before
    game.act("c", "win")
    assert game.scores == {"a": -3, "b": 0, "c": 3, "d": 0}
    assert game.discards["a"] == []
    assert game.hands["c"].count("m1") == 2


def test_mahjong_pass_win_then_pung_resolves_without_drawing():
    game = fixed_mahjong()
    game.hands["b"][:2] = ["m1", "m1"]
    game.hands["c"] = ["m2", "m3", "m4", "p2", "p3", "p4", "s7", "s8", "s9", "z1", "z1", "z1", "m1"]
    game.act("a", "discard", tile="m1")
    game.act("c", "pass")
    assert game.current_player_id == "b"
    game.act("b", "pung", tile="m1")
    assert game.current_player_id == "b"
    assert game.phase == "playing"
    assert len(game.hands["b"]) == 11
    assert game.melds["b"][0]["type"] == "pung"
    assert len(game.wall) == 3
    assert game.public_state("b")["legal_actions"] == ["discard"]


def test_mahjong_multiple_winners_use_nearest_seat_and_allow_declining():
    game = fixed_mahjong()
    waiting = ["m2", "m3", "m4", "p2", "p3", "p4", "s7", "s8", "s9", "z1", "z1", "z1", "m1"]
    game.hands["b"] = list(waiting)
    game.hands["d"] = list(waiting)
    game.act("a", "discard", tile="m1")
    assert game.current_player_id == "b"
    game.act("b", "pass")
    assert game.current_player_id == "d"
    game.act("d", "win")
    assert game.winners == ["d"]
    assert game.scores["a"] == -3


def test_mahjong_only_next_seat_can_chow_and_invalid_choice_is_atomic():
    game = fixed_mahjong()
    game.hands["b"][:2] = ["m2", "m3"]
    game.hands["c"][:2] = ["m2", "m3"]
    game.act("a", "discard", tile="m1")
    assert game.current_player_id == "b"
    state = game.public_state("b")
    assert state["chow_options"] == [["m1", "m2", "m3"]]
    assert game.public_state("c")["legal_actions"] == []
    with pytest.raises(ValueError):
        game.act("b", "chow", tiles=["m1", "m2", "m4"])
    assert game.public_state("b") == state
    game.act("b", "chow", tiles=["m2", "m3"])
    assert game.melds["b"][0]["tiles"] == ["m1", "m2", "m3"]
    assert len(game.hands["b"]) == 11


def test_mahjong_concealed_kong_is_private_and_draws_replacement():
    game = fixed_mahjong()
    game.hands["a"][:4] = ["m1"] * 4
    game.act("a", "kong", tile="m1")
    assert len(game.hands["a"]) == 11
    assert len(game.wall) == 2
    assert game.last_drawn_tile == "z6"
    assert game.melds["a"][0]["tiles"] == ["m1"] * 4
    assert game.public_state("b")["players"][0]["melds"][0]["tiles"] == []
    assert game.public_state("a")["players"][0]["melds"][0]["tiles"] == ["m1"] * 4


def test_mahjong_open_kong_takes_discard_and_draws_replacement():
    game = fixed_mahjong()
    game.hands["b"][:3] = ["m1"] * 3
    game.act("a", "discard", tile="m1")
    assert game.current_player_id == "b"
    assert "kong" in game.public_state("b")["legal_actions"]
    game.act("b", "kong", tile="m1")
    assert len(game.hands["b"]) == 11
    assert game.melds["b"][0]["tiles"] == ["m1"] * 4
    assert game.discards["a"] == []
    assert len(game.wall) == 2


def test_mahjong_added_kong_extends_a_pung():
    game = fixed_mahjong()
    game.hands["a"] = game.hands["a"][:11]
    game.melds["a"] = [{"type": "pung", "tiles": ["m1"] * 3, "concealed": False, "from_user_id": "b"}]
    game.act("a", "kong", tile="m1")
    assert game.melds["a"][0]["type"] == "kong"
    assert game.melds["a"][0]["tiles"] == ["m1"] * 4
    assert len(game.hands["a"]) == 11


def test_mahjong_empty_wall_ends_without_score_transfer():
    game = fixed_mahjong()
    game.wall = []
    game.act("a", "discard", tile="z7")
    assert game.finished
    assert game.winners == []
    assert all(score == 0 for score in game.scores.values())


def test_mahjong_invalid_actions_do_not_change_state():
    game = fixed_mahjong()
    original = game.public_state("a")
    for uid, action, payload in [
        ("b", "discard", {"tile": "m3"}),
        ("a", "discard", {"tile": "invalid"}),
        ("a", "kong", {"tile": "m1"}),
        ("a", "win", {}),
    ]:
        with pytest.raises(ValueError):
            game.act(uid, action, **payload)
        assert game.public_state("a") == original


@pytest.mark.parametrize("seed", range(20))
def test_mahjong_ai_finishes_a_legal_conserving_game(seed):
    game = MahjongGame(["a", "b", "c", "d"], seed=seed)
    expected = Counter({tile: 4 for tile in games.MAHJONG_TILES})
    for _ in range(500):
        owned_tiles = sum(game.hands.values(), []) + game.wall + sum(game.discards.values(), [])
        owned_tiles += [tile for uid in game.player_ids for meld in game.melds[uid] for tile in meld["tiles"]]
        assert Counter(owned_tiles) == expected
        if game.finished:
            break
        uid = game.current_player_id
        suggestion = game.suggest_action(uid)
        game.act(uid, **suggestion)
    assert game.finished
    assert sum(game.scores.values()) == 0
    assert not game.public_state("a")["legal_actions"]


def test_suggestions_do_not_depend_on_opponents_hidden_hands():
    for game in (LandlordGame(["a", "b", "c"], seed=33), MahjongGame(["a", "b", "c", "d"], seed=33)):
        current = game.current_player_id
        suggestion = game.suggest_action(current)
        others = [uid for uid in game.player_ids if uid != current]
        game.hands[others[0]], game.hands[others[1]] = game.hands[others[1]], game.hands[others[0]]
        assert game.suggest_action(current) == suggestion


@pytest.mark.parametrize(("engine", "ids"), [
    (LandlordGame, ["a", "b"]), (LandlordGame, ["a", "a", "c"]),
    (MahjongGame, ["a", "b", "c"]), (MahjongGame, ["a", "b", "c", "c"]),
])
def test_exact_player_counts_are_required(engine, ids):
    with pytest.raises(ValueError):
        engine(ids)
