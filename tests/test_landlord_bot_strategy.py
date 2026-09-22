"""斗地主陪玩协作、残局、拆牌代价与隐藏信息隔离回归。"""

from collections import Counter
from copy import deepcopy
import importlib
import time

import pytest


games = importlib.import_module("src.chat.features.games.blackjack-web.traditional_games")


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


def scenario(hand, *, player="b", previous=("4",), leader="a", enemy_count=8, teammate_count=7):
    game = games.LandlordGame(["a", "b", "c"], seed=17)
    game.act("a", "bid", bid=3)
    game.hands = {"a": cards("5", "6", "7", "8", "9", "10", "J", "Q")[:enemy_count],
                  "b": list(hand), "c": cards("3", "4", "5", "6", "7", "8", "9")[:teammate_count]}
    if player != "b":
        game.hands[player], game.hands["b"] = game.hands["b"], game.hands[player]
    game.turn_index = game.player_ids.index(player)
    if previous:
        played = cards(*previous)
        game.last_pattern = games.classify_landlord_cards(played)
        game.last_play = {"user_id": leader, "cards": played, **game.last_pattern.to_dict()}
    return game


def test_farmer_passes_teammate_without_spending_control_cards():
    game = scenario(cards("3", "3", "A", "2", "JokerBig"), leader="c")
    assert game.suggest_action("b") == {"action": "pass"}


def test_weak_hand_does_not_automatically_bid_for_landlord():
    game = games.LandlordGame(["a", "b", "c"], seed=17)
    game.hands["a"] = cards("3", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
    assert game.suggest_action("a") == {"action": "bid", "bid": 0}


def test_rocket_and_bomb_support_high_bid_without_reading_opponents():
    game = games.LandlordGame(["a", "b", "c"], seed=17)
    game.hands["a"] = cards("3", "3", "3", "3", "JokerSmall", "JokerBig")
    game.hands["b"] = HiddenHand(17)
    game.hands["c"] = HiddenHand(17)
    assert game.suggest_action("a") == {"action": "bid", "bid": 3}


def test_finishing_takes_precedence_over_passing_teammate():
    game = scenario(cards("A", "A"), leader="c", previous=("4", "4"))
    action = game.suggest_action("b")
    game.act("b", **action)
    assert game.finished and game.winners == ["b", "c"]


@pytest.mark.parametrize(("previous", "hand", "enemy_count", "rank"), [
    (("4",), ("5", "A", "2", "7", "7"), 1, 15),
    (("4", "4"), ("5", "5", "A", "A", "7"), 2, 14),
])
def test_blocks_enemy_last_single_or_pair(previous, hand, enemy_count, rank):
    game = scenario(cards(*hand), previous=previous, enemy_count=enemy_count)
    action = game.suggest_action("b")
    assert action["action"] == "play"
    assert games.classify_landlord_cards(action["cards"]).rank == rank
    game.act("b", **action)


def test_farmer_can_cover_teammate_to_block_landlord_next_turn():
    game = scenario(cards("5", "A", "2", "7", "7"), player="c", leader="b", enemy_count=1)
    action = game.suggest_action("c")
    assert action["action"] == "play"
    assert games.classify_landlord_cards(action["cards"]).rank == 15


def test_preserves_bomb_instead_of_beating_unthreatening_single():
    game = scenario(cards("3", "3", "3", "3", "6", "7", "8", "9", "10", "A"), previous=("2",))
    assert game.suggest_action("b") == {"action": "pass"}


def test_bomb_is_used_to_block_enemy_about_to_finish():
    game = scenario(cards("3", "3", "3", "3", "6", "7", "8", "9", "10"), previous=("2",), enemy_count=1)
    action = game.suggest_action("b")
    assert games.classify_landlord_cards(action["cards"]).kind == "bomb"


def test_does_not_break_straight_to_answer_unthreatening_single():
    game = scenario(cards("3", "3", "4", "5", "6", "7"), previous=("6",))
    assert game.suggest_action("b") == {"action": "pass"}


def test_lead_preserves_pair_straight_instead_of_longest_straight():
    game = scenario(cards("3", "3", "4", "4", "5", "5", "6", "7"), previous=None)
    action = game.suggest_action("b")
    pattern = games.classify_landlord_cards(action["cards"])
    # 出连对后只剩 6、7 两张；长顺子会拆散三对，留下更多散牌。
    assert pattern.kind == "pair_straight"


def test_does_not_feed_single_when_enemy_has_one_card():
    game = scenario(cards("3", "3", "4", "7", "9"), previous=None, enemy_count=1)
    action = game.suggest_action("b")
    assert games.classify_landlord_cards(action["cards"]).kind == "pair"


class HiddenHand:
    """公开牌数可读，任何访问暗牌内容都应立即失败。"""

    def __init__(self, count):
        self.count = count

    def __len__(self):
        return self.count

    def __iter__(self):
        raise AssertionError("策略读取了对手暗牌")

    def __getitem__(self, index):
        raise AssertionError("策略读取了对手暗牌")


def test_strategy_only_reads_own_hand_and_public_counts():
    game = scenario(cards("3", "3", "4", "4", "5", "5", "A"), previous=None)
    expected = game.suggest_action("b")
    game.hands["a"] = HiddenHand(8)
    game.hands["c"] = HiddenHand(7)
    assert game.suggest_action("b") == expected


def test_suggestions_do_not_change_game_state_or_dealing_randomness():
    game = scenario(cards("3", "3", "4", "4", "5", "5", "A"), previous=None)
    before = deepcopy(game.__dict__)
    rng_before = game.random.getstate()
    first = game.suggest_action("b")
    assert game.suggest_action("b") == first
    assert game.random.getstate() == rng_before
    for key, value in before.items():
        if key != "random":
            assert game.__dict__[key] == value


@pytest.mark.parametrize("seed", range(12))
def test_strategy_completes_legal_games_with_bounded_runtime(seed):
    game = games.LandlordGame(["a", "b", "c"], seed=seed)
    started = time.monotonic()
    for _ in range(500):
        if game.finished:
            break
        player = game.current_player_id
        game.act(player, **game.suggest_action(player))
    assert game.finished
    assert sum(game.scores.values()) == 0
    assert time.monotonic() - started < 5
