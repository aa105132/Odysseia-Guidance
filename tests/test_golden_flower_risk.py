"""炸金花不再无限盲跟、弱牌不接看牌后加注；不读取对手底牌。"""
import asyncio
import copy
import importlib
from types import SimpleNamespace

import pytest

poker = importlib.import_module('src.chat.features.games.blackjack-web.poker_games')
llm = importlib.import_module('src.chat.features.games.blackjack-web.game_llm')
runner_module = importlib.import_module('src.chat.features.games.blackjack-web.game_bot_runner')

HANDS = [
    (['ClubA', 'DiamondJ', 'Heart8'], False),
    (['ClubA', 'DiamondA', 'Heart8'], False),
    (['ClubA', 'DiamondK', 'HeartQ'], False),
    (['ClubA', 'ClubJ', 'Club8'], True),
    (['ClubA', 'ClubK', 'ClubQ'], True),
    (['ClubA', 'DiamondA', 'HeartA'], True),
]


def raised_game(hand, *, seen=True, buy_in=100, stake=1):
    game = poker.GoldenFlowerGame(['human', 'bot'], seed=9, buy_in=buy_in, base_stake=stake)
    game.players[1].hand = hand
    game.players[1].seen = seen
    game.act('human', 'look')
    game.act('human', 'raise', amount=stake * 2)
    return game


@pytest.mark.parametrize('hand,strong', HANDS)
def test_hand_threshold_and_visible_pressure_context(hand, strong):
    game = raised_game(hand)
    context = llm.build_table_context('golden_flower', game, 'bot')
    policy = context['strategy']['risk_policy']
    assert policy['is_strong_hand'] is strong
    assert policy['weak_hand_must_fold'] is not strong
    assert policy['seen_raise_opponents'] == ['seat_1']
    assert context['state']['players'][0]['hand'] == ['Hidden'] * 3
    for seed in range(16):
        candidate = copy.deepcopy(game)
        candidate.strategy_rng.seed(seed)
        action = candidate.suggest_action('bot')
        assert (action['action'] == 'fold') is not strong
        candidate.act('bot', **action)


def test_unseen_looks_then_folds_without_spending_against_seen_raise():
    game = raised_game(HANDS[0][0], seen=False)
    before = game.players[1].stack
    assert game.suggest_action('bot') == {'action': 'look'}
    game.act('bot', 'look')
    assert game.suggest_action('bot') == {'action': 'fold'}
    assert game.players[1].stack == before


def test_only_actual_seen_raise_is_pressure_not_later_look():
    game = poker.GoldenFlowerGame(['human', 'bot'], seed=9)
    game.act('human', 'raise', amount=2)
    game.act('human', 'look')
    game.players[1].seen = True
    game.players[1].hand = HANDS[1][0]
    policy = poker.golden_bot_policy(game.public_state('bot'), 'bot')
    assert policy['seen_raise_opponents'] == []
    assert not policy['weak_hand_must_fold']
    game.act('bot', 'call')
    game.act('human', 'raise', amount=4)
    assert game.seen_raises['human'] == 1
    assert game.suggest_action('bot') == {'action': 'fold'}


def test_unseen_repeated_call_stops_after_first_round_without_reading_cards():
    game = poker.GoldenFlowerGame(['bot', 'human'], seed=3)
    game.players[0].hand = ['unreadable private cards']
    assert game.guard_bot_action('bot', {'action': 'call'}) == {'action': 'call'}
    game.act('bot', 'call')
    game.act('human', 'call')
    assert game.guard_bot_action('bot', {'action': 'call'}) == {'action': 'look'}
    assert game.suggest_action('bot') == {'action': 'look'}


@pytest.mark.parametrize('buy_in,stake', [(100, 1), (20000, 100)])
def test_weak_hand_calls_have_finite_budget_even_with_large_pot(buy_in, stake):
    game = poker.GoldenFlowerGame(['bot', 'human'], seed=3, buy_in=buy_in, base_stake=stake)
    game.players[0].hand = HANDS[1][0]
    game.act('bot', 'look')
    for _ in range(10):
        action = game.guard_bot_action('bot', {'action': 'call'})
        if action['action'] == 'fold':
            break
        game.act('bot', **action)
        game.act('human', 'call')
    else:
        pytest.fail('weak hand called without budget bound')
    assert game.players[0].total_bet <= min(6 * stake, buy_in * .10)


@pytest.mark.parametrize('action', [
    {'action': 'call'}, {'action': 'raise', 'amount': 4}, {'action': 'compare', 'target_id': 'human'},
])
def test_all_paid_actions_guarded_and_hidden_cards_ignored(action):
    game = raised_game(HANDS[1][0])
    game.players[0].hand = ['opponent cards must never be read']
    game.deck = ['future cards must never be read']
    assert game.guard_bot_action('bot', action) == {'action': 'fold'}
    assert game.suggest_action('bot') == {'action': 'fold'}


def test_strong_hand_keeps_legal_value_raise_and_compare():
    game = raised_game(HANDS[3][0])
    for action in [{'action': 'raise', 'amount': 4}, {'action': 'compare', 'target_id': 'human'}]:
        assert game.guard_bot_action('bot', action) == action
        candidate = copy.deepcopy(game)
        candidate.act('bot', **action)


def test_human_rules_not_restricted_by_bot_policy():
    game = raised_game(HANDS[0][0])
    assert game.suggest_action('bot') == {'action': 'fold'}
    game.act('bot', 'call')
    assert game.players[1].total_bet == 5


class AlwaysCall:
    enabled = True
    timeout_seconds = 1

    async def choose_action(self, context):
        return {'action': 'call'}


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [True, False])
async def test_runner_model_and_fallback_look_then_fold_and_remember_actual_action(enabled):
    game = raised_game(HANDS[1][0], seen=False)
    client = AlwaysCall()
    client.enabled = enabled
    runner = runner_module.GameBotRunner(client, None)
    room = SimpleNamespace(room_id='golden-test', round_number=1, revision=1,
                           turn_deadline=0, game_type='golden_flower', state='playing', engine=game)
    try:
        for expected in ['look', 'fold']:
            before = copy.deepcopy(game.public_state('bot'))
            action = runner.table_action(room, 'bot')
            if enabled:
                assert action is None
                await asyncio.gather(*(pending.task for pending in runner.pending.values()))
                action = runner.table_action(room, 'bot')
            assert action == {'action': expected}
            assert game.public_state('bot') == before
            if enabled:
                messages = next(iter(runner.conversations.values())).messages
                assert expected in messages[-1]['content']
            game.act('bot', **action)
            room.revision += 1
        assert game.finished
        assert sum(player.stack for player in game.players) == 200
        assert game.players[1].stack == 99
    finally:
        await runner.close()
