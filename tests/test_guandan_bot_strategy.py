"""掼蛋完整牌组、候选覆盖和公开信息边界回归。"""

from collections import Counter
from copy import deepcopy
import importlib
from itertools import combinations
import time

import pytest


m = importlib.import_module("src.chat.features.games.blackjack-web.guandan_game")


def cards(*ranks):
    used = Counter()
    result = []
    for rank in ranks:
        index = used[rank]
        used[rank] += 1
        if rank.startswith("Joker"):
            result.append(f"{rank}#{index}")
        else:
            result.append(f"{m.SUITS[index % 4]}{rank}#{index // 4}")
    return result


def game_with(hand):
    game = m.GuandanGame(["a", "b", "c", "d"], seed=0)
    game.turn_index = 0
    game.hands["a"] = hand
    game.hands["b"] = cards("3", "4", "5", "6", "7", "8", "9")
    game.hands["c"] = cards("4", "5", "6", "7", "8", "9", "10")
    game.hands["d"] = cards("5", "6", "7", "8", "9", "10", "Q")
    return game


def assert_legal(game, action):
    deepcopy(game).act("a", **action)


def test_lead_keeps_four_jacks_intact_when_small_pair_is_available():
    game = game_with(cards("J", "J", "J", "J", "4", "4", "8"))
    action = game.suggest_action("a")
    assert action["action"] == "play"
    assert not any(m.card_parts(card)[1] == 11 for card in action["cards"])
    assert m.classify_guandan_cards(action["cards"], game.level)[0].kind == "pair"
    assert_legal(game, action)


def test_lead_can_clear_a_genuine_single_without_splitting_bomb():
    game = game_with(cards("J", "J", "J", "J", "4"))
    action = game.suggest_action("a")
    assert action["cards"] == cards("4")
    assert_legal(game, action)


def test_two_bombs_are_not_broken_into_triples_or_singles():
    game = game_with(cards("7", "7", "7", "7", "J", "J", "J", "J"))
    action = game.suggest_action("a")
    assert m.classify_guandan_cards(action["cards"], game.level)[0].kind == "bomb"
    assert len(action["cards"]) == 4
    assert_legal(game, action)


def test_finish_immediately_beats_preserving_large_combinations():
    game = game_with(cards("J", "J", "J", "J"))
    action = game.suggest_action("a")
    assert set(action["cards"]) == set(game.hands["a"])
    assert_legal(game, action)


def test_opponent_last_single_can_force_a_bomb_split_for_highest_blocker():
    game = game_with(cards("J", "J", "J", "J", "4", "4"))
    game.hands["b"] = cards("10")
    game.last_pattern = m.classify_guandan_cards(cards("5"), game.level)[0]
    game.last_play = {"user_id": "d", "cards": cards("5"), **game.last_pattern.to_dict()}
    action = game.suggest_action("a")
    assert len(action["cards"]) == 1 and m.card_parts(action["cards"][0])[1] == 11
    assert_legal(game, action)


def test_teammate_lead_is_not_covered_without_an_immediate_finish():
    game = game_with(cards("J", "J", "J", "J", "4", "4"))
    game.hands["c"] = cards("3")
    game.last_pattern = m.classify_guandan_cards(cards("5"), game.level)[0]
    game.last_play = {"user_id": "c", "cards": cards("5"), **game.last_pattern.to_dict()}
    assert game.suggest_action("a") == {"action": "pass"}


@pytest.mark.parametrize("own,enemy,teammate_play,teammate_rest,other,expected_kind", [
    (("A", "4"), ("K",), ("6",), ("3",), ("7", "8"), "single"),
    (("A", "A", "A", "4"), ("K", "K", "K"), ("6", "6", "6"), ("3",), ("7", "8"), "triple"),
    (("A", "A", "A", "4", "4", "5"), ("K", "K", "K", "Q", "Q"),
     ("6", "6", "6", "7", "7"), ("3",), ("8", "9"), "full_house"),
])
def test_real_turn_can_cover_teammate_to_block_enemy_finishing_shape(
        own, enemy, teammate_play, teammate_rest, other, expected_kind):
    game = game_with(cards(*own))
    game.hands["b"] = cards(*enemy)
    game.hands["c"] = cards(*teammate_play, *teammate_rest)
    game.hands["d"] = cards(*other)
    game.turn_index = 2
    game.act("c", "play", cards=cards(*teammate_play))
    game.act("d", "pass")
    assert game.current_player_id == "a"
    # 反例的旧行为：让过后，下家实际持有的末手可以直接走完。
    losing = deepcopy(game)
    losing.act("a", "pass")
    losing.act("b", "play", cards=list(losing.hands["b"]))
    assert losing.finish_order == ["b"]
    action = game.suggest_action("a")
    assert action["action"] == "play"
    game.act("a", **action)
    assert game.last_pattern.kind == expected_kind
    with pytest.raises(ValueError):
        game.act("b", "play", cards=list(game.hands["b"]))


def test_next_opponent_already_passed_keeps_teammate_wind():
    game = game_with(cards("A", "4"))
    game.hands["b"] = cards("K")
    game.hands["c"] = cards("6", "3")
    game.hands["d"] = cards("7", "8")
    game.turn_index = 2
    game.act("c", "play", cards=cards("6"))
    game.act("d", "pass")
    # 经过本轮其余玩家让过，下家再出现时已无新的接牌窗口。
    game.passed.add("b")
    assert game.suggest_action("a") == {"action": "pass"}


def test_visible_cards_eliminate_only_publicly_impossible_finishing_replies():
    game = game_with(cards("7", "4"))
    game.hands["b"] = cards("K")
    game.hands["c"] = cards("6", "3")
    game.hands["d"] = cards("8", "9")
    game.turn_index = 2
    game.act("c", "play", cards=cards("6"))
    game.act("d", "pass")
    # 所有7已经可见时，不能再把压住未知7算成有效阻击。
    visible = list(game.hands["a"]) + list(game.last_play["cards"])
    visible += [card for card in m.DECK if m.card_parts(card)[1] == 7 and card not in visible]
    threats = m.guandan_finishing_threats(visible, game.level, game.last_pattern, 1)
    seven = m.classify_guandan_cards(cards("7"), game.level)[0]
    assert threats and all(m.guandan_beats(threat, seven) for threat in threats)


def test_emergency_team_cover_reads_no_opponent_hidden_cards():
    class HiddenHand:
        def __init__(self, count):
            self.count = count

        def __len__(self):
            return self.count

        def __iter__(self):
            raise AssertionError("阻击也不能读取敌手或队友暗牌")

    game = game_with(cards("A", "4"))
    game.hands["b"] = cards("K")
    game.hands["c"] = cards("6", "3")
    game.hands["d"] = cards("7", "8")
    game.turn_index = 2
    game.act("c", "play", cards=cards("6"))
    game.act("d", "pass")
    expected = game.suggest_action("a")
    for uid in ("b", "c", "d"):
        game.hands[uid] = HiddenHand(len(game.hands[uid]))
    assert game.suggest_action("a") == expected


@pytest.mark.parametrize("kind,size", [("triple", 3), ("full_house", 5), ("straight", 5),
                                       ("pair_straight", 6), ("triple_straight", 6)])
def test_public_finishing_risk_covers_all_supported_non_single_shapes(kind, size):
    previous = m.GuandanPattern(kind, 8, size)
    threats = m.guandan_finishing_threats([], "2", previous, size)
    assert any(pattern.kind == kind and pattern.rank > 8 for pattern in threats)


def test_public_finishing_risk_keeps_four_card_bomb_possibility():
    previous = m.GuandanPattern("triple", 8, 3)
    threats = m.guandan_finishing_threats([], "2", previous, 4)
    assert any(pattern.kind == "bomb" for pattern in threats)
    assert any(pattern.kind == "rocket" for pattern in threats)


def test_two_pure_wildcards_only_form_level_pair_in_finishing_threats():
    level = "8"
    unseen = ["Heart8#0", "Heart8#1", "Spade8#1", "Club3#1", "Club7#0",
              "Heart9#0", "Diamond2#1", "ClubQ#1", "SpadeA#0"]
    previous = m.GuandanPattern("pair", 8, 2)
    actual = set()
    for pair in combinations(unseen, 2):
        try:
            patterns = m.classify_guandan_cards(list(pair), level)
        except ValueError:
            continue
        actual.update(pattern for pattern in patterns if m.guandan_beats(pattern, previous))
    visible = [card for card in m.DECK if card not in unseen]
    predicted = set(m.guandan_finishing_threats(visible, level, previous, 2))
    assert predicted == actual
    assert {pattern.rank for pattern in predicted} == {9, 12, 14, 15}
    assert m.classify_guandan_cards(["Heart8#0", "Heart8#1"], level) == (
        m.GuandanPattern("pair", 15, 2),)


@pytest.mark.parametrize("hand,kind", [
    (("3", "4", "5", "6", "7", "Q", "Q"), "straight_flush"),
    (("3", "3", "3", "4", "4", "4", "Q", "Q"), "triple_straight"),
    (("3", "3", "4", "4", "5", "5", "Q", "Q"), "pair_straight"),
])
def test_lead_prefers_complete_groups_over_splitting_them_into_singles(hand, kind):
    game = game_with(cards(*hand))
    action = game.suggest_action("a")
    # 可以先走另一组对子，但不应从完整连牌中盲目拆出单张。
    pattern = m.classify_guandan_cards(action["cards"], game.level)[0]
    assert pattern.kind in (kind, "pair")
    assert len(action["cards"]) >= 2
    assert_legal(game, action)


@pytest.mark.parametrize("seed", [0, 2, 4, 7, 21, 42])
def test_bounded_public_options_keep_shapes_beyond_the_old_first_eighty(seed):
    game = m.GuandanGame(["a", "b", "c", "d"], seed=seed)
    uid = game.current_player_id
    complete = list(game._candidates(uid))
    options = game.public_state(uid)["play_options"]
    assert len(options) <= 80
    assert {(p.kind, p.size) for _, p in complete} == {(o["kind"], o["size"]) for o in options}
    for kind, size in {(p.kind, p.size) for _, p in complete}:
        ranks = [p.rank for _, p in complete if (p.kind, p.size) == (kind, size)]
        offered = [o["rank"] for o in options if (o["kind"], o["size"]) == (kind, size)]
        assert min(offered) == min(ranks) and max(offered) == max(ranks)
    for option in options:
        deepcopy(game).act(uid, "play", cards=option["cards"], combo=option["combo"])


def test_hand_analysis_reports_bomb_loss_and_wildcard_use_without_mutating_hand():
    hand = cards("J", "J", "J", "J", "5") + ["Heart2#0"]
    before = list(hand)
    result = m.guandan_hand_analysis(hand, "2", ["ClubJ#0", "Heart2#0"])
    assert result["breaks_natural_bombs"] == [{"rank": 11, "before": 4, "used": 1, "remaining": 3}]
    assert result["wildcards_used"] == 1 and result["remaining_count"] == 4
    assert hand == before


def test_hidden_cards_never_affect_suggestions_or_candidate_lists():
    class HiddenHand:
        def __len__(self):
            return 7

        def __iter__(self):
            raise AssertionError("不能读取其他座位暗牌")

    game = game_with(cards("J", "J", "J", "J", "4", "4", "8"))
    expected = game.suggest_action("a")
    options = game.public_state("a")["play_options"]
    for uid in ("b", "c", "d"):
        game.hands[uid] = HiddenHand()
    assert game.suggest_action("a") == expected
    assert game.public_state("a")["play_options"] == options


def test_complete_games_terminate_with_legal_actions_and_bounded_runtime():
    started = time.monotonic()
    for seed in range(8):
        game = m.GuandanGame(["a", "b", "c", "d"], seed=seed)
        for _ in range(700):
            if game.finished:
                break
            uid = game.current_player_id
            action = game.suggest_action(uid)
            assert action["action"] != "pass" or game.last_play is not None
            game.act(uid, **action)
        assert game.finished, seed
    assert time.monotonic() - started < 15
