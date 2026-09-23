"""德州模型策略上下文、边池成本及免费过牌的回归。"""

import copy
import importlib
import json

import httpx
import pytest


llm = importlib.import_module("src.chat.features.games.blackjack-web.game_llm")
poker = importlib.import_module("src.chat.features.games.blackjack-web.poker_games")


def context_for(game):
    return llm.build_table_context("texas", game, game.current_player_id)


def test_opening_cost_and_position_are_public_facts_not_equity_estimates():
    game = poker.TexasHoldemGame(["甲", "乙", "丙", "丁"], seed=42)
    context = context_for(game)
    strategy = context["strategy"]
    assert context["you"] == "seat_4"
    assert strategy["call_cost"] == 2
    assert strategy["contestable_pot_before_call"] == 3
    assert strategy["call_break_even_equity"] == 0.4
    assert strategy["street_position_order"] == ["seat_4", "seat_1", "seat_2", "seat_3"]
    assert not strategy["last_position"]
    assert strategy["unraised_preflop"]
    assert strategy["active_opponents"] == strategy["opponents_can_fold"] == 3
    assert 0 <= strategy["mix_percentile"] < 100
    for suggestion in strategy["raise_to_examples"]:
        trial = copy.deepcopy(game)
        trial.act(game.current_player_id, "raise", amount=suggestion["raise_to"])
        assert suggestion["additional_cost"] == suggestion["raise_to"]


def test_short_stack_pot_odds_exclude_ineligible_side_pots():
    game = poker.TexasHoldemGame(["甲", "乙", "丙"], seed=3)
    game._current_index = 0
    game.current_bet = 100
    for index, player in enumerate(game.players):
        player.total_bet = player.round_bet = 10 if index == 0 else 100
        player.stack = 10 if index == 0 else 0
        player.all_in = index != 0
    strategy = context_for(game)["strategy"]
    assert strategy["call_cost"] == 10
    assert strategy["contestable_pot_before_call"] == 50
    assert strategy["call_break_even_equity"] == 0.1667
    assert strategy["call_stack_fraction"] == 1
    assert strategy["opponents_can_fold"] == 0
    assert strategy["raise_to_examples"] == []


def test_flop_relative_position_and_folded_contributions():
    game = poker.TexasHoldemGame(["甲", "乙", "丙", "丁"], seed=42)
    while game.phase == "preflop":
        actions = game.public_state(game.current_player_id)["legal_actions"]
        game.act(game.current_player_id, "check" if "check" in actions else "call")
    game.players[2].folded = True
    context = context_for(game)
    strategy = context["strategy"]
    assert context["you"] == "seat_2"
    assert strategy["street_position_order"] == ["seat_2", "seat_4", "seat_1"]
    assert strategy["contestable_pot_before_call"] == 8
    assert strategy["call_cost"] == strategy["call_break_even_equity"] == 0
    assert not strategy["unraised_preflop"]
    assert strategy["active_opponents"] == 2


def test_context_and_mixing_do_not_read_other_hands_or_advance_dealing_rng():
    game = poker.TexasHoldemGame(["甲", "乙", "丙", "丁"], seed=17)
    before = game.rng.getstate()
    context = context_for(game)
    changed = copy.deepcopy(game)
    changed.deck = ["不能读取牌堆"]
    for player in changed.players:
        if player.user_id != changed.current_player_id:
            player.hand = ["不能读取暗牌", "不能读取暗牌"]
    assert context_for(changed) == context
    assert game.rng.getstate() == before
    wire = llm.conversation_user_message(context)["content"]
    assert not any(name in wire for name in ("甲", "乙", "丙", "丁"))
    assert "strategy" in json.loads(wire)


@pytest.mark.parametrize("count", [2, 3, 8])
def test_raise_examples_are_legal_for_real_streets_including_short_stacks(count):
    game = poker.TexasHoldemGame([str(index) for index in range(count)], seed=9)
    for player in game.players:
        player.stack = 9 - player.total_bet
    for _ in range(100):
        if game.finished:
            break
        context = context_for(game)
        for suggestion in context["strategy"]["raise_to_examples"]:
            trial = copy.deepcopy(game)
            trial.act(game.current_player_id, "raise", amount=suggestion["raise_to"])
        legal = context["state"]["legal_actions"]
        game.act(game.current_player_id, "check" if "check" in legal else "call")
    assert game.finished


def test_strategy_builder_only_requires_public_interface():
    source = poker.TexasHoldemGame(["甲", "乙"], seed=42)
    state = source.public_state(source.current_player_id)

    class PublicOnlyEngine:
        def public_state(self, _):
            return copy.deepcopy(state)

        def __getattribute__(self, name):
            if name != "public_state":
                raise AssertionError("禁止读取引擎内部属性")
            return object.__getattribute__(self, name)

    assert llm.build_table_context("texas", PublicOnlyEngine(), source.current_player_id) == context_for(source)


@pytest.mark.asyncio
@pytest.mark.parametrize("free_check", [False, True])
async def test_free_fold_is_checked_without_forcing_paid_calls(monkeypatch, free_check):
    game = poker.TexasHoldemGame(["甲", "乙"], seed=42)
    if free_check:
        game.act(game.current_player_id, "call")
    context = context_for(game)
    before = copy.deepcopy(game.public_state(game.current_player_id))
    captured = []

    def handler(request):
        payload = json.loads(request.content)
        captured.append(payload)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"action":"fold"}'}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setenv("GAME_LLM_ENABLED", "true")
    monkeypatch.setenv("GAME_LLM_BASE_URL", "https://model.test/v1")
    monkeypatch.setenv("GAME_LLM_MODEL", "打牌LLM")
    monkeypatch.setenv("GAME_LLM_API_KEY", "synthetic-only")
    model = llm.GameLLMClient()
    result = await model.choose_action(context)
    assert result == {"action": "check" if free_check else "fold"}
    copy.deepcopy(game).act(game.current_player_id, **result)
    assert game.public_state(game.current_player_id) == before
    assert json.loads(captured[0]["messages"][-1]["content"])["strategy"] == context["strategy"]
    assert captured[0]["model"] == "打牌LLM"
    assert "max_tokens" not in captured[0]
    assert "reasoning_effort" not in captured[0]


def test_paid_fold_and_malformed_result_validation_stay_strict():
    context = context_for(poker.TexasHoldemGame(["甲", "乙"], seed=42))
    assert llm.GameLLMClient._validate_action({"action": "fold"}, context) == {"action": "fold"}
    context["state"]["legal_actions"] = ["fold", "check"]
    assert llm.GameLLMClient._validate_action({"action": "fold", "reason": "额外字段"}, context) is None
    assert llm.GameLLMClient._validate_action({"action": "raise", "amount": 10}, context) is None


def test_golden_flower_has_its_own_strategy_without_texas_assumptions():
    game = poker.GoldenFlowerGame(["甲", "乙"], seed=17)
    context = llm.build_table_context("golden_flower", game, game.current_player_id)
    assert "hand_analysis" not in context["strategy"]
    assert not context["strategy"]["equity_reference"]["available"]
    assert llm.GameLLMClient._validate_action({"action": "fold"}, context) == {"action": "fold"}
