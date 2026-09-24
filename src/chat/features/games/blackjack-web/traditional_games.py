"""斗地主与简化四人麻将规则；计算灵石净额，由统一钱包事务托管和结算。"""

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
import random


POKER_SUITS = ("Club", "Diamond", "Heart", "Spade")
POKER_RANKS = ("3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A", "2")
POKER_VALUES = {rank: index + 3 for index, rank in enumerate(POKER_RANKS)}
LANDLORD_CARD_NAMES = {
    "single": "单张", "pair": "对子", "triple": "三张", "triple_single": "三带一",
    "triple_pair": "三带二", "straight": "顺子", "pair_straight": "连对",
    "airplane": "飞机", "airplane_single": "飞机带单翅", "airplane_pair": "飞机带对翅",
    "four_two_single": "四带二单", "four_two_pair": "四带两对", "bomb": "炸弹", "rocket": "王炸",
}


def poker_value(card: str) -> int:
    if card == "JokerSmall":
        return 16
    if card == "JokerBig":
        return 17
    if not isinstance(card, str):
        raise ValueError("无效的扑克牌")
    for suit in POKER_SUITS:
        if card.startswith(suit) and card[len(suit):] in POKER_VALUES:
            return POKER_VALUES[card[len(suit):]]
    raise ValueError("无效的扑克牌")


@dataclass(frozen=True)
class LandlordPattern:
    kind: str
    rank: int
    size: int
    chain: int = 1

    def to_dict(self) -> dict:
        return {"kind": self.kind, "name": LANDLORD_CARD_NAMES[self.kind],
                "rank": self.rank, "size": self.size, "chain": self.chain}


def _consecutive(values) -> bool:
    values = sorted(values)
    return bool(values) and values[-1] <= 14 and values == list(range(values[0], values[-1] + 1))


def classify_landlord_cards(cards: list[str]) -> LandlordPattern:
    """识别标准常见牌型；飞机单翅使用互异点数且不允许把主体第四张当翅膀。"""
    if not isinstance(cards, list) or not cards or len(cards) > 20:
        raise ValueError("请选择 1 到 20 张牌")
    if any(not isinstance(card, str) for card in cards):
        raise ValueError("无效的扑克牌")
    if len(set(cards)) != len(cards):
        raise ValueError("不能重复使用同一张牌")
    counts = Counter(poker_value(card) for card in cards)
    size = len(cards)
    ranks = sorted(counts)
    if size == 2 and ranks == [16, 17]:
        return LandlordPattern("rocket", 17, size)
    if len(counts) == 1:
        kind = {1: "single", 2: "pair", 3: "triple", 4: "bomb"}.get(size)
        if kind:
            return LandlordPattern(kind, ranks[0], size)
    if size == 4 and sorted(counts.values()) == [1, 3]:
        return LandlordPattern("triple_single", next(v for v in counts if counts[v] == 3), size)
    if size == 5 and sorted(counts.values()) == [2, 3]:
        return LandlordPattern("triple_pair", next(v for v in counts if counts[v] == 3), size)
    if size >= 5 and all(n == 1 for n in counts.values()) and _consecutive(ranks):
        return LandlordPattern("straight", ranks[-1], size, size)
    if size >= 6 and size % 2 == 0 and all(n == 2 for n in counts.values()) and _consecutive(ranks):
        return LandlordPattern("pair_straight", ranks[-1], size, size // 2)
    if size >= 6 and size % 3 == 0 and all(n == 3 for n in counts.values()) and _consecutive(ranks):
        return LandlordPattern("airplane", ranks[-1], size, size // 3)
    # 先匹配主体再核对翅膀，避免仅凭点数计数误认飞机。
    for unit_size, kind, wing_count in ((4, "airplane_single", 1), (5, "airplane_pair", 2)):
        chain = size // unit_size
        if size % unit_size or chain < 2:
            continue
        for low in range(3, 15 - chain + 1):
            core = list(range(low, low + chain))
            if not all(counts[value] == 3 for value in core):
                continue
            wings = {value: count for value, count in counts.items() if value not in core}
            if len(wings) == chain and all(count == wing_count for count in wings.values()):
                return LandlordPattern(kind, core[-1], size, chain)
    quad = next((value for value in ranks if counts[value] == 4), None)
    if quad is not None and size == 6:
        return LandlordPattern("four_two_single", quad, size)
    if quad is not None and size == 8:
        wings = [count for value, count in counts.items() if value != quad]
        if wings == [2, 2]:
            return LandlordPattern("four_two_pair", quad, size)
    raise ValueError("所选牌不能组成有效牌型")


def landlord_beats(candidate: LandlordPattern, previous: LandlordPattern) -> bool:
    if previous.kind == "rocket":
        return False
    if candidate.kind == "rocket":
        return True
    if candidate.kind == "bomb" and previous.kind != "bomb":
        return True
    return (candidate.kind == previous.kind and candidate.size == previous.size
            and candidate.chain == previous.chain and candidate.rank > previous.rank)


def _landlord_finishing_patterns(previous: dict, remaining: int, available: Counter) -> list[LandlordPattern]:
    """按公开剩余容量判断可能的末手牌型，不枚举或读取其他玩家的暗牌。"""
    old = LandlordPattern(previous["kind"], previous["rank"], previous["size"], previous.get("chain", 1))
    possible = []
    if remaining == old.size:
        for rank in range(old.rank + 1, 18):
            core = [rank]
            amount = {"single": 1, "pair": 2, "triple": 3, "bomb": 4,
                      "triple_single": 3, "triple_pair": 3,
                      "four_two_single": 4, "four_two_pair": 4}.get(old.kind)
            if old.kind in ("straight", "pair_straight", "airplane", "airplane_single", "airplane_pair"):
                if rank > 14 or rank - old.chain + 1 < 3:
                    continue
                core = list(range(rank - old.chain + 1, rank + 1))
                amount = 1 if old.kind == "straight" else 2 if old.kind == "pair_straight" else 3
            if amount is None or not all(available[value] >= amount for value in core):
                continue
            outside = [available[value] for value in range(3, 18) if value not in core]
            if old.kind == "triple_single" and not any(count >= 1 for count in outside):
                continue
            if old.kind == "triple_pair" and not any(count >= 2 for count in outside):
                continue
            if old.kind == "airplane_single" and sum(count >= 1 for count in outside) < old.chain:
                continue
            if old.kind == "airplane_pair" and sum(count >= 2 for count in outside) < old.chain:
                continue
            if old.kind == "four_two_single" and sum(outside) < 2:
                continue
            if old.kind == "four_two_pair" and sum(count >= 2 for count in outside) < 2:
                continue
            possible.append(LandlordPattern(old.kind, rank, old.size, old.chain))
    # 炸弹与王炸可以改变牌型；不能因地主剩牌数不等于桌面牌张数而认定安全。
    if remaining == 4:
        possible.extend(LandlordPattern("bomb", rank, 4) for rank in range(3, 16)
                        if available[rank] >= 4 and landlord_beats(LandlordPattern("bomb", rank, 4), old))
    if remaining == 2 and available[16] and available[17]:
        rocket = LandlordPattern("rocket", 17, 2)
        if landlord_beats(rocket, old):
            possible.append(rocket)
    return list(dict.fromkeys(possible))


def landlord_blocks_finishing_reply(pattern: LandlordPattern, cooperation: dict) -> bool:
    """接管须至少阻止一种公开信息允许的末手；更大的普通牌挡不住末手炸弹。"""
    threats = cooperation.get("landlord_possible_finishing_patterns", [])
    return bool(cooperation.get("unknown_finishing_shape_threat")) or any(
        not landlord_beats(LandlordPattern(item["kind"], item["rank"], item["size"], item["chain"]), pattern)
        for item in threats
    )


def landlord_team_context(state: dict, user_id: str) -> dict:
    """只用自己的手牌与公开桌面，说明让牌效果和地主末牌的拦截窗口。"""
    players = state.get("players", [])
    player_ids = [player["user_id"] for player in players]
    own = next(player for player in players if player["user_id"] == user_id)
    landlord = state.get("landlord_id")
    teammate = next((player for player in players
                     if player["user_id"] not in (user_id, landlord)), None) if landlord and landlord != user_id else None
    previous = state.get("last_play")
    teammate_leads = bool(teammate and previous and previous["user_id"] == teammate["user_id"])
    next_player = player_ids[(player_ids.index(user_id) + 1) % len(player_ids)]
    # 三人局当前上手是队友、下家也正好是队友，说明地主已经对这手过牌。
    # 不能单看 seat_actions 的 pass；它可能属于队友本次压牌前的旧动作。
    returns_lead = teammate_leads and next_player == teammate["user_id"]
    landlord_count = next((player["hand_count"] for player in players if player["user_id"] == landlord), None)
    threats = []
    unknown_shape = False
    if teammate_leads and next_player == landlord:
        visible = set(own.get("hand", [])) | set(previous.get("cards", []))
        for action in state.get("seat_actions", {}).values():
            visible.update(action.get("cards", []))
        known = Counter(poker_value(card) for card in visible)
        available = Counter({rank: max(0, (1 if rank >= 16 else 4) - known[rank]) for rank in range(3, 18)})
        unknown_shape = previous.get("kind") not in LANDLORD_CARD_NAMES and landlord_count == previous["size"]
        threats = _landlord_finishing_patterns(previous, landlord_count, available)
    reply_ranks = [pattern.rank for pattern in threats if pattern.kind == previous["kind"]] if previous else []
    return {
        "teammate": teammate["user_id"] if teammate else None,
        "teammate_remaining_count": teammate["hand_count"] if teammate else None,
        "teammate_close_to_finish": bool(teammate and 0 < teammate["hand_count"] <= 2),
        "teammate_leads": teammate_leads,
        "landlord_acts_next": bool(landlord and next_player == landlord),
        "landlord_already_passed_current_play": returns_lead,
        "pass_returns_lead_to_teammate": returns_lead,
        "landlord_possible_finishing_reply_ranks": reply_ranks,
        "landlord_possible_finishing_patterns": [pattern.to_dict() for pattern in threats],
        "unknown_finishing_shape_threat": unknown_shape,
        "must_block_landlord_finish": bool(threats) or unknown_shape,
    }


def _validate_players(player_ids: list[str], expected: int) -> list[str]:
    ids = [str(user_id) for user_id in player_ids]
    if len(ids) != expected or len(set(ids)) != expected or any(not uid for uid in ids):
        raise ValueError(f"此玩法需要 {expected} 位不同玩家")
    return ids


def _validate_buy_in(buy_in: int) -> int:
    if type(buy_in) is not int or not 100 <= buy_in <= 2**53 - 1:
        raise ValueError("带入金额至少为 100 灵石，且须为可安全传输的整数")
    return buy_in


def _validate_base_stake(base_stake: int, buy_in: int) -> int:
    if type(base_stake) is not int or not 1 <= base_stake <= min(buy_in // 10, (2**53 - 1) // 80):
        raise ValueError("底分须为安全整数且不超过单局上限的十分之一")
    return base_stake


class LandlordGame:
    """三人斗地主：每人一次叫分，全不叫重发，农民共享胜负。"""

    SCORE_UNIT = 1

    def __init__(self, player_ids: list[str], seed: int | None = None, buy_in: int = 100, base_stake: int = 1):
        self.player_ids = _validate_players(player_ids, 3)
        self.buy_in = _validate_buy_in(buy_in)
        self.SCORE_UNIT = _validate_base_stake(base_stake, self.buy_in)
        self.random = random.Random(seed)
        self.scores = {uid: 0 for uid in self.player_ids}
        self._finished = False
        self.winners: list[str] = []
        self.deal_count = 0
        self._deal()

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def current_player_id(self) -> str | None:
        return None if self.finished else self.player_ids[self.turn_index]

    def _deal(self):
        deck = [f"{suit}{rank}" for suit in POKER_SUITS for rank in POKER_RANKS]
        deck += ["JokerSmall", "JokerBig"]
        self.random.shuffle(deck)
        self.hands = {uid: sorted(deck[i * 17:(i + 1) * 17], key=poker_value)
                      for i, uid in enumerate(self.player_ids)}
        self.bottom_cards = deck[51:]
        self.bids: dict[str, int] = {}
        self.seat_actions: dict[str, dict] = {}
        self.highest_bid = 0
        self.landlord_id: str | None = None
        self.phase = "bidding"
        self.turn_index = self.deal_count % 3
        self.deal_count += 1
        self.last_play: dict | None = None
        self.last_pattern: LandlordPattern | None = None
        self.pass_count = 0
        self.multiplier = 1
        self.effective_stake = 0
        self.play_counts = {uid: 0 for uid in self.player_ids}
        self.message = "请依次叫分；最高叫分者成为地主，全不叫则重新发牌。"

    def _begin_play(self):
        self.landlord_id = max(self.bids, key=self.bids.get)
        self.hands[self.landlord_id].extend(self.bottom_cards)
        self.hands[self.landlord_id].sort(key=poker_value)
        self.turn_index = self.player_ids.index(self.landlord_id)
        self.phase = "playing"
        self.seat_actions.clear()
        self.message = "地主获得三张底牌并先出牌。"

    def _legal_actions(self, user_id: str) -> list[str]:
        if self.finished or user_id != self.current_player_id:
            return []
        if self.phase == "bidding":
            return ["bid"]
        return ["play", "pass"] if self.last_play else ["play"]

    def public_state(self, viewer_id: str) -> dict:
        viewer_id = str(viewer_id)
        return {
            "phase": self.phase, "finished": self.finished,
            "current_player_id": self.current_player_id,
            "players": [{"user_id": uid,
                         "hand": list(self.hands[uid]) if uid == viewer_id or self.finished else [],
                         "hand_count": len(self.hands[uid]), "score": self.scores[uid],
                         "score_delta": self.scores[uid], "stack": self.buy_in + self.scores[uid],
                         "role": "landlord" if uid == self.landlord_id else "farmer" if self.landlord_id else "unknown",
                         "bid": self.bids.get(uid)} for uid in self.player_ids],
            "legal_actions": self._legal_actions(viewer_id), "message": self.message,
            "winners": list(self.winners), "landlord_id": self.landlord_id,
            "highest_bid": self.highest_bid, "bids": dict(self.bids),
            "bid_options": [0] + list(range(self.highest_bid + 1, 4)),
            "bottom_cards": list(self.bottom_cards) if self.landlord_id else [],
            "last_play": ({**self.last_play, "cards": list(self.last_play["cards"])}
                          if self.last_play else None),
            "seat_actions": {uid: {**display, "cards": list(display["cards"])}
                             for uid, display in self.seat_actions.items()},
            "multiplier": self.multiplier, "deal_count": self.deal_count,
            "score_unit": self.SCORE_UNIT, "effective_stake": self.effective_stake,
            "base_stake": self.SCORE_UNIT,
            "buy_in": self.buy_in,
            "settlement": dict(self.scores) if self.finished else {},
        }

    def act(self, user_id: str, action: str, **payload) -> None:
        user_id = str(user_id)
        if action not in self._legal_actions(user_id):
            raise ValueError("当前不能执行此操作或尚未轮到你")
        if action == "bid":
            bid = payload.get("bid")
            if type(bid) is not int or bid not in [0] + list(range(self.highest_bid + 1, 4)):
                raise ValueError("叫分必须是不叫或高于当前叫分的 1 到 3 分")
            self.bids[user_id] = bid
            self.seat_actions[user_id] = {
                "action": "bid", "cards": [], "label": f"{bid}分" if bid else "不叫", "bid": bid,
            }
            self.highest_bid = max(self.highest_bid, bid)
            if bid == 3 or len(self.bids) == 3:
                if self.highest_bid:
                    self._begin_play()
                else:
                    self._deal()
            else:
                self.turn_index = (self.turn_index + 1) % 3
                self.message = "继续叫分。"
            return
        if action == "pass":
            self.seat_actions[user_id] = {"action": "pass", "cards": [], "label": "不出"}
            self.pass_count += 1
            self.turn_index = (self.turn_index + 1) % 3
            if self.pass_count == 2:
                self.turn_index = self.player_ids.index(self.last_play["user_id"])
                self.last_play = None
                self.last_pattern = None
                self.pass_count = 0
                self.message = "其余玩家均不出，上一位出牌者重新领出。"
            else:
                self.message = "不出。"
            return
        cards = payload.get("cards")
        if not isinstance(cards, list) or any(not isinstance(card, str) for card in cards):
            raise ValueError("出牌必须提供 cards 数组")
        pattern = classify_landlord_cards(cards)
        owned = Counter(self.hands[user_id])
        if any(count > owned[card] for card, count in Counter(cards).items()):
            raise ValueError("所选牌不在你的手牌中")
        if self.last_pattern and not landlord_beats(pattern, self.last_pattern):
            raise ValueError("须以同张数同牌型的更大牌，或炸弹、王炸压过上家")
        # 两家不出后保留桌面，直到重新领出合法牌才清空上一轮展示。
        if self.last_play is None:
            self.seat_actions.clear()
        self.seat_actions[user_id] = {
            "action": "play", "cards": list(cards), "label": LANDLORD_CARD_NAMES[pattern.kind],
        }
        for card in cards:
            self.hands[user_id].remove(card)
        self.last_pattern = pattern
        self.last_play = {"user_id": user_id, "cards": list(cards), **pattern.to_dict()}
        self.pass_count = 0
        self.play_counts[user_id] += 1
        if pattern.kind in ("bomb", "rocket"):
            self.multiplier *= 2
        self.message = f"打出{LANDLORD_CARD_NAMES[pattern.kind]}。"
        if not self.hands[user_id]:
            self._finish(user_id)
        else:
            self.turn_index = (self.turn_index + 1) % 3

    def _finish(self, first_empty: str):
        landlord_wins = first_empty == self.landlord_id
        farmers = [uid for uid in self.player_ids if uid != self.landlord_id]
        spring = (all(self.play_counts[uid] == 0 for uid in farmers) if landlord_wins
                  else self.play_counts[self.landlord_id] <= 1)
        if spring:
            self.multiplier *= 2
        # 返回规则理论净额；钱包仅从已带入灵石零和分配，不自动追加账户资金。
        stake = self.highest_bid * self.SCORE_UNIT * self.multiplier
        self.effective_stake = stake
        sign = 1 if landlord_wins else -1
        self.scores[self.landlord_id] = sign * 2 * stake
        for uid in farmers:
            self.scores[uid] = -sign * stake
        self.winners = [self.landlord_id] if landlord_wins else farmers
        self._finished = True
        self.phase = "finished"
        self.message = ("地主获胜" if landlord_wins else "农民获胜") + ("，春天翻倍。" if spring else "。")
        self.message += f"每名农民理论结算 {stake} 灵石，地主理论结算 {stake * 2} 灵石；实际以钱包结算为准。"

    @staticmethod
    def _hand_candidates(hand: list[str]):
        """按点数生成代表性组合；同点数的花色不影响斗地主决策。"""
        groups: dict[int, list[str]] = {}
        for card in hand:
            groups.setdefault(poker_value(card), []).append(card)
        values = sorted(groups)
        candidates = []
        for value in values:
            group = groups[value]
            candidates.extend(group[:count] for count in range(1, len(group) + 1))
            if len(group) >= 3:
                for wing in values:
                    if wing != value:
                        candidates.append(group[:3] + groups[wing][:1])
                        if len(groups[wing]) >= 2:
                            candidates.append(group[:3] + groups[wing][:2])
            if len(group) == 4:
                rest = [card for rank in values if rank != value for card in groups[rank]]
                for wings in combinations(rest, 2):
                    candidates.append(group + list(wings))
                pair_values = [rank for rank in values if rank != value and len(groups[rank]) >= 2]
                for wings in combinations(pair_values, 2):
                    candidates.append(group + groups[wings[0]][:2] + groups[wings[1]][:2])
        if 16 in groups and 17 in groups:
            candidates.append(groups[16] + groups[17])
        for width, minimum in ((1, 5), (2, 3), (3, 2)):
            for start in range(3, 15):
                sequence = []
                for value in range(start, 15):
                    if len(groups.get(value, [])) < width:
                        break
                    sequence.append(value)
                    if len(sequence) < minimum:
                        continue
                    core = [card for rank in sequence for card in groups[rank][:width]]
                    candidates.append(core)
                    if width == 3:
                        wings = [rank for rank in values if rank not in sequence]
                        for wing_width in (1, 2):
                            eligible = [rank for rank in wings if len(groups[rank]) >= wing_width]
                            for chosen in combinations(eligible, len(sequence)):
                                candidates.append(core + [card for rank in chosen for card in groups[rank][:wing_width]])
        seen = set()
        for cards in candidates:
            signature = tuple(sorted(poker_value(card) for card in cards))
            if signature in seen:
                continue
            seen.add(signature)
            try:
                pattern = classify_landlord_cards(cards)
            except ValueError:
                continue
            yield cards, pattern

    def _candidates(self, user_id: str):
        """仅使用自己的牌，筛选可以压过当前桌面牌型的动作。"""
        for cards, pattern in self._hand_candidates(self.hands[user_id]):
            if self.last_pattern is None or landlord_beats(pattern, self.last_pattern):
                yield cards, pattern

    @staticmethod
    def _hand_planner(hand: list[str], candidates):
        """有界分解剩余手牌，兼顾连牌和带牌；预算耗尽使用合法分解上界。"""
        hand_counts = Counter(poker_value(card) for card in hand)
        original = tuple(hand_counts[rank] for rank in range(3, 18))
        moves = []
        for cards, _ in candidates:
            counts = Counter(poker_value(card) for card in cards)
            moves.append((len(cards), tuple((rank - 3, count) for rank, count in counts.items())))
        moves.sort(key=lambda item: -item[0])
        by_rank = [[move for move in moves if any(index == rank for index, _ in move[1])]
                   for rank in range(15)]
        cache = {tuple([0] * 15): 0}

        def remainder(state, move):
            if any(state[index] < count for index, count in move):
                return None
            result = list(state)
            for index, count in move:
                result[index] -= count
            return tuple(result)

        def greedy(state):
            # 三种起点降低“最长顺子拆掉飞机”等单一贪心偏差。
            if not any(state):
                return 0
            result = sum(state)
            for skip in range(3):
                remaining = state
                turns = 0
                ignored = skip
                for _, move in moves:
                    next_state = remainder(remaining, move)
                    if next_state is None:
                        continue
                    if ignored:
                        ignored -= 1
                        continue
                    while next_state is not None:
                        turns += 1
                        remaining = next_state
                        next_state = remainder(remaining, move)
                result = min(result, turns + sum(remaining))
            return result

        def estimate(state, budget=0):
            if state in cache:
                return cache[state]
            best = greedy(state)
            if best <= 2 or not budget:
                return best
            # 固定分支与节点预算；仅分解自己手牌，绝不枚举真实对手暗牌。
            pending = [(state, 0)]
            visited = {}
            while pending and budget:
                current, depth = pending.pop()
                budget -= 1
                if depth + 1 >= best:
                    continue
                anchor = next(index for index, count in enumerate(current) if count)
                options = []
                for size, move in by_rank[anchor]:
                    next_state = remainder(current, move)
                    if next_state is None:
                        continue
                    if not any(next_state):
                        best = min(best, depth + 1)
                        continue
                    next_depth = depth + 1
                    if visited.get(next_state, 100) <= next_depth:
                        continue
                    visited[next_state] = next_depth
                    if next_depth + 1 < best:
                        options.append((size, next_state, next_depth))
                for _, next_state, next_depth in reversed(sorted(options, reverse=True)[:24]):
                    pending.append((next_state, next_depth))
            cache[state] = best
            return best

        return original, remainder, estimate

    def suggest_action(self, user_id: str) -> dict:
        user_id = str(user_id)
        if not self._legal_actions(user_id):
            raise ValueError("尚未轮到该玩家")
        if self.phase == "bidding":
            counts = Counter(poker_value(card) for card in self.hands[user_id])
            strength = counts[17] * 3 + counts[16] * 2 + counts[15] * 1.5 + counts[14] * 0.4
            strength += sum(count == 4 for count in counts.values()) * 3
            if counts[16] and counts[17]:
                strength += 2
            # 高牌控制力和成型大牌共同决定叫分，弱牌允许不叫。
            strength += sum(count >= 3 for rank, count in counts.items() if rank <= 14) * 0.4
            desired = 3 if strength >= 7 else 2 if strength >= 4.5 else 1 if strength >= 2.5 else 0
            bid = desired if desired > self.highest_bid else 0
            return {"action": "bid", "bid": bid}
        all_candidates = list(self._hand_candidates(self.hands[user_id]))
        candidates = [item for item in all_candidates
                      if self.last_pattern is None or landlord_beats(item[1], self.last_pattern)]
        if not candidates:
            return {"action": "pass"}
        # 可直接出完优先结束，避免为了保留炸弹而错过已经到手的胜利。
        finishing = [item for item in candidates if len(item[0]) == len(self.hands[user_id])]
        if finishing:
            return {"action": "play", "cards": list(finishing[0][0])}

        # 对手只读取公开剩余张数，队友协作也不能查看队友手牌内容。
        enemies = [uid for uid in self.player_ids
                   if uid != user_id and (user_id == self.landlord_id or uid == self.landlord_id)]
        enemy_counts = [len(self.hands[uid]) for uid in enemies]
        nearest_enemy = min(enemy_counts)
        teammate = next((uid for uid in self.player_ids
                         if uid != user_id and uid != self.landlord_id), None) if user_id != self.landlord_id else None
        next_player = self.player_ids[(self.turn_index + 1) % 3]
        teammate_leads = bool(self.last_play and teammate and self.last_play["user_id"] == teammate)
        must_block = (self.last_pattern is not None and self.last_pattern.size in enemy_counts
                      and self.last_pattern.kind in ("single", "pair"))
        if teammate_leads:
            cooperation = landlord_team_context(self.public_state(user_id), user_id)
            if not cooperation["must_block_landlord_finish"]:
                return {"action": "pass"}
            # 仅保留确实能封住至少一种可能末牌的压牌；无意义接管会堵住队友。
            protecting = [item for item in candidates
                          if landlord_blocks_finishing_reply(item[1], cooperation)]
            if not protecting:
                return {"action": "pass"}
            threats = [LandlordPattern(item["kind"], item["rank"], item["size"], item["chain"])
                       for item in cooperation["landlord_possible_finishing_patterns"]]
            # 紧急接管先尽量封住可能的末手，同等保护效果才优先保留炸弹。
            cards, _ = max(protecting, key=lambda item: (
                sum(not landlord_beats(threat, item[1]) for threat in threats),
                item[1].kind not in ("bomb", "rocket"), item[1].rank))
            return {"action": "play", "cards": list(cards)}

        if must_block:
            normal = [item for item in candidates if item[1].kind not in ("bomb", "rocket")]
            if normal:
                cards, _ = max(normal, key=lambda item: item[1].rank)
                return {"action": "play", "cards": list(cards)}
            cards, _ = min(candidates, key=lambda item: (item[1].kind == "rocket", item[1].rank))
            return {"action": "play", "cards": list(cards)}

        original, remainder, estimate = self._hand_planner(self.hands[user_id], all_candidates)
        hand_turns = estimate(original, budget=64)
        ranked = []
        for cards, pattern in candidates:
            used = Counter(poker_value(card) for card in cards)
            remaining = remainder(original, tuple((rank - 3, count) for rank, count in used.items()))
            split_cost = sum(9 if original[rank - 3] == 4 else 1.0 if original[rank - 3] == 3 else 0.5
                             for rank, count in used.items() if count < original[rank - 3])
            if original[13] and original[14] and bool(used[16]) != bool(used[17]):
                split_cost += 6
            control_cost = sum(max(0, rank - 13) * count * 0.35 for rank, count in used.items())
            bomb_cost = 5 if pattern.kind in ("bomb", "rocket") else 0
            position_cost = 0
            if self.last_play is None:
                if pattern.kind == "single" and 1 in enemy_counts:
                    position_cost += 12 + (17 - pattern.rank) * 1.5
                if pattern.kind == "pair" and 2 in enemy_counts:
                    position_cost += 8 + (15 - pattern.rank)
                if teammate and next_player == teammate and nearest_enemy > 2:
                    if len(self.hands[teammate]) == pattern.size and pattern.kind in ("single", "pair"):
                        position_cost -= max(0, 16 - pattern.rank) * 0.7
            cost = split_cost + control_cost + bomb_cost + position_cost + pattern.rank * 0.025 - len(cards) * 0.3
            ranked.append([estimate(remaining) * 7 + cost, cards, pattern, remaining, cost, split_cost])
        # 先做便宜排序，只对最有希望的 12 个动作增加固定深度预算。
        ranked.sort(key=lambda item: item[0])
        for item in ranked[:12]:
            item[0] = estimate(item[3], budget=48) * 7 + item[4]
        best = min(ranked, key=lambda item: item[0])
        _, cards, pattern, remaining, _, split_cost = best
        remaining_turns = estimate(remaining)
        if self.last_play and nearest_enemy > 3:
            # 不能因“有牌可压”拆炸弹、拆连牌或过早交掉最后的控制牌。
            if (pattern.kind in ("bomb", "rocket") and remaining_turns > 1
                    or split_cost >= 6
                    or remaining_turns > hand_turns
                    or remaining_turns >= hand_turns and split_cost >= 1.4
                    or pattern.rank >= 15 and self.last_pattern.rank <= 12 and remaining_turns >= 3):
                return {"action": "pass"}
        return {"action": "play", "cards": list(cards)}


MAHJONG_TILES = tuple(f"{suit}{number}" for suit in ("m", "p", "s") for number in range(1, 10)) + tuple(f"z{number}" for number in range(1, 8))
TILE_INDEX = {tile: index for index, tile in enumerate(MAHJONG_TILES)}


@lru_cache(maxsize=32768)
def _mahjong_groups(counts: tuple[int, ...]) -> bool:
    first = next((index for index, count in enumerate(counts) if count), None)
    if first is None:
        return True
    remaining = list(counts)
    if remaining[first] >= 3:
        remaining[first] -= 3
        if _mahjong_groups(tuple(remaining)):
            return True
        remaining[first] += 3
    if first < 27 and first % 9 <= 6 and remaining[first + 1] and remaining[first + 2]:
        for index in (first, first + 1, first + 2):
            remaining[index] -= 1
        if _mahjong_groups(tuple(remaining)):
            return True
    return False


def is_mahjong_win(tiles: list[str], meld_count: int = 0) -> bool:
    """四组一对或未副露的七个不同对子；不含十三幺及地方番种。"""
    if type(meld_count) is not int or not 0 <= meld_count <= 4 or len(tiles) != 14 - 3 * meld_count:
        return False
    if any(not isinstance(tile, str) or tile not in TILE_INDEX for tile in tiles):
        return False
    frequencies = Counter(tiles)
    if any(count > 4 for count in frequencies.values()):
        return False
    if meld_count == 0 and len(frequencies) == 7 and all(count == 2 for count in frequencies.values()):
        return True
    counts = tuple(frequencies[tile] for tile in MAHJONG_TILES)
    for index, count in enumerate(counts):
        if count >= 2:
            remainder = list(counts)
            remainder[index] -= 2
            if _mahjong_groups(tuple(remainder)):
                return True
    return False


class MahjongGame:
    """136 张四人麻将：单赢家、胡优先、吃碰杠、自摸与弃牌胡。"""

    SCORE_UNIT = 1

    def __init__(self, player_ids: list[str], seed: int | None = None, buy_in: int = 100, base_stake: int = 1):
        self.player_ids = _validate_players(player_ids, 4)
        self.buy_in = _validate_buy_in(buy_in)
        self.SCORE_UNIT = _validate_base_stake(base_stake, self.buy_in)
        self.random = random.Random(seed)
        self.wall = [tile for tile in MAHJONG_TILES for _ in range(4)]
        self.random.shuffle(self.wall)
        self.hands = {uid: self._sorted([self.wall.pop() for _ in range(13)]) for uid in self.player_ids}
        self.melds: dict[str, list[dict]] = {uid: [] for uid in self.player_ids}
        self.discards: dict[str, list[str]] = {uid: [] for uid in self.player_ids}
        self.scores = {uid: 0 for uid in self.player_ids}
        self.phase = "playing"
        self._finished = False
        self.winners: list[str] = []
        self._current_id = self.player_ids[0]
        self.last_discard: dict | None = None
        self.reaction_queue: list[tuple[str, list[str]]] = []
        self._can_self_win = True
        self.last_drawn_tile: str | None = None
        self.message = "东家先行；凑齐四组一对或七对即可胡牌。"
        self._draw(self._current_id)

    @staticmethod
    def _sorted(tiles):
        return sorted(tiles, key=TILE_INDEX.__getitem__)

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def current_player_id(self) -> str | None:
        return None if self.finished else self._current_id

    def _chow_options(self, user_id: str, tile: str) -> list[list[str]]:
        if tile[0] == "z":
            return []
        number = int(tile[1])
        counts = Counter(self.hands[user_id])
        options = []
        for low in range(max(1, number - 2), min(number, 7) + 1):
            sequence = [f"{tile[0]}{value}" for value in range(low, low + 3)]
            required = Counter(sequence)
            required[tile] -= 1
            if all(counts[value] >= count for value, count in required.items()):
                options.append(sequence)
        return options

    def _kong_options(self, user_id: str) -> list[str]:
        counts = Counter(self.hands[user_id])
        options = [tile for tile, count in counts.items() if count == 4]
        options += [meld["tiles"][0] for meld in self.melds[user_id]
                    if meld["type"] == "pung" and counts[meld["tiles"][0]] > 0]
        return self._sorted(options)

    def _legal_actions(self, user_id: str) -> list[str]:
        if self.finished or user_id != self.current_player_id:
            return []
        if self.phase == "reaction":
            return list(self.reaction_queue[0][1]) + ["pass"]
        actions = ["discard"]
        if self._can_self_win and is_mahjong_win(self.hands[user_id], len(self.melds[user_id])):
            actions.append("win")
        if self._kong_options(user_id):
            actions.append("kong")
        return actions

    def public_state(self, viewer_id: str) -> dict:
        viewer_id = str(viewer_id)
        players = []
        for seat, uid in enumerate(self.player_ids):
            visible = uid == viewer_id or self.finished
            melds = [{**meld, "tiles": list(meld["tiles"]) if visible or not meld.get("concealed") else [],
                      "tile_count": len(meld["tiles"])} for meld in self.melds[uid]]
            players.append({"user_id": uid, "hand": list(self.hands[uid]) if visible else [],
                            "hand_count": len(self.hands[uid]), "melds": melds,
                            "discards": list(self.discards[uid]), "score": self.scores[uid],
                            "score_delta": self.scores[uid], "stack": self.buy_in + self.scores[uid],
                            "seat_wind": ("东", "南", "西", "北")[seat]})
        actions = self._legal_actions(viewer_id)
        return {
            "phase": self.phase, "finished": self.finished,
            "current_player_id": self.current_player_id, "players": players,
            "legal_actions": actions, "message": self.message, "winners": list(self.winners),
            "score_unit": self.SCORE_UNIT, "settlement": dict(self.scores) if self.finished else {},
            "base_stake": self.SCORE_UNIT,
            "buy_in": self.buy_in,
            "wall_count": len(self.wall),
            "last_discard": dict(self.last_discard) if self.last_discard else None,
            "discards": {uid: list(tiles) for uid, tiles in self.discards.items()},
            "chow_options": self._chow_options(viewer_id, self.last_discard["tile"]) if "chow" in actions else [],
            "kong_options": ([self.last_discard["tile"]] if self.phase == "reaction" else self._kong_options(viewer_id)) if "kong" in actions else [],
            "drawn_tile": self.last_drawn_tile if viewer_id == self.current_player_id and self.phase == "playing" else None,
        }

    def _draw(self, user_id: str, replacement: bool = False):
        if not self.wall:
            self._finished = True
            self.phase = "finished"
            self.message = "牌墙摸尽，本局流局，保证金原额返还。"
            self.last_drawn_tile = None
            return
        self.last_drawn_tile = self.wall.pop(0) if replacement else self.wall.pop()
        self.hands[user_id].append(self.last_drawn_tile)
        self.hands[user_id] = self._sorted(self.hands[user_id])
        self._current_id = user_id
        self.phase = "playing"
        self._can_self_win = True

    def _next_after(self, user_id: str) -> str:
        return self.player_ids[(self.player_ids.index(user_id) + 1) % 4]

    def _begin_reactions(self, discarder: str, tile: str):
        start = self.player_ids.index(discarder)
        ordered = [self.player_ids[(start + distance) % 4] for distance in range(1, 4)]
        win_queue, meld_queue, chow_queue = [], [], []
        for uid in ordered:
            if is_mahjong_win(self.hands[uid] + [tile], len(self.melds[uid])):
                win_queue.append((uid, ["win"]))
            count = self.hands[uid].count(tile)
            actions = (["kong"] if count >= 3 else []) + (["pung"] if count >= 2 else [])
            if actions:
                meld_queue.append((uid, actions))
            if uid == ordered[0] and self._chow_options(uid, tile):
                chow_queue.append((uid, ["chow"]))
        self.reaction_queue = win_queue + meld_queue + chow_queue
        if self.reaction_queue:
            self._current_id = self.reaction_queue[0][0]
            self.phase = "reaction"
            self.message = "等待胡、碰杠或下家吃牌响应；胡牌优先。"
        else:
            self._draw(self._next_after(discarder))

    def _finish_win(self, winner: str, discarder: str | None = None):
        self.winners = [winner]
        if discarder is None:
            for uid in self.player_ids:
                self.scores[uid] = 3 * self.SCORE_UNIT if uid == winner else -self.SCORE_UNIT
            self.message = "自摸胡牌：赢家 +3 灵石，其余各 -1 灵石。"
        else:
            self.scores[winner] = 3 * self.SCORE_UNIT
            self.scores[discarder] = -3 * self.SCORE_UNIT
            self.message = "弃牌胡：赢家 +3 灵石，放铳者 -3 灵石。"
        self._finished = True
        self.phase = "finished"
        self.reaction_queue = []

    def act(self, user_id: str, action: str, **payload) -> None:
        user_id = str(user_id)
        if action not in self._legal_actions(user_id):
            raise ValueError("当前不能执行此操作或尚未轮到你")
        if self.phase == "reaction":
            self._react(user_id, action, payload)
            return
        if action == "win":
            self._finish_win(user_id)
            return
        if action == "kong":
            tile = payload.get("tile")
            if tile not in self._kong_options(user_id):
                raise ValueError("请选择四张相同牌暗杠，或已有碰牌的第四张补杠")
            if self.hands[user_id].count(tile) == 4:
                for _ in range(4):
                    self.hands[user_id].remove(tile)
                self.melds[user_id].append({"type": "kong", "tiles": [tile] * 4, "concealed": True})
            else:
                self.hands[user_id].remove(tile)
                meld = next(meld for meld in self.melds[user_id] if meld["type"] == "pung" and meld["tiles"][0] == tile)
                meld["type"] = "kong"
                meld["tiles"].append(tile)
            self.message = "杠牌后从牌墙尾补牌。"
            self._draw(user_id, replacement=True)
            return
        tile = payload.get("tile")
        if not isinstance(tile, str) or tile not in self.hands[user_id]:
            raise ValueError("请选择手牌中的一张牌打出")
        self.hands[user_id].remove(tile)
        self.discards[user_id].append(tile)
        self.last_discard = {"user_id": user_id, "tile": tile}
        self.last_drawn_tile = None
        self._can_self_win = False
        self._begin_reactions(user_id, tile)

    def _react(self, user_id: str, action: str, payload: dict):
        discarder = self.last_discard["user_id"]
        tile = self.last_discard["tile"]
        if action == "pass":
            self.reaction_queue.pop(0)
            if self.reaction_queue:
                self._current_id = self.reaction_queue[0][0]
            else:
                self._draw(self._next_after(discarder))
            return
        if payload.get("tile") is not None and payload["tile"] != tile:
            raise ValueError("响应牌必须是刚打出的那一张")
        if action == "win":
            self.hands[user_id].append(tile)
            self.hands[user_id] = self._sorted(self.hands[user_id])
            self.discards[discarder].pop()
            self._finish_win(user_id, discarder)
            return
        if action == "chow":
            tiles = payload.get("tiles")
            if not isinstance(tiles, list) or any(not isinstance(value, str) or value not in TILE_INDEX for value in tiles):
                raise ValueError("吃牌须选择完整三张顺子，或手中两张配牌")
            sequence = list(tiles) + [tile] if len(tiles) == 2 else list(tiles)
            sequence = self._sorted(sequence)
            if sequence not in self._chow_options(user_id, tile):
                raise ValueError("所选牌无法与弃牌组成同花色顺子")
            required = list(sequence)
            required.remove(tile)
        else:
            required = [tile] * (3 if action == "kong" else 2)
            sequence = [tile] + required
        # 所有输入在此之前已经验证，以下统一提交吃碰杠，避免失败请求修改手牌。
        for value in required:
            self.hands[user_id].remove(value)
        self.discards[discarder].pop()
        self.melds[user_id].append({"type": action, "tiles": sequence, "concealed": False,
                                    "from_user_id": discarder})
        self.reaction_queue = []
        self._current_id = user_id
        self.phase = "playing"
        self._can_self_win = False
        self.last_drawn_tile = None
        self.message = "吃碰后请打出一张牌。" if action != "kong" else "明杠后补牌。"
        if action == "kong":
            self._draw(user_id, replacement=True)

    def suggest_action(self, user_id: str) -> dict:
        user_id = str(user_id)
        actions = self._legal_actions(user_id)
        if not actions:
            raise ValueError("尚未轮到该玩家")
        if "win" in actions:
            return {"action": "win"}
        if "kong" in actions:
            tile = self.last_discard["tile"] if self.phase == "reaction" else self._kong_options(user_id)[0]
            return {"action": "kong", "tile": tile}
        if "pung" in actions:
            return {"action": "pung", "tile": self.last_discard["tile"]}
        if "chow" in actions:
            return {"action": "chow", "tiles": self._chow_options(user_id, self.last_discard["tile"])[0]}
        if self.phase == "reaction":
            return {"action": "pass"}
        counts = Counter(self.hands[user_id])

        def usefulness(tile):
            count = counts[tile]
            value = 5 * (count - 1)
            if tile[0] != "z":
                number = int(tile[1])
                for offset, weight in ((-2, 1), (-1, 3), (1, 3), (2, 1)):
                    if 1 <= number + offset <= 9:
                        value += weight * min(counts[f"{tile[0]}{number + offset}"], 1)
            return value, -TILE_INDEX[tile]

        tile = min(counts, key=usefulness)
        return {"action": "discard", "tile": tile}
