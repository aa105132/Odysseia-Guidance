"""2–8 seat OpenSpiel ISMCTS bridge; approximate search, NOT GTO.

No foreign game rules: simulated transitions/side pots use TexasHoldemGame.
Only an explicit own/public snapshot crosses the process boundary.
"""
import copy
from importlib import import_module
import json
import random
from types import SimpleNamespace

poker = import_module('src.chat.features.games.blackjack-web.poker_games')
FIELDS = ('phase', 'buy_in', 'base_stake', 'small_blind', 'big_blind', 'current_player_id',
          'community_cards', 'current_bet', 'min_raise', 'pending_players', 'acted_at_bet', 'betting_history')
PLAYER_FIELDS = ('user_id', 'stack', 'round_bet', 'total_bet', 'folded', 'all_in',
                 'is_dealer', 'is_small_blind', 'is_big_blind')


def snapshot(context):
    if context.get('game_type') != 'texas':
        raise ValueError('Unsupported game')
    state, you = context['state'], context['you']
    result = {key: copy.deepcopy(state[key]) for key in FIELDS if key != 'betting_history'}
    history = state.get('betting_history', [])
    if not isinstance(history, list) or len(history) > 512:
        raise ValueError('Invalid public history')
    result['betting_history'] = []
    for event in history:
        if not isinstance(event, dict):
            raise ValueError('Invalid public event')
        public_event = {}
        for key in ('user_id', 'phase', 'action', 'prior_bet', 'round_bet', 'total_bet', 'pot', 'amount'):
            if key in event:
                value = event[key]
                if type(value) not in (str, int):
                    raise ValueError('Invalid public event value')
                public_event[key] = value
        result['betting_history'].append(public_event)
    result['players'] = [{key: p[key] for key in PLAYER_FIELDS} |
                         ({'hand': list(p['hand'])} if p['user_id'] == you else {})
                         for p in state['players']]
    result['you'] = you
    seats = [p['user_id'] for p in result['players']]
    if not 2 <= len(seats) <= 8 or len(set(seats)) != len(seats) or you != result['current_player_id']:
        raise ValueError('Invalid seats/turn')
    if any(not isinstance(seat, str) or not seat for seat in seats) or you not in seats:
        raise ValueError('Invalid seat identities')
    for key in ('buy_in', 'base_stake', 'small_blind', 'big_blind', 'min_raise', 'current_bet'):
        value = result[key]
        if type(value) is not int or not (0 if key == 'current_bet' else 1) <= value < 2**53:
            raise ValueError('Invalid betting configuration')
    own = next(p for p in result['players'] if p['user_id'] == you)
    board = result['community_cards']
    if result['phase'] not in ('preflop', 'flop', 'turn', 'river'):
        raise ValueError('Unsupported phase')
    if len(board) != {'preflop': 0, 'flop': 3, 'turn': 4, 'river': 5}[result['phase']]:
        raise ValueError('Invalid board')
    known = own['hand'] + board
    if len(own['hand']) != 2 or len(set(known)) != len(known) or set(known) - set(poker.create_deck()):
        raise ValueError('Invalid own cards')
    for p in result['players']:
        for key in ('stack', 'round_bet', 'total_bet'):
            if type(p[key]) is not int or not 0 <= p[key] < 2**53:
                raise ValueError('Invalid chips')
        if any(type(p[key]) is not bool for key in ('folded', 'all_in', 'is_dealer', 'is_small_blind', 'is_big_blind')):
            raise ValueError('Invalid player flags')
        if p['total_bet'] < p['round_bet'] or p['all_in'] != (p['stack'] == 0):
            raise ValueError('Inconsistent chips')
    if you not in result['pending_players'] or set(result['pending_players']) - set(seats):
        raise ValueError('Missing pending turn')
    if set(result['acted_at_bet']) - set(seats):
        raise ValueError('Invalid raise rights')
    if own['folded'] or own['all_in']:
        raise ValueError('Inactive actor')
    if len(set(result['pending_players'])) != len(result['pending_players']):
        raise ValueError('Duplicate pending seats')
    if any(p['user_id'] in result['pending_players'] and (p['folded'] or p['all_in']) for p in result['players']):
        raise ValueError('Inactive pending seat')
    if any(type(v) is not int or not 0 <= v <= result['current_bet'] for v in result['acted_at_bet'].values()):
        raise ValueError('Invalid acted bet')
    if result['current_bet'] < max(p['round_bet'] for p in result['players']):
        raise ValueError('Bet below contribution')
    for flag in ('is_dealer', 'is_small_blind', 'is_big_blind'):
        if sum(bool(p[flag]) for p in result['players']) != 1:
            raise ValueError('Invalid positions')
    return result


def materialize(data, rng):
    """Build an independent hypothetical world, never clone a live engine."""
    seats = [p['user_id'] for p in data['players']]
    own = next(p for p in data['players'] if p['user_id'] == data['you'])
    known = own['hand'] + data['community_cards']
    unseen = [card for card in poker.create_deck() if card not in known]
    rng.shuffle(unseen)
    engine = poker.TexasHoldemGame(seats, seed=0, buy_in=data['buy_in'], base_stake=data['base_stake'])
    engine.community_cards = list(data['community_cards'])
    engine.burned_cards = []
    # Uniform joint range. Includes folded blockers; no claims of learned ranges.
    for player, source in zip(engine.players, data['players']):
        player.hand = list(own['hand']) if player.user_id == data['you'] else [unseen.pop(), unseen.pop()]
        for key in ('stack', 'round_bet', 'total_bet', 'folded', 'all_in'):
            setattr(player, key, source[key])
    engine.deck = unseen
    engine.phase = data['phase']
    engine.current_bet = data['current_bet']
    engine.min_raise = data['min_raise']
    engine._pending = set(data['pending_players'])
    engine._acted_at_bet = dict(data['acted_at_bet'])
    engine._current_index = seats.index(data['you'])
    engine.betting_history = copy.deepcopy(data['betting_history'])
    for flag, attr in (('is_dealer', 'dealer_index'), ('is_small_blind', 'small_blind_index'), ('is_big_blind', 'big_blind_index')):
        setattr(engine, attr, next(i for i, p in enumerate(data['players']) if p[flag]))
    return engine


def menu(engine):
    player = engine._player(engine.current_player_id)
    legal = engine._legal_actions(player)
    result = {}
    if 'fold' in legal and 'check' not in legal:
        result[0] = {'action': 'fold'}
    if 'check' in legal or 'call' in legal:
        result[1] = {'action': 'check' if 'check' in legal else 'call'}
    if 'raise' in legal:
        maximum = player.round_bet + player.stack
        minimum = min(maximum, engine._minimum_raise_target())
        cost = max(0, engine.current_bet - player.round_bet)
        targets = [minimum, max(minimum, engine.current_bet + max(1, (engine.pot + cost)//2)),
                   max(minimum, engine.current_bet + engine.pot + cost)]
        seen = set()
        for index, target in enumerate(targets, 2):
            target = min(maximum, target)
            if target < maximum and target not in seen:
                result[index] = {'action': 'raise', 'amount': target}
                seen.add(target)
    if 'all_in' in legal:
        # Short all-in calls are identical to call; avoid duplicate branches.
        if player.stack + player.round_bet > engine.current_bet:
            result[5] = {'action': 'all_in'}
    return result


class SearchState:
    def __init__(self, engine, baseline, scale):
        self.engine, self.baseline, self.scale = engine, baseline, scale

    def get_game(self):
        import pyspiel
        return SimpleNamespace(get_type=lambda: SimpleNamespace(
            dynamics=pyspiel.GameType.Dynamics.SEQUENTIAL,
            information=pyspiel.GameType.Information.IMPERFECT_INFORMATION))

    def current_player(self):
        return self.engine._current_index

    def information_state_string(self):
        e = self.engine
        uid = e.current_player_id
        # public_state hides all other cards before showdown. No deck/random state.
        return json.dumps(e.public_state(uid), sort_keys=True, ensure_ascii=True)

    def legal_actions(self):
        return list(menu(self.engine))

    def apply_action(self, action):
        payload = dict(menu(self.engine)[action])
        self.engine.act(self.engine.current_player_id, payload.pop('action'), **payload)

    def is_terminal(self):
        return self.engine.finished

    def is_chance_node(self):
        # All future cards are sampled jointly at root; transitions reveal only as dealt.
        return False

    def clone(self):
        return SearchState(copy.deepcopy(self.engine), self.baseline, self.scale)

    def returns(self):
        return [(p.stack - initial) / self.scale for p, initial in zip(self.engine.players, self.baseline)]


class Rollout:
    """Bounded stochastic continuation; actions inspect only acting player's view."""
    def __init__(self, rng):
        self.rng = rng

    def prior(self, state):
        actions = state.legal_actions()
        return [(a, 1 / len(actions)) for a in actions]

    def evaluate(self, state):
        state = state.clone()
        for step in range(192):
            if state.is_terminal():
                return state.returns()
            e = state.engine
            p = e._player(e.current_player_id)
            choices = menu(e)
            hand = [poker.card_parts(c) for c in p.hand]
            board = [poker.card_parts(c) for c in e.community_cards]
            if board:
                category = poker._sampled_texas_rank(hand + board)[0]
                quality = min(.95, .22 + category * .16)
            else:
                quality = min(.95, poker._starting_hand_score(hand) / 2)
            cost = min(p.stack, max(0, e.current_bet - p.round_bet))
            odds = cost / max(1, e.pot + cost)
            actions, weights = [], []
            for action in choices:
                actions.append(action)
                if action == 0:
                    weight = max(.05, odds * 3 + .55 - quality)
                elif action == 1:
                    weight = .8 + quality
                elif action == 5:
                    weight = .015 + max(0, quality - .8)
                else:
                    weight = .03 + quality ** 3 * .5
                weights.append(weight)
            # After a bounded betting continuation, only call/check to finish,
            # with real side-pot settlement. No artificial chips or direct award.
            chosen = 1 if step >= 96 and 1 in choices else self.rng.choices(actions, weights)[0]
            state.apply_action(chosen)
        raise ValueError('Rollout exceeded bound')


def solve(data, simulations=192, seed=0):
    import numpy as np
    from open_spiel.python.algorithms import ismcts
    if type(simulations) is not int or not 16 <= simulations <= 2048:
        raise ValueError('Invalid search budget')
    rng = random.Random(seed)
    baseline = tuple(p['stack'] + p['total_bet'] for p in data['players'])
    scale = max(baseline)  # Shared scaling preserves each player's chip objective.
    root = SearchState(materialize(data, rng), baseline, scale)
    bot = ismcts.ISMCTSBot(root.get_game(), Rollout(rng), uct_c=.7,
                          max_simulations=simulations, random_state=np.random.RandomState(seed),
                          final_policy_type=ismcts.ISMCTSFinalPolicyType.MAX_VISIT_COUNT)
    bot.set_resampler(lambda state, player: SearchState(materialize(data, rng), baseline, scale))
    policy, chosen = bot.step_with_policy(root)
    return {'action': menu(root.engine)[int(chosen)], 'simulations': simulations,
            'players': len(baseline), 'policy': [(int(a), float(p)) for a, p in policy]}
