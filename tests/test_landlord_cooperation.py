"""农民残局协作：保留队友领出权，仅在真实末牌窗口接管。"""

from copy import deepcopy
import importlib

import pytest


games = importlib.import_module("src.chat.features.games.blackjack-web.traditional_games")


def teammate_lead(own_hand, teammate_cards, lead, *, own="b", landlord_hand=None):
    game = games.LandlordGame(["a", "b", "c"], seed=17)
    game.act("a", "bid", bid=3)
    teammate = "c" if own == "b" else "b"
    game.hands = {
        "a": list(landlord_hand or ["Club8", "Heart9", "Spade10", "DiamondJ"]),
        own: list(own_hand),
        teammate: list(teammate_cards) + list(lead),
    }
    game.bottom_cards = []
    game.turn_index = game.player_ids.index(teammate)
    game.act(teammate, "play", cards=list(lead))
    if game.current_player_id == "a":
        game.act("a", "pass")
    assert game.current_player_id == own
    return game


def team_context(game, own):
    return games.landlord_team_context(game.public_state(own), own)


@pytest.mark.parametrize("remaining", [["Club3"], ["Club3", "Heart3"]])
def test_pass_returns_lead_to_short_teammate_after_landlord_already_passed(remaining):
    game = teammate_lead(["Spade3", "SpadeJ", "SpadeA"], remaining, ["Spade4"],
                         landlord_hand=["Spade5"])
    facts = team_context(game, "b")
    assert facts["teammate_remaining_count"] == len(remaining)
    assert facts["teammate_close_to_finish"]
    assert facts["landlord_already_passed_current_play"]
    assert facts["pass_returns_lead_to_teammate"]
    assert not facts["must_block_landlord_finish"]
    assert game.suggest_action("b") == {"action": "pass"}
    game.act("b", **game.suggest_action("b"))
    assert game.current_player_id == "c" and game.last_play is None
    game.act("c", "play", cards=remaining)
    assert game.finished and game.winners == ["b", "c"]


def test_teammate_ace_needs_cover_when_next_landlord_can_finish_with_two():
    game = teammate_lead(["Spade3", "Spade2", "JokerBig"], ["Club3"], ["SpadeA"],
                         own="c", landlord_hand=["Club2"])
    assert team_context(game, "c")["must_block_landlord_finish"]
    assert game.suggest_action("c") == {"action": "play", "cards": ["JokerBig"]}
    game.act("c", **game.suggest_action("c"))


def test_teammate_pair_aces_needs_cover_when_next_landlord_has_last_pair():
    game = teammate_lead(["Spade3", "Spade2", "Club2"], ["Club3"], ["SpadeA", "HeartA"],
                         own="c", landlord_hand=["Heart2", "Diamond2"])
    facts = team_context(game, "c")
    assert facts["landlord_possible_finishing_reply_ranks"] == [15]
    action = game.suggest_action("c")
    assert games.classify_landlord_cards(action["cards"]) == games.LandlordPattern("pair", 15, 2)
    game.act("c", **action)


def test_does_not_cover_teammate_two_when_both_jokers_are_known_in_own_hand():
    game = teammate_lead(["Spade3", "JokerSmall", "JokerBig"], ["Club3"], ["Spade2"],
                         own="c", landlord_hand=["ClubA"])
    facts = team_context(game, "c")
    assert not facts["must_block_landlord_finish"]
    assert facts["landlord_possible_finishing_reply_ranks"] == []
    assert game.suggest_action("c") == {"action": "pass"}


def test_does_not_take_over_when_only_possible_landlord_card_beats_own_reply():
    game = teammate_lead(["Spade3", "Spade2", "Club2", "Heart2", "Diamond2", "JokerSmall"],
                         ["Club3"], ["SpadeA"], own="c", landlord_hand=["JokerBig"])
    facts = team_context(game, "c")
    assert facts["landlord_possible_finishing_reply_ranks"] == [17]
    action = game.suggest_action("c")
    # 小王对唯一可能的末张大王没有保护作用；完整炸弹确实能挡住。
    assert games.classify_landlord_cards(action["cards"]).kind == "bomb"
    game.act("c", **action)


def test_unhelpful_single_does_not_block_short_teammate_when_no_real_cover_exists():
    game = teammate_lead(["Spade3", "JokerSmall"], ["Club3"], ["Spade2"],
                         own="c", landlord_hand=["JokerBig"])
    facts = team_context(game, "c")
    assert facts["landlord_possible_finishing_reply_ranks"] == [17]
    assert game.suggest_action("c") == {"action": "pass"}


def test_old_landlord_pass_does_not_hide_new_teammate_play_threat():
    game = teammate_lead(["Spade3", "Spade2", "JokerBig"], ["Club3"], ["SpadeA"],
                         own="c", landlord_hand=["Club2"])
    # seat_actions 可保留地主对更早一手的过牌，本次队友刚压牌后尚未轮到地主。
    game.seat_actions["a"] = {"action": "pass", "cards": [], "label": "不出"}
    facts = team_context(game, "c")
    assert not facts["landlord_already_passed_current_play"]
    assert facts["must_block_landlord_finish"]
    assert game.suggest_action("c")["action"] == "play"


def test_direct_team_win_still_overrides_yielding_to_teammate():
    game = teammate_lead(["SpadeA"], ["Club3", "Heart3"], ["Spade4"],
                         landlord_hand=["Spade5"])
    action = game.suggest_action("b")
    assert action == {"action": "play", "cards": ["SpadeA"]}
    game.act("b", **action)
    assert game.finished and game.winners == ["b", "c"]


def test_teammate_closing_does_not_trigger_cover_without_matching_enemy_size():
    game = teammate_lead(["Spade3", "Spade2", "JokerBig"], ["Club3"], ["Spade4"],
                         own="c", landlord_hand=["Club2", "Heart2"])
    assert not team_context(game, "c")["must_block_landlord_finish"]
    assert game.suggest_action("c") == {"action": "pass"}


def rank_cards(rank, count, suits=games.POKER_SUITS):
    return [f"{suit}{rank}" for suit in suits[:count]]


@pytest.mark.parametrize("lead,cover,enemy,kind", [
    (rank_cards("6", 3), rank_cards("A", 3), rank_cards("K", 3), "triple"),
    (rank_cards("6", 3) + ["Club7"], rank_cards("A", 3) + ["Club8"],
     rank_cards("K", 3) + ["Club9"], "triple_single"),
    (rank_cards("6", 3) + rank_cards("7", 2), rank_cards("A", 3) + rank_cards("8", 2),
     rank_cards("K", 3) + rank_cards("9", 2), "triple_pair"),
    ([f"Club{rank}" for rank in (3, 4, 5, 6, 7)], [f"Heart{rank}" for rank in (8, 9, 10, "J", "Q")],
     [f"Diamond{rank}" for rank in (4, 5, 6, 7, 8)], "straight"),
    (sum((rank_cards(rank, 2) for rank in (3, 4, 5)), []),
     sum((rank_cards(rank, 2) for rank in ("J", "Q", "K")), []),
     sum((rank_cards(rank, 2) for rank in (8, 9, 10)), []), "pair_straight"),
    (rank_cards(3, 3) + rank_cards(4, 3), rank_cards("K", 3) + rank_cards("A", 3),
     rank_cards("J", 3) + rank_cards("Q", 3), "airplane"),
    (rank_cards(3, 3) + rank_cards(4, 3) + ["Club5", "Club6"],
     rank_cards("K", 3) + rank_cards("A", 3) + ["Club8", "Club9"],
     rank_cards("J", 3) + rank_cards("Q", 3) + ["Diamond9", "Club10"], "airplane_single"),
    (rank_cards(3, 3) + rank_cards(4, 3) + rank_cards(5, 2) + rank_cards(6, 2),
     rank_cards("K", 3) + rank_cards("A", 3) + rank_cards(8, 2) + rank_cards(9, 2),
     rank_cards("J", 3) + rank_cards("Q", 3) + rank_cards(7, 2) + rank_cards(10, 2), "airplane_pair"),
    (rank_cards(3, 4) + ["Club5", "Club6"], rank_cards("A", 4) + ["Club8", "Club9"],
     rank_cards("K", 4) + ["ClubJ", "ClubQ"], "four_two_single"),
    (rank_cards(3, 4) + rank_cards(5, 2) + rank_cards(6, 2),
     rank_cards("A", 4) + rank_cards(8, 2) + rank_cards(9, 2),
     rank_cards("K", 4) + rank_cards("J", 2) + rank_cards("Q", 2), "four_two_pair"),
    (rank_cards(6, 4), rank_cards("A", 4), rank_cards("K", 4), "bomb"),
])
def test_complex_finishing_shapes_keep_emergency_cover(lead, cover, enemy, kind):
    game = teammate_lead(cover + ["Spade2"], ["Spade5"], lead, own="c", landlord_hand=enemy)
    facts = team_context(game, "c")
    expected = games.classify_landlord_cards(enemy)
    assert facts["must_block_landlord_finish"]
    assert expected.to_dict() in facts["landlord_possible_finishing_patterns"]
    action = game.suggest_action("c")
    assert action["action"] == "play"
    played = games.classify_landlord_cards(action["cards"])
    assert played.kind in (kind, "bomb", "rocket")
    assert not games.landlord_beats(expected, played)
    game.act("c", **action)


def test_last_four_card_bomb_needs_bomb_cover_even_on_single_lead():
    game = teammate_lead(rank_cards("A", 4) + ["Club4"], ["Club3"], ["Club6"],
                         own="c", landlord_hand=rank_cards("K", 4))
    facts = team_context(game, "c")
    assert facts["must_block_landlord_finish"]
    assert facts["landlord_possible_finishing_reply_ranks"] == []
    assert not games.landlord_blocks_finishing_reply(games.LandlordPattern("single", 14, 1), facts)
    action = game.suggest_action("c")
    assert games.classify_landlord_cards(action["cards"]) == games.LandlordPattern("bomb", 14, 4)
    game.act("c", **action)


def test_known_cards_can_rule_out_every_last_four_card_bomb():
    # 自己每个点数至少一张，地主未知的四张就不可能是自然炸弹。
    game = teammate_lead([f"Club{rank}" for rank in games.POKER_RANKS], ["Heart3"], ["Heart6"],
                         own="c", landlord_hand=["Heart7", "Heart8", "Heart9", "Heart10"])
    facts = team_context(game, "c")
    assert not facts["must_block_landlord_finish"]
    assert game.suggest_action("c") == {"action": "pass"}


def test_possible_last_rocket_is_not_assumed_safe_but_cannot_be_stopped():
    game = teammate_lead(rank_cards("A", 4) + ["Club4"], ["Club3"], ["Club6"],
                         own="c", landlord_hand=["JokerSmall", "JokerBig"])
    facts = team_context(game, "c")
    assert facts["must_block_landlord_finish"]
    assert facts["landlord_possible_finishing_patterns"] == [games.LandlordPattern("rocket", 17, 2).to_dict()]
    assert not games.landlord_blocks_finishing_reply(games.LandlordPattern("bomb", 14, 4), facts)
    assert game.suggest_action("c") == {"action": "pass"}


@pytest.mark.parametrize("lead,cover,enemy", [
    (rank_cards("6", 3), rank_cards("A", 3), rank_cards("K", 3)),
    (["Club6"], rank_cards("A", 4), rank_cards("K", 4)),
])
def test_landlord_already_passed_returns_lead_even_with_complex_potential_finish(lead, cover, enemy):
    game = teammate_lead(cover + ["Club4"], ["Club3"], lead, landlord_hand=enemy)
    facts = team_context(game, "b")
    assert facts["pass_returns_lead_to_teammate"]
    assert not facts["must_block_landlord_finish"]
    assert facts["landlord_possible_finishing_patterns"] == []
    assert game.suggest_action("b") == {"action": "pass"}


class HiddenHand:
    """允许公开牌数，禁止任何暗牌内容访问。"""

    def __init__(self, count):
        self.count = count

    def __len__(self):
        return self.count

    def __iter__(self):
        raise AssertionError("策略读取了其他座位暗牌")


def test_cooperation_uses_no_hidden_cards_and_does_not_mutate_state():
    game = teammate_lead(["Spade3", "Spade2", "JokerBig"], ["Club3"], ["SpadeA"],
                         own="c", landlord_hand=["Club2"])
    expected = game.suggest_action("c")
    public = game.public_state("c")
    saved = deepcopy(public)
    before = team_context(game, "c")
    assert public == saved
    game.hands["a"] = HiddenHand(1)
    game.hands["b"] = HiddenHand(1)
    assert team_context(game, "c") == before
    assert game.suggest_action("c") == expected
    assert games.landlord_team_context(public, "c") == before
    assert public == saved
