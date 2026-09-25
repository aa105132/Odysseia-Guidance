"""炸金花情境混合策略：随机而非乱打，信息隔离和预算仍受保护。"""
import asyncio
import copy
import importlib
from collections import Counter
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


def distribution(game, proposal=None, count=400):
    result = Counter()
    for seed in range(count):
        candidate = copy.deepcopy(game)
        candidate.strategy_rng.seed(seed)
        before = copy.deepcopy(candidate.public_state('bot'))
        deal_rng = candidate.rng.getstate()
        action = candidate.guard_bot_action('bot', proposal)
        assert candidate.public_state('bot') == before
        assert candidate.rng.getstate() == deal_rng
        assert candidate.guard_bot_action('bot', proposal) == action
        result[action['action']] += 1
        candidate.act('bot', **action)
    return result


@pytest.mark.parametrize('hand,strong', HANDS)
def test_hand_threshold_does_not_force_one_action(hand, strong):
    game = raised_game(hand)
    context = llm.build_table_context('golden_flower', game, 'bot')
    policy = context['strategy']['risk_policy']
    assert policy['is_strong_hand'] is strong
    assert not policy['weak_hand_must_fold']
    assert policy['seen_raise_opponents'] == ['seat_1']
    assert context['state']['players'][0]['hand'] == ['Hidden'] * 3
    counts = distribution(game)
    assert counts['call'] > 10
    assert counts['raise'] > 0
    assert counts['compare'] > 0
    if not strong:
        assert 20 < counts['fold'] < 390
    else:
        assert counts['fold'] == 0


@pytest.mark.parametrize('proposal', [{'action': 'call'}, {'action': 'fold'}, {'action': 'raise', 'amount': 4}])
def test_constant_model_proposal_does_not_collapse_mixture(proposal):
    counts = distribution(raised_game(HANDS[1][0]), proposal)
    assert set(counts) == {'fold', 'call', 'raise', 'compare'}
    assert max(counts.values()) < 350


def test_visible_pressure_and_multiple_players_reduce_continuation():
    base = raised_game(HANDS[1][0])
    blind = copy.deepcopy(base)
    blind.seen_raises['human'] = 0
    blind.players[0].seen = False
    repeated = copy.deepcopy(base)
    repeated.seen_raises['human'] = 3
    crowded = copy.deepcopy(repeated)
    crowded.players.extend([poker.PokerPlayer('third'), poker.PokerPlayer('fourth')])
    rates = [distribution(game)['fold'] for game in [blind, base, repeated, crowded]]
    assert rates == sorted(rates)
    assert rates[-1] > rates[0] + 80


def test_only_actual_seen_raise_is_pressure_not_later_look():
    game = poker.GoldenFlowerGame(['human', 'bot'], seed=9)
    game.act('human', 'raise', amount=2)
    game.act('human', 'look')
    game.players[1].seen = True
    game.players[1].hand = HANDS[1][0]
    assert not poker.golden_bot_policy(game.public_state('bot'), 'bot')['seen_raise_opponents']
    game.act('bot', 'call')
    game.act('human', 'raise', amount=4)
    assert game.seen_raises['human'] == 1
    assert poker.golden_bot_policy(game.public_state('bot'), 'bot')['seen_raise_opponents'] == ['human']


def test_unseen_mixes_then_must_look_without_reading_own_cards():
    game = raised_game(HANDS[0][0], seen=False)
    game.players[1].hand = ['unreadable private cards']
    counts = distribution(game)
    assert counts['look'] > counts['call'] > 0
    game.action_count = 6
    assert game.guard_bot_action('bot', {'action': 'call'}) == {'action': 'look'}


@pytest.mark.parametrize('buy_in,stake', [(100, 1), (20000, 100)])
def test_weak_paid_actions_stay_within_budget(buy_in, stake):
    for seed in range(100):
        game = poker.GoldenFlowerGame(['bot', 'human'], seed=seed, buy_in=buy_in, base_stake=stake)
        game.players[0].hand = HANDS[1][0]
        game.act('bot', 'look')
        budget = min(12 * stake, buy_in * .20)
        for _ in range(30):
            before = game.players[0].total_bet
            action = game.guard_bot_action('bot', {'action': 'call'})
            if action['action'] in {'call', 'raise', 'compare'}:
                cost = game.base_bet * 2 if action['action'] == 'call' else (action['amount'] * 2 if action['action'] == 'raise' else game.base_bet * 4)
                assert before + cost <= budget
                assert cost <= min(stake * 8, game.players[0].stack * .10)
            game.act('bot', **action)
            if game.finished:
                break
            game.act('human', 'call' if 'call' in game.public_state('human')['legal_actions'] else 'fold')
            if game.finished:
                break
        assert game.finished
        assert sum(p.stack for p in game.players) == buy_in * 2


def test_hidden_hands_and_deck_cannot_change_decisions():
    game = raised_game(HANDS[1][0])
    candidate = copy.deepcopy(game)
    candidate.players[0].hand = ['hidden opponent cards'] * 3
    candidate.deck = ['hidden future cards']
    assert game.guard_bot_action('bot', {'action': 'call'}) == candidate.guard_bot_action('bot', {'action': 'call'})


def test_cached_decision_cannot_be_rerolled_by_proposal_or_return_mutation():
    game = raised_game(HANDS[1][0])
    action = game.guard_bot_action('bot', {'action': 'call'})
    rng = game.strategy_rng.getstate()
    action['action'] = 'invalid'
    assert game.guard_bot_action('bot', {'action': 'fold'})['action'] != 'invalid'
    assert game.strategy_rng.getstate() == rng
    game.act('bot', 'call')
    game.act('human', 'call')
    game.suggest_action('bot')
    assert game.strategy_rng.getstate() != rng


def test_strong_hands_hide_strength_and_vary_raise_size():
    game = raised_game(HANDS[3][0])
    amounts = set()
    for seed in range(100):
        candidate = copy.deepcopy(game)
        candidate.strategy_rng.seed(seed)
        action = candidate.suggest_action('bot')
        if action['action'] == 'raise':
            amounts.add(action['amount'])
    assert len(amounts) >= 2
    assert distribution(game)['call'] > 20


def test_excessive_price_forces_look_then_fold_but_does_not_restrict_human_rules():
    game = raised_game(HANDS[0][0], seen=False)
    game.base_bet = 16
    assert game.guard_bot_action('bot', {'action': 'call'}) == {'action': 'look'}
    game.act('bot', 'look')
    assert game.suggest_action('bot') == {'action': 'fold'}
    game.act('bot', 'call')
    assert game.players[1].total_bet == 33


class AlwaysCall:
    enabled = True
    timeout_seconds = 1

    async def choose_action(self, context):
        return {'action': 'call'}


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [True, False])
async def test_runner_model_and_fallback_respect_hard_risk_and_remember_actual_action(enabled):
    game = raised_game(HANDS[1][0], seen=False)
    game.base_bet = 16
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


def test_premium_short_stack_keeps_call_when_compare_is_unaffordable():
    game = raised_game(HANDS[5][0])
    game.players[1].stack = game.base_bet * 2
    assert game.suggest_action('bot') == {'action': 'call'}
    game.act('bot', 'call')


@pytest.mark.parametrize('count,buy_in,stake', [(2, 100, 1), (5, 100, 1), (5, 20000, 100)])
def test_mixed_policy_completes_many_games_with_legal_actions_and_conserved_chips(count, buy_in, stake):
    for seed in range(60):
        game = poker.GoldenFlowerGame([str(i) for i in range(count)], seed=seed, buy_in=buy_in, base_stake=stake)
        for _ in range(300):
            if game.finished:
                break
            uid = game.current_player_id
            before = game.rng.getstate()
            action = game.guard_bot_action(uid, {'action': 'call'})
            assert game.guard_bot_action(uid, {'action': 'fold'}) == action
            assert game.rng.getstate() == before
            game.act(uid, **action)
            assert all(player.stack >= 0 for player in game.players)
        assert game.finished
        assert sum(player.stack for player in game.players) == count * buy_in
