"""陪玩策略回归：决策信息边界、风险控制、诈唬及采样牌力一致性。"""

import copy
import importlib
import random

import pytest


poker = importlib.import_module("src.chat.features.games.blackjack-web.poker_games")


def river_game(hand, board, *, bet=0):
    game = poker.TexasHoldemGame(["月月", "玩家"], seed=17)
    game.phase = "river"
    game.community_cards = board
    game.players[0].hand = hand
    game.current_bet = bet
    game.min_raise = max(2, bet)
    game._current_index = 0
    game._pending = {"月月"}
    for player in game.players:
        player.round_bet = 0
        player.total_bet = 10
        player.stack = 90
    game.players[1].round_bet = bet
    game.players[1].total_bet += bet
    game.players[1].stack -= bet
    return game


@pytest.mark.parametrize("count", [5, 6, 7])
def test_sampling_evaluator_matches_settlement_evaluator(count):
    rng = random.Random(817)
    for _ in range(350):
        cards = rng.sample(poker.create_deck(), count)
        assert poker._sampled_texas_rank([poker.card_parts(card) for card in cards]) == poker.evaluate_best_hand(cards)


@pytest.mark.parametrize("seed", range(8))
def test_texas_weak_pair_folds_to_large_bet_on_dangerous_board(seed):
    game = river_game(
        ["Spade7", "Diamond7"],
        ["HeartA", "HeartK", "HeartQ", "Heart10", "Club2"],
        bet=60,
    )
    game.strategy_rng.seed(seed)
    assert game.suggest_action("月月") == {"action": "fold"}


def test_texas_low_pocket_pair_does_not_call_preflop_shove():
    game = poker.TexasHoldemGame(["月月", "玩家"], seed=17)
    game.players[0].hand = ["Club2", "Diamond2"]
    game.players[1].stack = 0
    game.players[1].round_bet = game.players[1].total_bet = 100
    game.players[1].all_in = True
    game.current_bet = 100
    assert game.suggest_action("月月") == {"action": "fold"}


def test_texas_nuts_bets_for_value_without_automatically_shoving():
    game = river_game(
        ["HeartA", "HeartK"],
        ["HeartQ", "HeartJ", "Heart10", "Diamond2", "Club3"],
    )
    raises = []
    for seed in range(12):
        candidate = copy.deepcopy(game)
        candidate.strategy_rng.seed(seed)
        action = candidate.suggest_action("月月")
        if action["action"] == "raise":
            raises.append(action["amount"])
            candidate.act("月月", **action)
    assert len(raises) >= 6
    assert all(2 <= amount <= 20 for amount in raises)


def test_texas_bluffs_only_with_room_to_fold_and_small_sizing():
    game = river_game(
        ["Club2", "Diamond4"],
        ["HeartA", "SpadeK", "DiamondQ", "Heart9", "Club6"],
    )
    raises = 0
    for seed in range(96):
        candidate = copy.deepcopy(game)
        candidate.strategy_rng.seed(seed)
        action = candidate.suggest_action("月月")
        if action["action"] == "raise":
            raises += 1
            assert action["amount"] <= 10
    assert 1 <= raises <= 14
    game.players[1].all_in = True
    game.players[1].stack = 0
    assert game.suggest_action("月月") == {"action": "check"}


def test_texas_premium_pair_can_raise_in_eight_player_pot():
    game = poker.TexasHoldemGame([str(index) for index in range(8)], seed=81)
    hero = game._player(game.current_player_id)
    hero.hand = ["HeartA", "DiamondA"]
    suggestions = []
    for seed in range(8):
        candidate = copy.deepcopy(game)
        candidate.strategy_rng.seed(seed)
        suggestions.append(candidate.suggest_action(hero.user_id)["action"])
    assert suggestions.count("raise") >= 4


@pytest.mark.parametrize("count", [2, 8])
def test_texas_equity_uses_public_cards_and_number_of_opponents(count):
    game = poker.TexasHoldemGame([str(index) for index in range(count)], seed=81)
    hero = game._player(game.current_player_id)
    hero.hand = ["HeartA", "DiamondA"]
    opponents = [player for player in game.players if player is not hero]
    equity = game._estimated_equity(hero, opponents)
    assert 0.60 < equity < 0.98 if count == 2 else 0.15 < equity < 0.60


@pytest.mark.parametrize("game_class", [poker.TexasHoldemGame, poker.GoldenFlowerGame])
def test_decisions_ignore_hidden_hands_and_future_deck_and_do_not_consume_dealing_rng(game_class):
    game = game_class(["月月", "玩家", "其他玩家"], seed=62)
    hero = game._player(game.current_player_id)
    if game_class is poker.GoldenFlowerGame:
        hero.seen = True
    candidate = copy.deepcopy(game)
    for player in candidate.players:
        if player.user_id != hero.user_id:
            player.hand = ["不能读取隐藏手牌"]
    candidate.deck = ["不能读取未发牌"]
    before = game.rng.getstate()
    assert candidate.suggest_action(hero.user_id) == game.suggest_action(hero.user_id)
    assert game.rng.getstate() == before


def test_golden_unseen_decisions_do_not_read_own_cards():
    game = poker.GoldenFlowerGame(["月月", "玩家"], seed=3)
    candidate = copy.deepcopy(game)
    candidate.players[0].hand = ["没看牌不能读取"]
    assert candidate.suggest_action("月月") == game.suggest_action("月月")


def golden_bluff_game():
    game = poker.GoldenFlowerGame(["月月", "玩家"], seed=71)
    game.players[0].seen = True
    game.players[0].hand = ["Club2", "Diamond5", "Heart8"]
    game.action_count = 4
    return game


def test_golden_can_bluff_but_frequency_and_cost_are_limited():
    game = golden_bluff_game()
    raises = 0
    for seed in range(96):
        candidate = copy.deepcopy(game)
        candidate.strategy_rng.seed(seed)
        action = candidate.suggest_action("月月")
        if action["action"] == "raise":
            raises += 1
            assert action["amount"] == 2
            candidate.act("月月", **action)
    assert 1 <= raises <= 16


@pytest.mark.parametrize("situation", ["expensive", "many_players", "late"])
def test_golden_weak_hand_does_not_bluff_in_bad_spots(situation):
    game = golden_bluff_game()
    if situation == "expensive":
        game.base_bet = 16
    elif situation == "many_players":
        game.players.extend([poker.PokerPlayer("另一玩家"), poker.PokerPlayer("第四玩家")])
    else:
        game.action_count = 20
    for seed in range(20):
        candidate = copy.deepcopy(game)
        candidate.strategy_rng.seed(seed)
        assert candidate.suggest_action("月月")["action"] != "raise"


def test_golden_strong_hand_raises_or_compares_when_price_increases():
    game = golden_bluff_game()
    game.players[0].hand = ["ClubA", "DiamondA", "HeartA"]
    game.base_bet = 16
    assert game.suggest_action("月月")["action"] in {"raise", "compare"}


@pytest.mark.parametrize("game_class,count", [(poker.TexasHoldemGame, 8), (poker.GoldenFlowerGame, 5)])
def test_strategy_actions_remain_legal_with_large_buyin_and_stakes(game_class, count):
    for seed in range(5):
        game = game_class([str(index) for index in range(count)], seed=seed, buy_in=20000, base_stake=100)
        for _ in range(500):
            if game.finished:
                break
            user_id = game.current_player_id
            action = game.suggest_action(user_id)
            game.act(user_id, **action)
        assert game.finished
        assert sum(player.stack for player in game.players) == 20000 * count
