"""桌游活动的德州扑克与炸金花规则；筹码对应房间冻结的灵石。"""

import itertools
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


SUITS = ("Club", "Diamond", "Heart", "Spade")
RANKS = tuple(str(value) for value in range(2, 11)) + ("J", "Q", "K", "A")
RANK_VALUES = {rank: value for value, rank in enumerate(RANKS, 2)}
TEXAS_HAND_NAMES = ("高牌", "一对", "两对", "三条", "顺子", "同花", "葫芦", "四条", "同花顺")
GOLDEN_HAND_NAMES = ("单张", "对子", "顺子", "金花", "顺金", "豹子")
STARTING_STACK = 100


def create_deck() -> list[str]:
    return [f"{suit}{rank}" for suit in SUITS for rank in RANKS]


def card_parts(card: str) -> tuple[str, int]:
    for suit in SUITS:
        if card.startswith(suit) and card[len(suit):] in RANK_VALUES:
            return suit, RANK_VALUES[card[len(suit):]]
    raise ValueError("无效的扑克牌")


def _straight_high(values: list[int]) -> int:
    ranks = sorted(set(values), reverse=True)
    if 14 in ranks:
        ranks.append(1)
    for start in range(len(ranks) - 4):
        if ranks[start] - ranks[start + 4] == 4:
            return ranks[start]
    return 0


def evaluate_five(cards: list[str]) -> tuple[int, ...]:
    """返回可直接按字典序比较的五张牌牌力，不用花色打破平局。"""
    if len(cards) != 5 or len(set(cards)) != 5:
        raise ValueError("五张牌评估需要五张不同的牌")
    parts = [card_parts(card) for card in cards]
    ranks = sorted((rank for _, rank in parts), reverse=True)
    counts = Counter(ranks)
    groups = sorted(((count, rank) for rank, count in counts.items()), reverse=True)
    flush = len({suit for suit, _ in parts}) == 1
    straight = _straight_high(ranks)
    if flush and straight:
        return (8, straight)
    if groups[0][0] == 4:
        return (7, groups[0][1], groups[1][1])
    if [group[0] for group in groups] == [3, 2]:
        return (6, groups[0][1], groups[1][1])
    if flush:
        return (5, *ranks)
    if straight:
        return (4, straight)
    if groups[0][0] == 3:
        return (3, groups[0][1], *sorted((rank for rank in ranks if counts[rank] == 1), reverse=True))
    pairs = sorted((rank for rank, count in counts.items() if count == 2), reverse=True)
    if len(pairs) == 2:
        return (2, *pairs, next(rank for rank in ranks if counts[rank] == 1))
    if pairs:
        return (1, pairs[0], *sorted((rank for rank in ranks if counts[rank] == 1), reverse=True))
    return (0, *ranks)


def evaluate_best_hand(cards: list[str]) -> tuple[int, ...]:
    if not 5 <= len(cards) <= 7 or len(set(cards)) != len(cards):
        raise ValueError("德州牌力评估需要五至七张不同的牌")
    return max(evaluate_five(list(selection)) for selection in itertools.combinations(cards, 5))


def evaluate_golden_hand(cards: list[str]) -> tuple[int, ...]:
    """炸金花：豹子、顺金、金花、顺子、对子、单张；A23 为最小顺子。"""
    if len(cards) != 3 or len(set(cards)) != 3:
        raise ValueError("炸金花牌力评估需要三张不同的牌")
    parts = [card_parts(card) for card in cards]
    ranks = sorted((rank for _, rank in parts), reverse=True)
    counts = Counter(ranks)
    flush = len({suit for suit, _ in parts}) == 1
    straight = 3 if ranks == [14, 3, 2] else (
        ranks[0] if len(counts) == 3 and ranks[0] - ranks[-1] == 2 else 0
    )
    if len(counts) == 1:
        return (5, ranks[0])
    if flush and straight:
        return (4, straight)
    if flush:
        return (3, *ranks)
    if straight:
        return (2, straight)
    pair = next((rank for rank, count in counts.items() if count == 2), None)
    if pair is not None:
        return (1, pair, next(rank for rank in ranks if rank != pair))
    return (0, *ranks)


def _sampled_texas_rank(cards: list[tuple[str, int]]) -> tuple[int, ...]:
    """采样专用的五至七张牌评估，避免每次枚举 21 种五张组合。"""
    counts = Counter(rank for _, rank in cards)
    ranks = sorted(counts, reverse=True)
    suits: dict[str, list[int]] = {}
    for suit, rank in cards:
        suits.setdefault(suit, []).append(rank)
    flush = next((sorted(values, reverse=True) for values in suits.values() if len(values) >= 5), [])
    if flush and (high := _straight_high(flush)):
        return (8, high)
    quads = [rank for rank in ranks if counts[rank] == 4]
    if quads:
        return (7, quads[0], next(rank for rank in ranks if rank != quads[0]))
    trips = [rank for rank in ranks if counts[rank] >= 3]
    pairs = [rank for rank in ranks if counts[rank] >= 2]
    if trips and (other_pairs := [rank for rank in pairs if rank != trips[0]]):
        return (6, trips[0], other_pairs[0])
    if flush:
        return (5, *flush[:5])
    if high := _straight_high(ranks):
        return (4, high)
    if trips:
        return (3, trips[0], *[rank for rank in ranks if rank != trips[0]][:2])
    if len(pairs) >= 2:
        return (2, *pairs[:2], next(rank for rank in ranks if rank not in pairs[:2]))
    if pairs:
        return (1, pairs[0], *[rank for rank in ranks if rank != pairs[0]][:3])
    return (0, *ranks[:5])


_STRATEGY_DECK = [(suit, rank) for suit in SUITS for rank in range(2, 15)]


def _starting_hand_score(hand: list[tuple[str, int]]) -> float:
    """仅用于对公开大额加注建立较紧的候选范围。"""
    (suit_a, rank_a), (suit_b, rank_b) = hand
    high, low = max(rank_a, rank_b), min(rank_a, rank_b)
    if high == low:
        return 1.0 + high / 14
    return (high + low) / 28 + (0.12 if suit_a == suit_b else 0) - max(0, high - low - 1) * 0.035


def _validate_player_ids(player_ids: list[str], minimum: int, maximum: int) -> list[str]:
    ids = [str(user_id) for user_id in player_ids]
    if not minimum <= len(ids) <= maximum:
        raise ValueError(f"本游戏需要 {minimum} 至 {maximum} 名玩家")
    if len(set(ids)) != len(ids) or any(not user_id for user_id in ids):
        raise ValueError("玩家身份必须非空且不重复")
    return ids


def _integer_amount(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("下注金额必须为正整数")
    return value


def _validate_buy_in(buy_in: int) -> int:
    amount = _integer_amount(buy_in)
    if amount < STARTING_STACK:
        raise ValueError(f"每局至少带入 {STARTING_STACK} 灵石")
    return amount


def _validate_base_stake(base_stake: int, buy_in: int) -> int:
    amount = _integer_amount(base_stake)
    if amount > (2**53 - 1) // 80 or amount * 10 > buy_in:
        raise ValueError("底分须为安全整数且不超过单局上限的十分之一")
    return amount


@dataclass
class PokerPlayer:
    user_id: str
    hand: list[str] = field(default_factory=list)
    stack: int = STARTING_STACK
    round_bet: int = 0
    total_bet: int = 0
    folded: bool = False
    all_in: bool = False
    seen: bool = False
    payout: int = 0


class TexasHoldemGame:
    """单手无限注德州，支持不完整全下加注、行动重开和多层边池。"""

    SMALL_BLIND = 1
    BIG_BLIND = 2

    def __init__(self, player_ids: list[str], seed: int | None = None,
                 buy_in: int = STARTING_STACK, base_stake: int = 1):
        ids = _validate_player_ids(player_ids, 2, 8)
        self.buy_in = _validate_buy_in(buy_in)
        self.base_stake = _validate_base_stake(base_stake, self.buy_in)
        self.SMALL_BLIND = self.base_stake
        self.BIG_BLIND = self.base_stake * 2
        self.rng = random.Random(seed)
        # 策略采样不消耗洗牌随机数，不能通过陪玩决策影响发牌。
        self.strategy_rng = random.Random(f"texas-strategy:{seed}" if seed is not None else None)
        self.players = [PokerPlayer(user_id, stack=self.buy_in) for user_id in ids]
        self.deck = create_deck()
        self.rng.shuffle(self.deck)
        for _ in range(2):
            for player in self.players:
                player.hand.append(self.deck.pop())
        self.dealer_index = 0
        self.small_blind_index = 0 if len(ids) == 2 else 1
        self.big_blind_index = 1 if len(ids) == 2 else 2
        self.community_cards: list[str] = []
        self.burned_cards: list[str] = []
        self.phase = "preflop"
        self.finished = False
        self.showdown = False
        self.winners: list[str] = []
        self.side_pots: list[dict] = []
        self.current_bet = self.BIG_BLIND
        self.min_raise = self.BIG_BLIND
        self._acted_at_bet: dict[str, int] = {}
        self._pending = set(ids)
        self._pay(self.players[self.small_blind_index], self.SMALL_BLIND)
        self._pay(self.players[self.big_blind_index], self.BIG_BLIND)
        self._current_index = (self.big_blind_index + 1) % len(ids)
        self.message = f"已下小盲 {self.SMALL_BLIND}、大盲 {self.BIG_BLIND}，翻牌前下注。"

    @property
    def current_player_id(self) -> str | None:
        return None if self.finished else self.players[self._current_index].user_id

    @property
    def pot(self) -> int:
        return sum(player.total_bet for player in self.players)

    def _player(self, user_id: str) -> PokerPlayer:
        player = next((player for player in self.players if player.user_id == str(user_id)), None)
        if player is None:
            raise ValueError("你不在本局中")
        return player

    def _active(self) -> list[PokerPlayer]:
        return [player for player in self.players if not player.folded]

    def _eligible(self) -> list[PokerPlayer]:
        return [player for player in self.players if not player.folded and not player.all_in]

    def _pay(self, player: PokerPlayer, amount: int) -> None:
        player.stack -= amount
        player.round_bet += amount
        player.total_bet += amount
        player.all_in = player.stack == 0

    def _can_raise(self, player: PokerPlayer) -> bool:
        if player.stack + player.round_bet <= self.current_bet:
            return False
        if not any(other.user_id != player.user_id for other in self._eligible()):
            return False
        previous = self._acted_at_bet.get(player.user_id)
        return previous is None or self.current_bet - previous >= self.min_raise

    def _minimum_raise_target(self) -> int:
        return self.current_bet + self.min_raise

    def _legal_actions(self, player: PokerPlayer) -> list[str]:
        if self.finished or player.user_id != self.current_player_id or player.folded or player.all_in:
            return []
        to_call = max(0, self.current_bet - player.round_bet)
        actions = ["fold", "call" if to_call else "check"]
        if self._can_raise(player):
            actions.extend(["raise", "all_in"])
        elif player.stack <= to_call:
            actions.append("all_in")
        return actions

    def act(self, user_id: str, action: str, **payload) -> None:
        player = self._player(user_id)
        if action not in self._legal_actions(player):
            raise ValueError("当前不能执行该操作，或尚未轮到你")
        to_call = max(0, self.current_bet - player.round_bet)
        target = None
        if action in ("raise", "all_in"):
            target = (player.round_bet + player.stack) if action == "all_in" else _integer_amount(payload.get("amount"))
            maximum = player.round_bet + player.stack
            if target > maximum:
                raise ValueError("筹码不足，无法加注到该金额")
            if action == "raise" and target <= self.current_bet:
                raise ValueError("加注目标必须高于本轮当前下注")
            if target > self.current_bet:
                if not self._can_raise(player):
                    raise ValueError("不完整全下加注尚未重新开放你的加注权")
                if target < self._minimum_raise_target() and target != maximum:
                    raise ValueError(f"最低需加注到 {self._minimum_raise_target()}，或全下")

        # 上方完成全部校验后再修改筹码和行动位，错误请求不会改变牌局。
        acted_index = self._current_index
        self._pending.discard(player.user_id)
        if action == "fold":
            player.folded = True
            self.message = f"玩家 {player.user_id} 弃牌。"
        elif action in ("check", "call"):
            amount = min(to_call, player.stack)
            if amount:
                self._pay(player, amount)
            self._acted_at_bet[player.user_id] = self.current_bet
            self.message = f"玩家 {player.user_id} {'过牌' if not amount else f'跟注 {amount}'}。"
        else:
            previous_bet = self.current_bet
            self._pay(player, target - player.round_bet)
            if target > previous_bet:
                increase = target - previous_bet
                if target >= self._minimum_raise_target():
                    self.min_raise = increase
                    self._acted_at_bet.clear()
                self.current_bet = target
                self._pending.update(
                    other.user_id for other in self._eligible()
                    if other.user_id != player.user_id and other.round_bet < target
                )
            self._acted_at_bet[player.user_id] = self.current_bet
            self.message = f"玩家 {player.user_id} {'全下' if player.all_in else '加注'}至 {target}。"
        self._advance(acted_index)

    def _advance(self, previous_index: int) -> None:
        active = self._active()
        if len(active) == 1:
            self._award_uncontested(active[0])
            return
        eligible = self._eligible()
        eligible_ids = {player.user_id for player in eligible}
        self._pending.intersection_update(eligible_ids)
        # 只剩一人能行动且已跟齐时，不能继续向全下对手下注。
        if len(eligible) <= 1 and all(player.round_bet >= self.current_bet for player in eligible):
            self._pending.clear()
        if not self._pending:
            self._next_street()
            return
        for offset in range(1, len(self.players) + 1):
            index = (previous_index + offset) % len(self.players)
            if self.players[index].user_id in self._pending:
                self._current_index = index
                return

    def _next_street(self) -> None:
        if self.phase == "river":
            self._showdown()
            return
        next_phase = {"preflop": "flop", "flop": "turn", "turn": "river"}[self.phase]
        self.phase = next_phase
        self.burned_cards.append(self.deck.pop())  # 每条街发公共牌前销掉一张牌。
        self.community_cards.extend(self.deck.pop() for _ in range(3 if next_phase == "flop" else 1))
        for player in self.players:
            player.round_bet = 0
        self.current_bet = 0
        self.min_raise = self.BIG_BLIND
        self._acted_at_bet.clear()
        eligible = self._eligible()
        self._pending = {player.user_id for player in eligible}
        if len(eligible) <= 1:
            self._next_street()
            return
        self.message = {"flop": "翻牌圈", "turn": "转牌圈", "river": "河牌圈"}[self.phase] + "下注。"
        for offset in range(1, len(self.players) + 1):
            index = (self.dealer_index + offset) % len(self.players)
            if self.players[index].user_id in self._pending:
                self._current_index = index
                break

    def _award_uncontested(self, winner: PokerPlayer) -> None:
        winner.stack += self.pot
        winner.payout = self.pot
        self.winners = [winner.user_id]
        self.side_pots = [{"amount": self.pot, "winners": list(self.winners)}]
        self.finished = True
        self.phase = "finished"
        self._pending.clear()
        self.message = f"其他玩家均已弃牌，玩家 {winner.user_id} 赢得 {self.pot} 灵石底池。"

    def _showdown(self) -> None:
        self.showdown = True
        strengths = {player.user_id: evaluate_best_hand(player.hand + self.community_cards) for player in self._active()}
        levels = sorted({player.total_bet for player in self.players if player.total_bet > 0})
        previous = 0
        award_order = self.players[self.dealer_index + 1:] + self.players[:self.dealer_index + 1]
        winner_ids = set()
        for level in levels:
            contributors = [player for player in self.players if player.total_bet >= level]
            amount = (level - previous) * len(contributors)
            previous = level
            contenders = [player for player in contributors if not player.folded]
            if len(contributors) == 1:
                # 无人跟到的超额投入原额返还，不把退款列为获胜。
                refund = contributors[0]
                refund.stack += amount
                refund.payout += amount
                self.side_pots.append({"amount": amount, "winners": [], "refund_to": refund.user_id})
                continue
            best = max(strengths[player.user_id] for player in contenders)
            tied_ids = {player.user_id for player in contenders if strengths[player.user_id] == best}
            winners = [player for player in award_order if player.user_id in tied_ids]
            share, remainder = divmod(amount, len(winners))
            for index, winner in enumerate(winners):
                payout = share + (1 if index < remainder else 0)
                winner.stack += payout
                winner.payout += payout
                winner_ids.add(winner.user_id)
            self.side_pots.append({"amount": amount, "winners": [player.user_id for player in winners]})
        self.winners = [player.user_id for player in self.players if player.user_id in winner_ids]
        self.finished = True
        self.phase = "finished"
        self._pending.clear()
        self.message = "摊牌结束，各主池、边池按最佳五张牌结算；同牌力平分。"

    def public_state(self, viewer_id: str) -> dict:
        viewer = next((player for player in self.players if player.user_id == str(viewer_id)), None)
        players = []
        for index, player in enumerate(self.players):
            visible = player.user_id == str(viewer_id) or (self.showdown and not player.folded)
            players.append({
                "user_id": player.user_id,
                "hand": list(player.hand) if visible else [],
                "hand_count": len(player.hand),
                "stack": player.stack,
                "round_bet": player.round_bet,
                "total_bet": player.total_bet,
                "folded": player.folded,
                "all_in": player.all_in,
                "payout": player.payout,
                "score_delta": player.stack - self.buy_in if self.finished else 0,
                "is_dealer": index == self.dealer_index,
                "is_small_blind": index == self.small_blind_index,
                "is_big_blind": index == self.big_blind_index,
                "hand_name": TEXAS_HAND_NAMES[evaluate_best_hand(player.hand + self.community_cards)[0]] if visible and len(self.community_cards) >= 3 else "",
            })
        maximum = viewer.stack + viewer.round_bet if viewer else 0
        return {
            "phase": self.phase,
            "buy_in": self.buy_in,
            "base_stake": self.base_stake,
            "small_blind": self.SMALL_BLIND,
            "big_blind": self.BIG_BLIND,
            "finished": self.finished,
            "current_player_id": self.current_player_id,
            "players": players,
            "legal_actions": self._legal_actions(viewer) if viewer else [],
            "community_cards": list(self.community_cards),
            "pot": self.pot,
            "current_bet": self.current_bet,
            "call_amount": min(viewer.stack, max(0, self.current_bet - viewer.round_bet)) if viewer else 0,
            "min_raise": self.min_raise,
            "min_raise_to": min(maximum, self._minimum_raise_target()),
            "max_raise_to": maximum,
            "side_pots": [dict(pot, winners=list(pot["winners"])) for pot in self.side_pots],
            "message": self.message,
            "winners": list(self.winners),
            "settlement": {player.user_id: player.stack - self.buy_in for player in self.players} if self.finished else {},
        }

    def _estimated_equity(self, player: PokerPlayer, opponents: list[PokerPlayer]) -> float:
        """从自己的底牌和已发公共牌采样；对手的牌和牌堆都不参与计算。"""
        hand = [card_parts(card) for card in player.hand]
        board = [card_parts(card) for card in self.community_cards]
        known = set(hand + board)
        unseen = [card for card in _STRATEGY_DECK if card not in known]
        score = 0.0
        samples = 80 if not board else 64
        for _ in range(samples):
            remaining = list(unseen)
            sampled_hands = []
            for opponent in opponents:
                # 已公开的大注意味着范围更强；评估候选时仍不能看未来公共牌。
                pressure = (opponent.round_bet >= self.BIG_BLIND * 4) if not board else (
                    opponent.round_bet >= max(self.BIG_BLIND * 2, self.pot * 0.25)
                )
                candidates = [self.strategy_rng.sample(remaining, 2) for _ in range(3 if pressure else 1)]
                if board:
                    candidate = max(candidates, key=lambda cards: _sampled_texas_rank(cards + board))
                else:
                    candidate = max(candidates, key=_starting_hand_score)
                sampled_hands.append(candidate)
                for card in candidate:
                    remaining.remove(card)
            runout = board + self.strategy_rng.sample(remaining, 5 - len(board))
            own_rank = _sampled_texas_rank(hand + runout)
            other_ranks = [_sampled_texas_rank(cards + runout) for cards in sampled_hands]
            best_other = max(other_ranks)
            if own_rank > best_other:
                score += 1
            elif own_rank == best_other:
                score += 1 / (1 + other_ranks.count(own_rank))
        return score / samples

    def suggest_action(self, user_id: str) -> dict:
        player = self._player(user_id)
        actions = self._legal_actions(player)
        if not actions:
            raise ValueError("当前没有可执行的操作")
        opponents = [other for other in self._active() if other.user_id != player.user_id]
        equity = self._estimated_equity(player, opponents)
        to_call = min(player.stack, max(0, self.current_bet - player.round_bet))
        # 只能争夺自己能覆盖的底池，短筹码不能把其他人的边池算作跟注收益。
        eligible_pot = sum(min(other.total_bet, player.total_bet + to_call) for other in self.players)
        expected_calls = 0.0
        if not self.community_cards and self.current_bet <= self.BIG_BLIND * 3 and to_call <= player.stack * 0.10:
            # 翻前早位采样包含尚未行动的人，给他们的小额跟注留折扣，否则八人桌 AA 也会被赔率误判。
            expected_calls = 0.55 * sum(
                min(other.stack, max(0, self.current_bet - other.round_bet))
                for other in opponents if other.user_id in self._pending
            )
        pot_odds = to_call / max(1, eligible_pot + to_call + expected_calls)
        commitment = to_call / max(1, player.stack)
        call_threshold = pot_odds + 0.035 + commitment * 0.075
        if len(opponents) > 1 and self.community_cards:
            call_threshold += 0.025
        can_continue = not to_call or equity >= call_threshold

        value_threshold = 0.64 if len(opponents) == 1 else (0.50 if len(opponents) == 2 else 0.40)
        if not self.community_cards:
            # 多人翻前的绝对胜率天然较低，仍应给 AA 等明显领先平均范围的牌取价值。
            value_threshold = max(0.27, 0.62 - 0.075 * (len(opponents) - 1))
        later_players = [other for other in opponents if not other.all_in and other.user_id in self._pending]
        in_position = not later_players or (not self.community_cards and self._current_index == self.dealer_index)
        roll = self.strategy_rng.random()
        # 低频偷池只在对手尚未表现强度且有弃牌空间时出现，多人底池不乱诈唬。
        bluff = (
            not to_call and in_position and len(opponents) <= 2
            and all(not other.all_in for other in opponents)
            and equity < value_threshold and roll < 0.055
        )
        value_raise = can_continue and equity >= value_threshold and roll < 0.82
        if "raise" in actions and (value_raise or bluff):
            effective_max = min(
                player.round_bet + player.stack,
                max(other.round_bet + other.stack for other in opponents if not other.all_in),
            )
            fraction = 0.45 if bluff else (0.75 if equity > 0.8 else 0.55)
            target = max(self._minimum_raise_target(), self.current_bet + round((self.pot + to_call) * fraction))
            target = min(target, effective_max)
            extra = target - player.round_bet
            # 边缘牌不因最低加注过高而被迫推光；极强牌或低筹码才允许大额承诺。
            affordable = extra <= player.stack * (0.18 if bluff else 0.38) or (not bluff and equity >= 0.80)
            if target >= self._minimum_raise_target() and affordable:
                return {"action": "raise", "amount": target}
        if "check" in actions:
            return {"action": "check"}
        if "call" in actions and can_continue:
            return {"action": "call"}
        return {"action": "fold"}


class GoldenFlowerGame:
    """炸金花，不启用 235 特例；不提供全下或边池。"""

    ANTE = 1
    MAX_ROUNDS = 20

    def __init__(self, player_ids: list[str], seed: int | None = None,
                 buy_in: int = STARTING_STACK, base_stake: int = 1):
        ids = _validate_player_ids(player_ids, 2, 5)
        self.buy_in = _validate_buy_in(buy_in)
        self.base_stake = _validate_base_stake(base_stake, self.buy_in)
        self.ANTE = self.base_stake
        self.rng = random.Random(seed)
        self.strategy_rng = random.Random(f"golden-strategy:{seed}" if seed is not None else None)
        self.deck = create_deck()
        self.rng.shuffle(self.deck)
        self.players = [PokerPlayer(user_id, stack=self.buy_in) for user_id in ids]
        for player in self.players:
            player.hand = [self.deck.pop() for _ in range(3)]
            player.stack -= self.ANTE
            player.total_bet = self.ANTE
        self.base_bet = self.ANTE
        self.phase = "betting"
        self.finished = False
        self.showdown = False
        self.winners: list[str] = []
        self._current_index = 0
        self.action_count = 0
        self.message = f"每人底注 {self.ANTE}；暗牌按基础注，看牌后跟注和加注费用加倍。"

    @property
    def current_player_id(self) -> str | None:
        return None if self.finished else self.players[self._current_index].user_id

    @property
    def pot(self) -> int:
        return sum(player.total_bet for player in self.players)

    def _player(self, user_id: str) -> PokerPlayer:
        player = next((player for player in self.players if player.user_id == str(user_id)), None)
        if player is None:
            raise ValueError("你不在本局中")
        return player

    def _active(self) -> list[PokerPlayer]:
        return [player for player in self.players if not player.folded]

    def _cost(self, player: PokerPlayer) -> int:
        return self.base_bet * (2 if player.seen else 1)

    def _legal_actions(self, player: PokerPlayer) -> list[str]:
        if self.finished or player.folded:
            return []
        actions = [] if player.seen else ["look"]
        if player.user_id != self.current_player_id:
            return actions
        actions.append("fold")
        if player.stack >= self._cost(player):
            actions.append("call")
        if player.stack >= self._cost(player) * 2:
            actions.extend(["raise", "compare"])
        return actions

    def _pay(self, player: PokerPlayer, amount: int) -> None:
        player.stack -= amount
        player.total_bet += amount

    def act(self, user_id: str, action: str, **payload) -> None:
        player = self._player(user_id)
        if action not in self._legal_actions(player):
            raise ValueError("当前不能执行该操作，或筹码不足")
        amount = None
        target = None
        if action == "raise":
            amount = _integer_amount(payload.get("amount"))
            if amount < self.base_bet * 2:
                raise ValueError(f"基础注至少加到 {self.base_bet * 2}")
            if amount * (2 if player.seen else 1) > player.stack:
                raise ValueError("筹码不足，无法加注")
        elif action == "compare":
            target_id = str(payload.get("target_id", ""))
            target = self._player(target_id)
            if target.folded or target.user_id == player.user_id:
                raise ValueError("请选择仍在本局中的其他玩家比牌")

        if action == "look":
            player.seen = True
            self.message = f"玩家 {player.user_id} 已看牌。"
            return
        if action == "fold":
            player.folded = True
            self.message = f"玩家 {player.user_id} 弃牌。"
        elif action == "call":
            paid = self._cost(player)
            self._pay(player, paid)
            self.message = f"玩家 {player.user_id} 跟注 {paid}。"
        elif action == "raise":
            self.base_bet = amount
            paid = self._cost(player)
            self._pay(player, paid)
            self.message = f"基础注提高至 {amount}，玩家 {player.user_id} 投入 {paid}。"
        elif action == "compare":
            self._pay(player, self._cost(player) * 2)
            initiator_wins = evaluate_golden_hand(player.hand) > evaluate_golden_hand(target.hand)
            loser = target if initiator_wins else player
            loser.folded = True
            self.message = f"玩家 {player.user_id} 与 {target.user_id} 比牌，{loser.user_id} 出局；同牌力时发起方负。"
        self.action_count += 1
        active = self._active()
        if len(active) == 1:
            self._finish(active, showdown=False)
        elif self.action_count >= self.MAX_ROUNDS * len(self.players):
            best = max(evaluate_golden_hand(candidate.hand) for candidate in active)
            self._finish([candidate for candidate in active if evaluate_golden_hand(candidate.hand) == best], showdown=True)
        else:
            for offset in range(1, len(self.players) + 1):
                index = (self._current_index + offset) % len(self.players)
                if not self.players[index].folded:
                    self._current_index = index
                    break

    def _finish(self, winners: list[PokerPlayer], showdown: bool) -> None:
        self.showdown = showdown
        share, remainder = divmod(self.pot, len(winners))
        for index, winner in enumerate(winners):
            winner.payout = share + (1 if index < remainder else 0)
            winner.stack += winner.payout
        self.winners = [winner.user_id for winner in winners]
        self.finished = True
        self.phase = "finished"
        self.message += " 本局结束，底池已按结果派发。" if not showdown else " 达到行动上限，剩余玩家摊牌，同牌力平分底池。"

    def public_state(self, viewer_id: str) -> dict:
        viewer = next((player for player in self.players if player.user_id == str(viewer_id)), None)
        players = []
        for player in self.players:
            visible = (player.user_id == str(viewer_id) and (player.seen or self.finished)) or (self.finished and not player.folded)
            players.append({
                "user_id": player.user_id,
                "hand": list(player.hand) if visible else [],
                "hand_count": len(player.hand),
                "stack": player.stack,
                "total_bet": player.total_bet,
                "folded": player.folded,
                "seen": player.seen,
                "payout": player.payout,
                "score_delta": player.stack - self.buy_in if self.finished else 0,
                "hand_name": GOLDEN_HAND_NAMES[evaluate_golden_hand(player.hand)[0]] if visible else "",
            })
        return {
            "phase": self.phase,
            "buy_in": self.buy_in,
            "base_stake": self.base_stake,
            "ante": self.ANTE,
            "finished": self.finished,
            "current_player_id": self.current_player_id,
            "players": players,
            "legal_actions": self._legal_actions(viewer) if viewer else [],
            "pot": self.pot,
            "current_bet": self.base_bet,
            "base_bet": self.base_bet,
            "call_amount": self._cost(viewer) if viewer else 0,
            "compare_cost": self._cost(viewer) * 2 if viewer else 0,
            "min_raise_to": self.base_bet * 2,
            "max_raise_to": viewer.stack // (2 if viewer.seen else 1) if viewer else 0,
            "action_count": self.action_count,
            "max_actions": self.MAX_ROUNDS * len(self.players),
            "compare_targets": [player.user_id for player in self._active() if player.user_id != str(viewer_id)],
            "message": self.message,
            "winners": list(self.winners),
            "settlement": {player.user_id: player.stack - self.buy_in for player in self.players} if self.finished else {},
        }

    def suggest_action(self, user_id: str) -> dict:
        player = self._player(user_id)
        actions = self._legal_actions(player)
        if player.user_id != self.current_player_id or not actions:
            raise ValueError("当前没有可执行的操作")
        if "look" in actions:
            # 暗牌阶段不能读取自己的手牌；只在首圈且底注很小时少量闷一手。
            if (
                "call" in actions and self.action_count < len(self.players)
                and self._cost(player) <= min(self.ANTE * 2, player.stack // 30)
                and self.strategy_rng.random() < 0.12
            ):
                return {"action": "call"}
            return {"action": "look"}
        opponents = [other for other in self._active() if other.user_id != player.user_id]
        known = set(player.hand)
        unseen = [card for card in create_deck() if card not in known]
        own_rank = evaluate_golden_hand(player.hand)
        # 对随机三张牌的胜率只是基线：持续跟注的明牌对手用更紧的范围估计。
        wins = sum(own_rank > evaluate_golden_hand(self.strategy_rng.sample(unseen, 3)) for _ in range(192))
        percentile = wins / 192
        rounds = self.action_count / len(self.players)
        equity = 1.0
        for other in opponents:
            tightness = min(0.45, (0.12 if other.seen else 0) + rounds * 0.035 + (0.12 if self.base_bet >= 4 * self.ANTE else 0))
            equity *= max(0.0, (percentile - tightness) / (1 - tightness))
        cost = self._cost(player)
        compare_cost = cost * 2
        roll = self.strategy_rng.random()
        affordable_raise = compare_cost <= min(player.stack * 0.18, self.buy_in * 0.16)
        bluff = (
            len(opponents) <= 2 and 0.8 <= rounds <= 5 and self.base_bet <= self.ANTE * 4
            and percentile < 0.65 and affordable_raise and roll < 0.075
        )
        value_raise = equity >= (0.72 if len(opponents) == 1 else 0.55) and roll < 0.62
        if "raise" in actions and (bluff or (value_raise and (affordable_raise or equity > 0.94))):
            return {"action": "raise", "amount": self.base_bet * 2}
        # 高成本、后期或接近行动上限时主动比牌，避免强牌白白跟注耗尽筹码。
        if "compare" in actions and (
            equity > 0.58 and (rounds >= 2 or cost >= player.stack * 0.10 or roll < 0.24)
        ):
            targets = sorted(opponents, key=lambda other: (other.seen, other.total_bet))
            return {"action": "compare", "target_id": targets[0].user_id}
        required = cost / max(1, self.pot + cost) + 0.08 + min(0.16, cost / max(1, player.stack) * 0.4)
        if "call" in actions and equity >= required and (percentile >= 0.45 or cost <= self.ANTE * 2):
            return {"action": "call"}
        return {"action": "fold"}
