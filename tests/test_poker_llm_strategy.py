"""模型参考牌力的真实引擎回归：盲牌边界、非对子、全下和真实行动成本。"""

import copy
import importlib
import json
import random
import time

import pytest


llm = importlib.import_module("src.chat.features.games.blackjack-web.game_llm")
strategy = importlib.import_module("src.chat.features.games.blackjack-web.poker_llm_strategy")
poker = importlib.import_module("src.chat.features.games.blackjack-web.poker_games")


def texas_context(hand, *, board=(), opponents=1, bet=0, all_in=False):
    game = poker.TexasHoldemGame(["月月", *[f"玩家{index}" for index in range(opponents)]], seed=19)
    game._current_index = 0
    game.players[0].hand = list(hand)
    game.community_cards = list(board)
    game.phase = {0: "preflop", 3: "flop", 4: "turn", 5: "river"}[len(board)]
    game.current_bet = bet
    for player in game.players:
        player.round_bet = 0
        player.total_bet = 10
        player.stack = 90
        player.all_in = False
    if bet:
        game.players[1].round_bet = bet
        game.players[1].total_bet += bet
        game.players[1].stack -= bet
        game.players[1].all_in = all_in
    return llm.build_table_context("texas", game, "月月")


def golden_game(hand=("HeartA", "ClubK", "Diamond9"), count=2):
    game = poker.GoldenFlowerGame(["月月", *[f"玩家{index}" for index in range(count - 1)]], seed=7)
    game.act("月月", "look")
    game.players[0].hand = list(hand)
    return game


def test_unpaired_ace_king_is_not_treated_as_worthless_preflop():
    premium = texas_context(["HeartA", "ClubK"])["strategy"]["hand_analysis"]
    weak = texas_context(["Heart7", "Club2"])["strategy"]["hand_analysis"]
    assert not premium["starting_hand"]["pocket_pair"]
    assert premium["starting_hand"]["high_cards_10_plus"] == 2
    assert premium["equity_reference"]["heads_up_random_share"] > 0.58
    assert premium["equity_reference"]["heads_up_random_share"] > weak["equity_reference"]["heads_up_random_share"] + 0.2


def test_eight_player_opening_keeps_heads_up_and_all_active_assumptions_separate():
    analysis = texas_context(["HeartA", "ClubA"], opponents=7)["strategy"]["hand_analysis"]
    equity = analysis["equity_reference"]
    assert equity["all_active_opponents"] == 7
    assert equity["heads_up_random_share"] > 0.75
    assert equity["all_active_random_share"] < equity["heads_up_random_share"] - 0.3
    assert "尚未行动者可能弃牌" in equity["preflop_caution"]


def test_suited_connectors_and_real_semibluff_draws_are_visible():
    hand = texas_context(["Heart9", "Heart10"])["strategy"]["hand_analysis"]["starting_hand"]
    assert hand["suited"] and hand["gap"] == 0 and not hand["pocket_pair"]
    flush = texas_context(["HeartA", "HeartK"], board=["Heart2", "Heart7", "Club9"])["strategy"]["hand_analysis"]
    assert flush["made_hand"] == "高牌"
    assert flush["flush_draws"] == [{"suit": "Heart", "nut_flush_draw": True}]
    straight = texas_context(["Spade9", "Club10"], board=["Heart7", "Diamond8", "ClubA"])["strategy"]["hand_analysis"]
    assert straight["straight_completing_ranks"] == [6, 11]


def test_public_board_draw_is_not_mistaken_for_a_private_draw_and_river_has_no_future_draw():
    board_only = texas_context(["Spade2", "Diamond2"], board=["Heart6", "Club7", "Diamond8", "Spade9"])["strategy"]["hand_analysis"]
    assert board_only["straight_completing_ranks"] == []
    river = texas_context(["HeartA", "ClubQ"], board=["Heart2", "Heart7", "Heart9", "DiamondJ", "Club3"])["strategy"]["hand_analysis"]
    assert river["flush_draws"] == river["straight_completing_ranks"] == []


def test_all_in_is_given_odds_and_a_labeled_range_sensitivity_not_automatic_folding():
    context = texas_context(["HeartA", "ClubA"], bet=90, all_in=True)
    reference = context["strategy"]["hand_analysis"]["equity_reference"]
    assert context["strategy"]["hand_analysis"]["opponents_all_in"] == ["seat_2"]
    assert reference["heads_up_stronger_range_share"] > context["strategy"]["call_break_even_equity"]
    assert reference["heads_up_random_share"] > 0.75
    assert "仅是假设" in reference["stronger_range_assumption"]
    assert "不能一见all_in就fold" in context["rules"]
    assert llm.GameLLMClient._validate_action({"action": "fold"}, context) == {"action": "fold"}


def test_nuts_equity_and_public_board_ties_use_the_real_settlement_ranking():
    nuts = texas_context(["HeartA", "HeartK"], board=["HeartQ", "HeartJ", "Heart10", "Club2", "Diamond3"])
    assert nuts["strategy"]["hand_analysis"]["equity_reference"]["all_active_random_share"] == 1
    chop = texas_context(["Spade2", "Club3"], board=["HeartA", "HeartK", "HeartQ", "HeartJ", "Heart10"], opponents=3)
    reference = chop["strategy"]["hand_analysis"]["equity_reference"]
    assert reference["heads_up_random_share"] == 0.5
    assert reference["all_active_random_share"] == 0.25


@pytest.mark.parametrize("game_class", [poker.TexasHoldemGame, poker.GoldenFlowerGame])
def test_reference_never_reads_hidden_opponent_hands_or_dealing_rng(game_class):
    identities = ["PRIVATE-YUEYUE", "PRIVATE-PLAYER", "PRIVATE-OTHER"]
    game = game_class(identities, seed=23)
    user_id = game.current_player_id
    game_type = "texas" if game_class is poker.TexasHoldemGame else "golden_flower"
    if game_type == "golden_flower":
        game.act(user_id, "look")
    before_deal = game.rng.getstate()
    before_strategy = game.strategy_rng.getstate()
    before_global = random.getstate()
    context = llm.build_table_context(game_type, game, user_id)
    changed = copy.deepcopy(game)
    changed.deck = ["禁止读取未发牌"]
    for player in changed.players:
        if player.user_id != user_id:
            player.hand = ["禁止读取暗牌"] * len(player.hand)
    assert llm.build_table_context(game_type, changed, user_id) == context
    assert game.rng.getstate() == before_deal
    assert game.strategy_rng.getstate() == before_strategy
    assert random.getstate() == before_global
    wire = llm.conversation_user_message(context)["content"]
    assert not any(name in wire for name in (*identities, "禁止读取"))


def test_unseen_golden_hand_has_no_equity_and_look_is_not_mandatory():
    game = poker.GoldenFlowerGame(["月月", "玩家"], seed=23)
    context = llm.build_table_context("golden_flower", game, "月月")
    changed = copy.deepcopy(game)
    changed.players[0].hand = ["未看牌禁止读取"] * 3
    assert llm.build_table_context("golden_flower", changed, "月月") == context
    assert context["strategy"]["equity_reference"]["available"] is False
    assert "made_hand" not in context["strategy"]
    assert context["state"]["players"][0]["hand"] == ["Hidden"] * 3
    assert context["strategy"]["look_cost_now"] == 0
    assert context["strategy"]["look_ends_turn"] is False
    assert {"call", "raise"} <= set(context["state"]["legal_actions"])


def test_high_card_can_beat_unseen_golden_range_without_claiming_opponents_exact_hand():
    game = golden_game()
    context = llm.build_table_context("golden_flower", game, "月月")
    reference = context["strategy"]["equity_reference"]
    assert context["strategy"]["made_hand"] == "单张"
    assert context["strategy"]["unseen_opponents"] == ["seat_2"]
    assert reference["heads_up_random_strict_win_rate"] > 0.55
    assert "盲打不按强牌收紧" in reference["assumptions"]
    assert "未看牌对手不知道自己的牌" in context["strategy"]["unseen_range_note"]


@pytest.mark.parametrize("seen", [False, True])
@pytest.mark.parametrize("count", [2, 5])
def test_golden_raise_examples_and_compare_cost_match_engine_and_conserve_chips(seen, count):
    game = poker.GoldenFlowerGame([str(index) for index in range(count)], seed=17, buy_in=20000, base_stake=100)
    user_id = game.current_player_id
    if seen:
        game.act(user_id, "look")
    context = llm.build_table_context("golden_flower", game, user_id)
    info = context["strategy"]
    before = game.players[0].stack
    for example in info["raise_to_examples"]:
        candidate = copy.deepcopy(game)
        candidate.act(user_id, "raise", amount=example["raise_to"])
        assert before - candidate.players[0].stack == example["additional_cost"]
        assert example["additional_cost"] == example["raise_to"] * (2 if seen else 1)
        assert sum(player.stack + player.total_bet for player in candidate.players) == 20000 * count
        assert example["opponents_next_call_cost"][0]["cost"] == example["raise_to"]
    target = context["state"]["compare_targets"][0]
    candidate = copy.deepcopy(game)
    candidate.act(user_id, "compare", target_id=context["_seat_ids"][target])
    if not candidate.finished:
        assert before - candidate.players[0].stack == info["compare_cost"]
    assert info["compare_cost"] == info["call_cost"] * 2
    if count == 2:
        assert info["heads_up_compare_break_even_win_rate"] == round(info["compare_cost"] / (game.pot + info["compare_cost"]), 4)
    else:
        assert info["heads_up_compare_break_even_win_rate"] is None


def test_golden_compare_counts_ties_as_a_loss_for_initiator():
    game = golden_game(["ClubA", "DiamondK", "HeartQ"])
    game.players[1].hand = ["SpadeA", "HeartK", "ClubQ"]
    context = llm.build_table_context("golden_flower", game, "月月")
    assert "strict_win_rate" in " ".join(context["strategy"]["equity_reference"])
    game.act("月月", "compare", target_id="玩家0")
    assert game.finished and game.winners == ["玩家0"]


def test_sampling_work_is_bounded_and_context_cache_is_reused(monkeypatch):
    strategy._texas_equity.cache_clear()
    strategy._golden_equity.cache_clear()
    calls = {"texas": 0, "golden": 0}
    texas_rank, golden_rank = poker._sampled_texas_rank, poker.evaluate_golden_hand

    def count_texas(cards):
        calls["texas"] += 1
        return texas_rank(cards)

    def count_golden(cards):
        calls["golden"] += 1
        return golden_rank(cards)

    monkeypatch.setattr(poker, "_sampled_texas_rank", count_texas)
    monkeypatch.setattr(poker, "evaluate_golden_hand", count_golden)
    started = time.perf_counter()
    first = texas_context(["SpadeA", "ClubK"], opponents=7, bet=90, all_in=True)
    count = calls["texas"]
    assert count <= strategy.EQUITY_SAMPLES * 13
    assert texas_context(["SpadeA", "ClubK"], opponents=7, bet=90, all_in=True) == first
    assert calls["texas"] == count
    game = golden_game(count=5)
    llm.build_table_context("golden_flower", game, "月月")
    assert calls["golden"] <= strategy.EQUITY_SAMPLES * 4 + 4
    assert time.perf_counter() - started < 3
    assert len(llm.conversation_user_message(first)["content"].encode("utf-8")) < 12000
    json.dumps(first, allow_nan=False)
