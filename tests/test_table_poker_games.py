"""验证扑克牌力、边池、行动权、暗牌隔离及完整自动牌局。"""

import copy
import importlib
import random

import pytest


poker = importlib.import_module("src.chat.features.games.blackjack-web.poker_games")
TexasHoldemGame = poker.TexasHoldemGame
GoldenFlowerGame = poker.GoldenFlowerGame


@pytest.mark.parametrize(
    ("cards", "expected"),
    [
        (["HeartA", "HeartK", "HeartQ", "HeartJ", "Heart10"], (8, 14)),
        (["ClubA", "Diamond2", "Heart3", "Spade4", "Club5"], (4, 5)),
        (["ClubK", "DiamondK", "HeartK", "SpadeK", "ClubA"], (7, 13, 14)),
        (["Club2", "Diamond2", "Heart2", "SpadeA", "ClubA"], (6, 2, 14)),
        (["Club2", "Club4", "Club6", "Club8", "ClubA"], (5, 14, 8, 6, 4, 2)),
        (["ClubA", "DiamondA", "HeartK", "SpadeK", "Club2"], (2, 14, 13, 2)),
    ],
)
def test_texas_five_card_ranking(cards, expected):
    assert poker.evaluate_five(cards) == expected


def test_texas_chooses_best_five_of_seven_and_ignores_suits_on_ties():
    cards = ["ClubA", "DiamondA", "HeartA", "SpadeK", "ClubK", "DiamondK", "Heart2"]
    assert poker.evaluate_best_hand(cards) == (6, 14, 13)
    assert poker.evaluate_five(["ClubA", "DiamondK", "HeartQ", "SpadeJ", "Club10"]) == poker.evaluate_five(["DiamondA", "HeartK", "SpadeQ", "ClubJ", "Diamond10"])


@pytest.mark.parametrize(
    ("cards", "category"),
    [
        (["ClubA", "DiamondA", "HeartA"], 5),
        (["HeartA", "Heart2", "Heart3"], 4),
        (["Club2", "Club5", "Club9"], 3),
        (["ClubA", "Diamond2", "Heart3"], 2),
        (["ClubK", "DiamondK", "Heart2"], 1),
        (["Club2", "Diamond3", "Heart5"], 0),
    ],
)
def test_golden_flower_hand_categories(cards, category):
    assert poker.evaluate_golden_hand(cards)[0] == category


def test_golden_flower_a23_is_lowest_straight_and_235_is_not_special():
    wheel = poker.evaluate_golden_hand(["ClubA", "Diamond2", "Heart3"])
    higher = poker.evaluate_golden_hand(["Club2", "Diamond3", "Heart4"])
    assert wheel == (2, 3)
    assert higher > wheel
    assert poker.evaluate_golden_hand(["Club2", "Diamond3", "Heart5"]) < poker.evaluate_golden_hand(["Club2", "Diamond2", "Heart2"])


def test_texas_heads_up_blinds_and_street_action_order():
    game = TexasHoldemGame(["a", "b"], seed=7)
    assert game.current_player_id == "a"
    assert [player.stack for player in game.players] == [99, 98]
    assert game.pot == 3
    game.act("a", "call")
    assert game.current_player_id == "b"
    game.act("b", "check")
    assert game.phase == "flop"
    assert game.current_player_id == "b"
    assert len(game.community_cards) == 3
    assert game.current_bet == 0
    assert game.pot == 4


def test_texas_three_players_big_blind_retains_option_after_calls():
    game = TexasHoldemGame(["a", "b", "c"], seed=8)
    assert game.current_player_id == "a"
    game.act("a", "call")
    game.act("b", "call")
    assert game.phase == "preflop"
    assert game.current_player_id == "c"
    assert "raise" in game.public_state("c")["legal_actions"]
    game.act("c", "check")
    assert game.phase == "flop"
    assert game.current_player_id == "b"


@pytest.mark.parametrize("action,payload", [("check", {}), ("raise", {"amount": 3}), ("raise", {"amount": 200}), ("raise", {"amount": True}), ("raise", {"amount": 4.5})])
def test_texas_invalid_actions_do_not_change_state(action, payload):
    game = TexasHoldemGame(["a", "b", "c"], seed=8)
    before = copy.deepcopy(game.public_state("a"))
    with pytest.raises(ValueError):
        game.act("a", action, **payload)
    assert game.public_state("a") == before


def test_texas_wrong_turn_does_not_change_state():
    game = TexasHoldemGame(["a", "b", "c"], seed=8)
    before = copy.deepcopy(game.public_state("a"))
    with pytest.raises(ValueError):
        game.act("b", "call")
    assert game.public_state("a") == before


def test_texas_short_all_in_requires_call_but_does_not_reopen_raise():
    game = TexasHoldemGame(["a", "b", "c"], seed=9)
    game.players[1].stack = 2  # 小盲已投 1，本手最多投入 3。
    game.act("a", "call")
    game.act("b", "all_in")
    assert game.current_bet == 3
    assert game.min_raise == 2
    game.act("c", "call")
    assert game.current_player_id == "a"
    assert game.public_state("a")["legal_actions"] == ["fold", "call"]
    before = game.public_state("a")
    with pytest.raises(ValueError):
        game.act("a", "raise", amount=6)
    assert game.public_state("a") == before
    game.act("a", "call")
    assert game.phase == "flop"


def test_texas_multiple_short_all_ins_can_cumulatively_reopen_action():
    game = TexasHoldemGame(["a", "b", "c", "d"], seed=9)
    # 首位 d 跟注 2，a 全下至 3，b 再全下至 4，累计涨幅达到完整加注 2。
    game.players[0].stack = 3
    game.players[1].stack = 3
    game.act("d", "call")
    game.act("a", "all_in")
    game.act("b", "all_in")
    game.act("c", "call")
    assert game.current_player_id == "d"
    assert "raise" in game.public_state("d")["legal_actions"]
    game.act("d", "raise", amount=6)
    assert game.current_bet == 6


def test_texas_short_opening_all_in_does_not_reopen_previous_checker():
    game = TexasHoldemGame(["a", "b", "c"], seed=9)
    game.act("a", "call")
    game.act("b", "call")
    game.act("c", "check")
    assert game.phase == "flop"
    game.act("b", "check")
    game.players[2].stack = 1
    game.act("c", "all_in")
    assert game.current_bet == 1
    assert game.public_state("a")["min_raise_to"] == 3
    game.act("a", "call")
    assert "raise" not in game.public_state("b")["legal_actions"]
    game.act("b", "call")
    assert game.phase == "turn"


def test_texas_all_in_deals_board_and_conserves_chips():
    game = TexasHoldemGame(["a", "b", "c"], seed=123)
    game.act("a", "all_in")
    game.act("b", "call")
    game.act("c", "call")
    assert game.finished
    assert len(game.community_cards) == 5
    assert game.current_player_id is None
    assert game.pot == 300
    assert sum(player.stack for player in game.players) == 300
    assert sum(player.payout for player in game.players) == 300


def test_texas_side_pots_and_unmatched_all_in_are_settled_separately():
    game = TexasHoldemGame(["a", "b", "c"], seed=2)
    game.players[0].hand = ["ClubA", "DiamondA"]
    game.players[1].hand = ["ClubK", "DiamondK"]
    game.players[2].hand = ["ClubQ", "DiamondQ"]
    game.community_cards = ["Heart2", "Spade4", "Heart6", "Spade8", "Heart10"]
    for player, amount in zip(game.players, [10, 30, 50]):
        player.total_bet = amount
        player.stack = 0
    game._showdown()
    assert [player.stack for player in game.players] == [30, 40, 20]
    assert game.winners == ["a", "b"]
    assert game.side_pots == [
        {"amount": 30, "winners": ["a"]},
        {"amount": 40, "winners": ["b"]},
        {"amount": 20, "winners": [], "refund_to": "c"},
    ]


def test_texas_odd_split_chip_goes_clockwise_after_dealer():
    game = TexasHoldemGame(["a", "b", "c"], seed=2)
    game.community_cards = ["Heart10", "HeartJ", "HeartQ", "HeartK", "HeartA"]
    for player in game.players:
        player.total_bet = 5
        player.stack = 0
    game.players[0].hand = ["Club2", "Diamond2"]
    game.players[1].hand = ["Club3", "Diamond3"]
    game.players[2].folded = True
    game._showdown()
    assert [player.stack for player in game.players] == [7, 8, 0]


def test_texas_folded_players_cannot_win_and_do_not_reveal_cards():
    game = TexasHoldemGame(["a", "b"], seed=4)
    game.act("a", "fold")
    assert game.finished
    assert game.winners == ["b"]
    assert [player.stack for player in game.players] == [99, 101]
    assert game.public_state("b")["players"][0]["hand"] == []


def test_texas_hole_cards_are_private_and_returned_as_copies():
    game = TexasHoldemGame(["a", "b"], seed=4)
    state = game.public_state("a")
    assert len(state["players"][0]["hand"]) == 2
    assert state["players"][1]["hand"] == []
    assert state["players"][1]["hand_count"] == 2
    state["players"][0]["hand"].clear()
    assert len(game.players[0].hand) == 2


def test_golden_look_is_private_and_does_not_consume_turn():
    game = GoldenFlowerGame(["a", "b"], seed=4)
    assert game.public_state("a")["players"][0]["hand"] == []
    game.act("a", "look")
    assert game.current_player_id == "a"
    assert game.action_count == 0
    assert len(game.public_state("a")["players"][0]["hand"]) == 3
    assert game.public_state("b")["players"][0]["hand"] == []
    assert game.public_state("a")["call_amount"] == 2
    game.act("a", "call")
    assert game.players[0].stack == 97
    assert game.current_player_id == "b"


def test_golden_raise_charges_seen_multiplier_and_changes_base():
    game = GoldenFlowerGame(["a", "b"], seed=4)
    game.act("a", "look")
    game.act("a", "raise", amount=2)
    assert game.players[0].stack == 95
    assert game.public_state("b")["call_amount"] == 2
    game.act("b", "look")
    assert game.public_state("b")["call_amount"] == 4


def test_golden_compare_equal_hands_eliminates_initiator():
    game = GoldenFlowerGame(["a", "b"], seed=4)
    game.players[0].hand = ["ClubA", "DiamondK", "HeartJ"]
    game.players[1].hand = ["DiamondA", "HeartK", "SpadeJ"]
    game.act("a", "compare", target_id="b")
    assert game.finished
    assert game.players[0].folded
    assert game.winners == ["b"]
    assert game.players[0].stack == 97
    assert game.players[1].stack == 103


@pytest.mark.parametrize("action,payload", [("compare", {"target_id": "a"}), ("compare", {"target_id": "missing"}), ("raise", {"amount": 1}), ("raise", {"amount": 200}), ("raise", {"amount": True})])
def test_golden_invalid_actions_leave_state_unchanged(action, payload):
    game = GoldenFlowerGame(["a", "b"], seed=4)
    before = copy.deepcopy(game.public_state("a"))
    with pytest.raises(ValueError):
        game.act("a", action, **payload)
    assert game.public_state("a") == before


def test_golden_action_limit_forces_showdown_and_split():
    game = GoldenFlowerGame(["a", "b"], seed=4)
    game.players[0].hand = ["ClubA", "DiamondK", "HeartJ"]
    game.players[1].hand = ["DiamondA", "HeartK", "SpadeJ"]
    while not game.finished:
        game.act(game.current_player_id, "call")
    assert game.action_count == 40
    assert game.winners == ["a", "b"]
    assert [player.stack for player in game.players] == [100, 100]


@pytest.mark.parametrize("game_class,count", [(TexasHoldemGame, 2), (TexasHoldemGame, 6), (TexasHoldemGame, 8), (GoldenFlowerGame, 2), (GoldenFlowerGame, 5)])
def test_ai_completes_games_with_valid_actions_and_conserves_chips(game_class, count):
    for seed in range(30):
        game = game_class([str(index) for index in range(count)], seed=seed)
        for _ in range(500):
            if game.finished:
                break
            user_id = game.current_player_id
            suggestion = game.suggest_action(user_id)
            assert suggestion["action"] in game.public_state(user_id)["legal_actions"]
            game.act(user_id, **suggestion)
            assert all(player.stack >= 0 for player in game.players)
        assert game.finished, (game_class.__name__, seed)
        assert game.winners
        assert sum(player.stack for player in game.players) == 100 * count


@pytest.mark.parametrize("game_class,count", [(TexasHoldemGame, 2), (GoldenFlowerGame, 2)])
def test_ai_choices_do_not_depend_on_opponent_hidden_cards(game_class, count):
    game = game_class([str(index) for index in range(count)], seed=42)
    if game_class is GoldenFlowerGame:
        game.act(game.current_player_id, "look")
    cloned = copy.deepcopy(game)
    opponent = next(player for player in cloned.players if player.user_id != cloned.current_player_id)
    opponent.hand = ["ClubA", "DiamondA"] if game_class is TexasHoldemGame else ["ClubA", "DiamondA", "HeartA"]
    assert game.suggest_action(game.current_player_id) == cloned.suggest_action(cloned.current_player_id)


@pytest.mark.parametrize("game_class,count", [(TexasHoldemGame, 6), (GoldenFlowerGame, 5)])
def test_random_legal_actions_finish_without_chip_loss(game_class, count):
    for seed in range(100):
        rng = random.Random(seed)
        game = game_class([str(index) for index in range(count)], seed=seed)
        for _ in range(500):
            if game.finished:
                break
            uid = game.current_player_id
            state = game.public_state(uid)
            action = rng.choice(state["legal_actions"])
            payload = {}
            if action == "raise":
                payload["amount"] = rng.randint(state["min_raise_to"], state["max_raise_to"])
            elif action == "compare":
                payload["target_id"] = rng.choice(state["compare_targets"])
            game.act(uid, action, **payload)
            assert all(player.stack >= 0 for player in game.players)
            if not game.finished:
                assert sum(player.stack for player in game.players) + game.pot == 100 * count
        assert game.finished, (game_class.__name__, seed)
        assert sum(player.stack for player in game.players) == 100 * count
        assert sum(game.public_state("0")["settlement"].values()) == 0


@pytest.mark.parametrize("game_class", [TexasHoldemGame, GoldenFlowerGame])
@pytest.mark.parametrize("buy_in", [100, 250, 20000, 123456789])
def test_custom_buy_in_sets_stacks_and_uses_exact_settlement_baseline(game_class, buy_in):
    game = game_class(["a", "b"], seed=1, buy_in=buy_in)
    assert game.public_state("a")["buy_in"] == buy_in
    assert sum(player.stack for player in game.players) + game.pot == buy_in * 2
    game.act("a", "fold")
    state = game.public_state("a")
    assert game.finished
    assert state["settlement"] == {"a": -1, "b": 1}
    assert [player["score_delta"] for player in state["players"]] == [-1, 1]
    assert sum(player.stack for player in game.players) == buy_in * 2


@pytest.mark.parametrize("game_class", [TexasHoldemGame, GoldenFlowerGame])
@pytest.mark.parametrize("buy_in", [0, 99, -1, True, 100.5, "100"])
def test_invalid_buy_in_is_rejected(game_class, buy_in):
    with pytest.raises(ValueError):
        game_class(["a", "b"], buy_in=buy_in)


def test_large_texas_buy_in_can_be_raised_without_fixed_game_limit():
    game = TexasHoldemGame(["a", "b"], seed=1, buy_in=20000)
    game.act("a", "raise", amount=15000)
    assert game.players[0].round_bet == 15000
    assert game.players[0].stack == 5000
    game.act("b", "all_in")
    game.act("a", "call")
    assert game.finished
    assert sum(player.stack for player in game.players) == 40000
    assert sum(game.public_state("a")["settlement"].values()) == 0


def test_large_golden_buy_in_can_be_raised_without_fixed_game_limit():
    game = GoldenFlowerGame(["a", "b"], seed=1, buy_in=20000)
    game.act("a", "raise", amount=15000)
    assert game.base_bet == 15000
    assert game.players[0].stack == 4999
    game.act("b", "fold")
    assert game.finished
    assert game.public_state("a")["settlement"] == {"a": 1, "b": -1}


def test_eight_player_texas_preserves_all_52_cards_through_each_street():
    game = TexasHoldemGame([str(index) for index in range(8)], seed=9)
    assert len(game.players) == 8
    expected = set(poker.create_deck())
    observed_phases = set()
    for _ in range(50):
        observed_phases.add(game.phase)
        cards = game.deck + game.community_cards + game.burned_cards
        cards += [card for player in game.players for card in player.hand]
        assert len(cards) == 52
        assert set(cards) == expected
        assert len(game.burned_cards) <= 3
        if game.finished:
            break
        uid = game.current_player_id
        actions = game.public_state(uid)["legal_actions"]
        game.act(uid, "check" if "check" in actions else "call")
    assert game.finished
    assert observed_phases == {"preflop", "flop", "turn", "river", "finished"}
    assert len(game.community_cards) == 5
    assert len(game.burned_cards) == 3
    assert len(game.deck) == 28
    assert sum(player.stack for player in game.players) == 800


def test_texas_rejects_ninth_player():
    with pytest.raises(ValueError, match="2 至 8"):
        TexasHoldemGame([str(index) for index in range(9)])
