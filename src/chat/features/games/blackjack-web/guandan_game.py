"""四人掼蛋：双副牌、红桃级牌通配、对家升级与自动贡还贡。"""

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
import random


SUITS = ("Club", "Diamond", "Heart", "Spade")
RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
VALUES = {rank: index + 2 for index, rank in enumerate(RANKS)}
NAMES = {"single": "单张", "pair": "对子", "triple": "三张", "full_house": "三带二",
         "straight": "顺子", "pair_straight": "三连对", "triple_straight": "钢板",
         "bomb": "炸弹", "straight_flush": "同花顺", "rocket": "四王炸"}
DECK = tuple(f"{suit}{rank}#{pack}" for pack in (0, 1) for suit in SUITS for rank in RANKS) + tuple(
    f"{joker}#{pack}" for pack in (0, 1) for joker in ("JokerSmall", "JokerBig"))
CARD_PARTS = {card: (card.split("#")[0][:-len(rank)], VALUES[rank])
              for card in DECK for rank in RANKS if card.split("#")[0] in {f"{suit}{rank}" for suit in SUITS}}
CARD_PARTS.update({f"JokerSmall#{pack}": ("", 16) for pack in (0, 1)})
CARD_PARTS.update({f"JokerBig#{pack}": ("", 17) for pack in (0, 1)})


def card_parts(card):
    if not isinstance(card, str) or card not in CARD_PARTS:
        raise ValueError("无效的掼蛋牌ID")
    return CARD_PARTS[card]


def power(rank, level):
    return 15 if rank == VALUES[level] else rank


@dataclass(frozen=True)
class GuandanPattern:
    kind: str
    rank: int
    size: int
    detail: str = ""

    @property
    def combo(self):
        return f"{self.kind}:{self.rank}:{self.size}:{self.detail}"

    def to_dict(self):
        return {"kind": self.kind, "name": NAMES[self.kind], "rank": self.rank,
                "size": self.size, "combo": self.combo}


def bomb_order(pattern):
    if pattern.kind == "rocket":
        return (20, 0)
    if pattern.kind == "straight_flush":
        return (5.5, pattern.rank)
    if pattern.kind == "bomb":
        return (pattern.size, pattern.rank)
    return (0, pattern.rank)


def guandan_beats(candidate, previous):
    a, b = bomb_order(candidate), bomb_order(previous)
    if a[0] or b[0]:
        return a[0] > 0 and a > b
    return candidate.kind == previous.kind and candidate.size == previous.size and candidate.rank > previous.rank


def sequences(width):
    yield [14] + list(range(2, width + 1))
    for low in range(2, 16 - width):
        yield list(range(low, low + width))


@lru_cache(maxsize=8192)
def _classify(signature, level):
    parts = [card_parts(card) for card in signature]
    wild_count = sum(part == ("Heart", VALUES[level]) for part in parts)
    natural = [part for part in parts if part != ("Heart", VALUES[level])]
    counts = Counter(rank for _, rank in natural)
    size = len(signature)
    found = []
    if size == 1:
        return (GuandanPattern("single", power(parts[0][1], level), 1),)
    if size == 4 and Counter(rank for _, rank in parts) == {16: 2, 17: 2}:
        return (GuandanPattern("rocket", 17, 4),)
    if len(counts) <= 1:
        ranks = list(counts) or [VALUES[level]]
        rank = ranks[0]
        if (rank < 16 or wild_count == 0) and (rank < 16 or size == 2):
            kind = {2: "pair", 3: "triple"}.get(size, "bomb" if 4 <= size <= 10 else None)
            if kind:
                found.append(GuandanPattern(kind, power(rank, level), size))
    if size == 5:
        for triple in range(2, 15):
            for pair in range(2, 18):
                if pair == triple or pair == 15:
                    continue
                needs = {triple: 3, pair: 2}
                if any(count > needs.get(rank, 0) for rank, count in counts.items()):
                    continue
                if pair >= 16 and counts[pair] != 2:
                    continue
                if sum(max(0, count - counts[rank]) for rank, count in needs.items()) == wild_count:
                    found.append(GuandanPattern("full_house", power(triple, level), 5, str(pair)))
    for kind, width, copies in (("straight", 5, 1), ("pair_straight", 3, 2), ("triple_straight", 2, 3)):
        if size != width * copies:
            continue
        for seq in sequences(width):
            if any(rank not in seq or count > copies for rank, count in counts.items()):
                continue
            if sum(copies - counts[rank] for rank in seq) != wild_count:
                continue
            rank = seq[-1]
            flush = kind == "straight" and len({suit for suit, _ in natural}) <= 1
            found.append(GuandanPattern("straight_flush" if flush else kind, rank, size,
                                        next(iter({suit for suit, _ in natural}), "Heart") if flush else ""))
            if flush and wild_count:
                # 通配可声明为其他花色，保留普通顺子和同花顺两种合法解释。
                found.append(GuandanPattern("straight", rank, size))
    return tuple(sorted(set(found), key=lambda p: (bomb_order(p)[0], p.rank, p.kind, p.detail)))


def classify_guandan_cards(cards, level="2"):
    if level not in RANKS or not isinstance(cards, list) or not 1 <= len(cards) <= 10:
        raise ValueError("请选择1至10张有效掼蛋牌")
    for card in cards:
        card_parts(card)
    if len(set(cards)) != len(cards):
        raise ValueError("同一张牌不能重复使用")
    patterns = _classify(tuple(sorted(cards)), level)
    if not patterns:
        raise ValueError("不能组成合法的掼蛋牌型")
    return patterns


def guandan_hand_analysis(hand, level, cards):
    """只解释自己出牌后的结构变化，不推测其他座位的暗牌。"""
    counts = Counter(card_parts(card)[1] for card in hand)
    used = Counter(card_parts(card)[1] for card in cards)
    remaining = list(hand)
    for card in cards:
        remaining.remove(card)
    splits = [{"rank": rank, "before": counts[rank], "used": number,
               "remaining": counts[rank] - number}
              for rank, number in sorted(used.items()) if number < counts[rank]]
    return {
        "remaining_hand": remaining, "remaining_count": len(remaining), "wins_immediately": not remaining,
        "wildcards_used": sum(card_parts(card) == ("Heart", VALUES[level]) for card in cards),
        "splits_same_rank_groups": splits,
        "breaks_natural_bombs": [row for row in splits if row["rank"] < 16 and row["before"] >= 4
                                  and row["used"] < 4 and row["remaining"] < 4],
        "splits_four_jokers": counts[16] == counts[17] == 2 and 0 < used[16] + used[17] < 4,
    }


def guandan_hand_plan(hand, level):
    """复用一份自己的牌型模板，返回每个候选出牌后的有界规划查询器。"""
    hand = list(hand)
    if level not in RANKS or not 1 <= len(hand) <= 27 or len(set(hand)) != len(hand):
        raise ValueError("掼蛋规划需要1至27张不重复的自己手牌")
    for card in hand:
        card_parts(card)
    candidates = list(GuandanGame._hand_candidates(hand, level))
    original, _, _, estimate = GuandanGame._hand_planner(hand, candidates, level)
    current = estimate(original, budget=40)
    chains = [(cards, pattern) for cards, pattern in candidates
              if pattern.kind in ("straight", "straight_flush", "pair_straight", "triple_straight")]
    cache = {}

    def analyze(cards):
        signature = tuple(sorted(cards))
        if signature in cache:
            return deepcopy(cache[signature])
        if len(set(cards)) != len(cards) or not set(cards) <= set(hand):
            raise ValueError("规划候选必须来自自己的手牌")
        remaining_cards = [card for card in hand if card not in cards]
        if not remaining_cards:
            remaining_candidates, remaining_plays = [], 0
        elif not cards:
            remaining_candidates, remaining_plays = candidates, current
        else:
            # 出掉自然牌后，通配牌可能形成新的组合；不能沿用出牌前模板漏掉这条路线。
            remaining_candidates = list(GuandanGame._hand_candidates(remaining_cards, level))
            rest, _, _, estimate_remaining = GuandanGame._hand_planner(remaining_cards, remaining_candidates, level)
            remaining_plays = estimate_remaining(rest, budget=24)
        retained = {(pattern.kind, pattern.rank, pattern.size) for _, pattern in remaining_candidates}
        broken = {}
        for chain_cards, pattern in chains:
            key = (pattern.kind, pattern.rank, pattern.size)
            if key not in retained and not set(chain_cards) <= set(cards):
                # 同点副本或通配牌仍可继续组成时保留，不按牌ID误报拆组。
                broken.setdefault(key, {"kind": pattern.kind, "rank": pattern.rank, "size": pattern.size})
        result = {
            "estimated_current_plays": current,
            "estimated_remaining_plays": remaining_plays,
            "estimated_total_plays_after_action": remaining_plays + bool(cards),
            "estimated_extra_plays": max(0, remaining_plays + bool(cards) - current),
            "broken_sequence_groups": list(broken.values()),
            "sequence_split_worsens_plan": bool(broken and remaining_plays + bool(cards) > current),
        }
        cache[signature] = result
        return deepcopy(result)

    return analyze


def guandan_finishing_threats(visible_cards, level, previous, remaining_count):
    """枚举公开信息尚未排除的末手牌型，不将未知牌归给任一玩家。"""
    if not 1 <= remaining_count <= 10:
        return []
    visible = set(visible_cards)
    unseen = [card_parts(card) for card in DECK if card not in visible]
    wild_count = sum(part == ("Heart", VALUES[level]) for part in unseen)
    natural = [part for part in unseen if part != ("Heart", VALUES[level])]
    counts = Counter(rank for _, rank in natural)
    found = set()

    def available(needs, suit=None):
        source = counts if suit is None else Counter(rank for color, rank in natural if color == suit)
        if any(rank >= 16 and source[rank] < number for rank, number in needs.items()):
            return False
        return sum(max(0, number - source[rank]) for rank, number in needs.items()) <= wild_count

    def add(kind, rank, size):
        pattern = GuandanPattern(kind, rank, size)
        if size == remaining_count and guandan_beats(pattern, previous):
            found.add(pattern)

    if remaining_count == 1:
        for rank in list(range(2, 15)) + [16, 17]:
            if counts[rank] or rank == VALUES[level] and wild_count:
                add("single", power(rank, level), 1)
    for size, kind in ((2, "pair"), (3, "triple")):
        if remaining_count == size:
            for rank in list(range(2, 15)) + ([16, 17] if size == 2 else []):
                # 纯双通配按引擎只解释为级牌对子，不能虚构其他点数的对子。
                if (counts[rank] or rank == VALUES[level]) and available({rank: size}):
                    add(kind, power(rank, level), size)
    if remaining_count == 5:
        for triple in range(2, 15):
            if any(pair != triple and available({triple: 3, pair: 2})
                   for pair in list(range(2, 15)) + [16, 17]):
                add("full_house", power(triple, level), 5)
    for kind, width, copies in (("straight", 5, 1), ("pair_straight", 3, 2), ("triple_straight", 2, 3)):
        if remaining_count != width * copies:
            continue
        for sequence in sequences(width):
            needs = {rank: copies for rank in sequence}
            if available(needs):
                add(kind, sequence[-1], remaining_count)
            if kind == "straight" and any(available(needs, suit) for suit in SUITS):
                add("straight_flush", sequence[-1], 5)
    if 4 <= remaining_count <= 10:
        for rank in range(2, 15):
            if available({rank: remaining_count}):
                add("bomb", power(rank, level), remaining_count)
    if remaining_count == 4 and counts[16] == counts[17] == 2:
        add("rocket", 17, 4)
    return sorted(found, key=lambda pattern: (bomb_order(pattern), pattern.kind))


class GuandanGame:
    """采用升1/2/3级、回贡10及以下、打A头游且队友非末游过关的房规。"""

    def __init__(self, player_ids, seed=None, buy_in=100, base_stake=1, match_state=None):
        self.player_ids = [str(uid) for uid in player_ids]
        if len(self.player_ids) != 4 or len(set(self.player_ids)) != 4:
            raise ValueError("掼蛋需要四位不同玩家")
        if type(buy_in) is not int or not 100 <= buy_in <= 2**53 - 1 or type(base_stake) is not int or not 1 <= base_stake <= buy_in // 10:
            raise ValueError("掼蛋底分或带入金额不合法")
        self.buy_in, self.base_stake = buy_in, base_stake
        self.random = random.Random(seed)
        self.teams = {uid: index % 2 for index, uid in enumerate(self.player_ids)}
        prior = match_state if match_state and match_state.get("player_ids") == self.player_ids and not match_state.get("match_finished") else None
        self.team_levels = list(prior["team_levels"]) if prior else ["2", "2"]
        self.declarer_team = prior["declarer_team"] if prior else 0
        self.level = self.team_levels[self.declarer_team]
        self.match_finished, self.match_winner_team = False, None
        self.phase, self.finished = "playing", False
        self.finish_order, self.winners, self.tribute_events = [], [], []
        self.scores = {uid: 0 for uid in self.player_ids}
        self.last_play, self.last_pattern = None, None
        self.seat_actions, self.passed = {}, set()
        self.level_gain, self.message = 0, "四人对家掼蛋，本局统一打" + self.level + "。"
        deck = list(DECK)
        self.random.shuffle(deck)
        self.hands = {uid: self._sort(deck[index * 27:(index + 1) * 27]) for index, uid in enumerate(self.player_ids)}
        self.turn_index = self.random.randrange(4)
        if prior:
            self._tribute(prior["finish_order"])

    def _sort(self, cards):
        return sorted(cards, key=lambda card: (power(card_parts(card)[1], self.level), card))

    @property
    def current_player_id(self):
        return None if self.finished else self.player_ids[self.turn_index]

    def _partner(self, uid):
        return self.player_ids[(self.player_ids.index(uid) + 2) % 4]

    def _next(self, uid):
        index = self.player_ids.index(uid)
        for step in range(1, 5):
            candidate = self.player_ids[(index + step) % 4]
            if self.hands[candidate]:
                return candidate
        raise ValueError("没有剩余玩家")

    def _tribute(self, finish_order):
        first, second, _, last = finish_order
        double = self.teams[first] == self.teams[second]
        donors = finish_order[2:] if double else [last]
        if sum(sum(card_parts(card)[1] == 17 for card in self.hands[uid]) for uid in donors) == 2:
            self.tribute_events.append({"kind": "resist", "user_ids": donors, "automatic": True})
            self.turn_index = self.player_ids.index(first)
            return
        gifts = []
        for donor in donors:
            candidates = [card for card in self.hands[donor] if card_parts(card) != ("Heart", VALUES[self.level])]
            card = max(candidates, key=lambda item: (power(card_parts(item)[1], self.level), item))
            gifts.append((donor, card))
        gifts.sort(key=lambda item: (-power(card_parts(item[1])[1], self.level), self.player_ids.index(item[0])))
        self.turn_index = self.player_ids.index(gifts[0][0])
        for index, (donor, tribute) in enumerate(gifts):
            receiver = finish_order[index] if double else first
            original = self.hands[receiver]
            eligible = [card for card in original if card_parts(card)[1] <= 10 and card_parts(card)[1] != VALUES[self.level]]
            # 无10及以下非级牌时，以手中最小的非通配牌还贡，明确记录降级规则。
            fallback = not eligible
            eligible = eligible or [card for card in original if card_parts(card) != ("Heart", VALUES[self.level])]
            returned = min(eligible, key=lambda item: (power(card_parts(item)[1], self.level), item))
            self.hands[donor].remove(tribute)
            self.hands[receiver].remove(returned)
            self.hands[donor].append(returned)
            self.hands[receiver].append(tribute)
            self.tribute_events.extend([
                {"kind": "tribute", "user_id": donor, "target_id": receiver, "card": tribute, "automatic": True},
                {"kind": "return", "user_id": receiver, "target_id": donor, "card": returned, "automatic": True, "fallback": fallback},
            ])
        self.hands = {uid: self._sort(hand) for uid, hand in self.hands.items()}

    def _legal_actions(self, uid):
        if uid != self.current_player_id:
            return []
        return ["play", "pass"] if self.last_play else ["play"]

    @staticmethod
    def _templates(hand, level):
        """按点数和有限模板造牌，不枚举27张手牌的幂集。"""
        wilds = [card for card in hand if card_parts(card) == ("Heart", VALUES[level])]
        groups = {}
        for card in hand:
            if card not in wilds:
                groups.setdefault(card_parts(card)[1], []).append(card)

        def fill(needs, suit=None):
            result, missing = [], 0
            for rank, count in needs.items():
                eligible = [card for card in groups.get(rank, []) if suit is None or card_parts(card)[0] == suit]
                chosen = eligible[:count]
                if rank >= 16 and len(chosen) < count:
                    return None
                result.extend(chosen)
                missing += count - len(chosen)
            if missing > len(wilds):
                return None
            return result + wilds[:missing]

        for card in hand:
            yield [card]
        for rank in list(range(2, 15)) + [16, 17]:
            for size in range(2, min(10, len(groups.get(rank, [])) + (len(wilds) if rank < 16 else 0)) + 1):
                if rank >= 16 and size != 2:
                    continue
                cards = fill({rank: size})
                if cards:
                    yield cards
        if len(wilds) == 2:
            yield wilds
        jokers = [card for card in hand if card_parts(card)[1] >= 16]
        if len(jokers) == 4:
            yield jokers
        for triple in range(2, 15):
            if len(groups.get(triple, [])) + len(wilds) < 3:
                continue
            for pair in list(range(2, 15)) + [16, 17]:
                if pair != triple:
                    cards = fill({triple: 3, pair: 2})
                    if cards:
                        yield cards
        for width, count in ((5, 1), (3, 2), (2, 3)):
            for seq in sequences(width):
                cards = fill({rank: count for rank in seq})
                if cards:
                    yield cards
                if width == 5:
                    for suit in SUITS:
                        cards = fill({rank: 1 for rank in seq}, suit)
                        if cards:
                            yield cards

    @staticmethod
    def _hand_candidates(hand, level):
        seen = set()
        for cards in GuandanGame._templates(hand, level):
            signature = tuple(sorted(cards))
            if signature in seen:
                continue
            seen.add(signature)
            for pattern in classify_guandan_cards(list(signature), level):
                yield list(signature), pattern

    def _candidates(self, uid):
        for cards, pattern in self._hand_candidates(self.hands[uid], self.level):
            if not self.last_pattern or guandan_beats(pattern, self.last_pattern):
                yield cards, pattern

    def _play_options(self, uid, limit=80):
        """先看完整有界模板，再均衡抽取，避免后枚举的顺子、钢板被截断。"""
        candidates = list(self._candidates(uid))
        groups = {}
        selected, signatures = [], set()

        def append(item):
            cards, pattern = item
            signature = (tuple(cards), pattern.combo)
            if signature not in signatures and len(selected) < limit:
                signatures.add(signature)
                selected.append({"cards": cards, **pattern.to_dict()})

        for item in candidates:
            cards, pattern = item
            if len(cards) == len(self.hands[uid]):
                append(item)
            groups.setdefault((pattern.kind, pattern.size), []).append(item)
        for group in groups.values():
            group.sort(key=lambda item: (item[1].rank, item[1].detail, item[0]))
        take_high = False
        while len(selected) < limit and any(groups.values()):
            for group in groups.values():
                if group:
                    append(group.pop(-1 if take_high else 0))
            take_high = not take_high
        return selected

    @staticmethod
    def _hand_planner(hand, candidates, level):
        """按点数和独立通配牌计数估计手数；固定预算，不枚举对手牌。"""
        def move(cards):
            counts = Counter(16 if card_parts(card) == ("Heart", VALUES[level])
                             else card_parts(card)[1] - 2 for card in cards)
            return tuple(sorted(counts.items()))

        original = [0] * 17
        for index, number in move(hand):
            original[index] = number
        original = tuple(original)
        moves = sorted({(len(cards), move(cards)) for cards, _ in candidates}, reverse=True)
        by_index = [[item for item in moves if any(index == current for index, _ in item[1])]
                    for current in range(17)]
        cache = {tuple([0] * 17): 0}

        def remainder(state, action):
            if any(state[index] < number for index, number in action):
                return None
            result = list(state)
            for index, number in action:
                result[index] -= number
            return tuple(result)

        def estimate(state, budget=0):
            if state in cache and (not budget or cache[state] <= 2):
                return cache[state]
            best = cache.get(state, sum(state))
            # 两种贪心起点避免只追求最长组合，把另一个完整组拆散。
            for skip in range(2):
                rest, turns, ignored = state, 0, skip
                for _, action in moves:
                    next_state = remainder(rest, action)
                    if next_state is None:
                        continue
                    if ignored:
                        ignored -= 1
                        continue
                    while next_state is not None:
                        rest, turns = next_state, turns + 1
                        next_state = remainder(rest, action)
                best = min(best, turns + sum(rest))
            pending, visited = [(state, 0)], {}
            while pending and budget and best > 2:
                current, depth = pending.pop()
                budget -= 1
                if depth + 1 >= best:
                    continue
                anchor = next(index for index, count in enumerate(current) if count)
                branches = []
                for size, action in by_index[anchor]:
                    next_state = remainder(current, action)
                    if next_state is None:
                        continue
                    if not any(next_state):
                        best = min(best, depth + 1)
                    elif visited.get(next_state, 100) > depth + 1:
                        visited[next_state] = depth + 1
                        branches.append((size, next_state, depth + 1))
                for _, rest, depth in reversed(sorted(branches, reverse=True)[:16]):
                    pending.append((rest, depth))
            cache[state] = best
            return best

        return original, move, remainder, estimate

    def suggest_action(self, user_id):
        uid = str(user_id)
        if not self._legal_actions(uid):
            raise ValueError("尚未轮到该玩家")
        hand = self.hands[uid]
        all_candidates = list(self._hand_candidates(hand, self.level))
        candidates = [item for item in all_candidates
                      if not self.last_pattern or guandan_beats(item[1], self.last_pattern)]
        finishing = [item for item in candidates if len(item[0]) == len(self.hands[uid])]
        if finishing:
            cards, pattern = min(finishing, key=lambda item: bomb_order(item[1]))
            return {"action": "play", "cards": cards, "combo": pattern.combo}
        if not candidates:
            return {"action": "pass"}
        if self.last_play and self.teams[self.last_play["user_id"]] == self.teams[uid]:
            following = self._next(uid)
            if self.teams[following] != self.teams[uid] and following not in self.passed:
                visible = set(hand) | set(self.last_play["cards"])
                for event in self.seat_actions.values():
                    visible.update(event["cards"])
                threats = guandan_finishing_threats(visible, self.level, self.last_pattern,
                                                   len(self.hands[following]))
                blockers = [(cards, pattern) for cards, pattern in candidates
                            if any(not guandan_beats(threat, pattern) for threat in threats)]
                if blockers:
                    # 只在下个敌手存在末手接走可能时替队友拦截；无法封堵就继续让牌。
                    def blocking_key(item):
                        _, pattern = item
                        covered = sum(not guandan_beats(threat, pattern) for threat in threats)
                        return (-covered, bool(bomb_order(pattern)[0]), bomb_order(pattern))
                    cards, pattern = min(blockers, key=blocking_key)
                    return {"action": "play", "cards": cards, "combo": pattern.combo}
            return {"action": "pass"}
        enemies = [other for other in self.player_ids
                   if self.teams[other] != self.teams[uid] and self.hands[other]]
        enemy_counts = [len(self.hands[other]) for other in enemies]
        waiting_counts = [len(self.hands[other]) for other in enemies if other not in self.passed]
        danger = min(enemy_counts) <= 2
        must_block = (self.last_pattern and self.last_pattern.kind in ("single", "pair")
                      and self.last_pattern.size in waiting_counts)
        if must_block:
            normal = [item for item in candidates if bomb_order(item[1])[0] == 0]
            if normal:
                # 对手已报单/报双时允许拆组，用本方最大同型牌守住这一轮。
                cards, pattern = max(normal, key=lambda item: item[1].rank)
                return {"action": "play", "cards": cards, "combo": pattern.combo}
        original, move, remainder, estimate = self._hand_planner(hand, all_candidates, self.level)
        current_turns = estimate(original, budget=40)
        ranked = []
        for cards, pattern in candidates:
            facts = guandan_hand_analysis(hand, self.level, cards)
            split = sum(14 if row in facts["breaks_natural_bombs"] else 2 if row["before"] >= 4 else 1.2
                        for row in facts["splits_same_rank_groups"])
            split += 14 if facts["splits_four_jokers"] else 0
            rest = remainder(original, move(cards))
            bomb_cost = (1 if danger else 3) if bomb_order(pattern)[0] else 0
            lead_cost = 0
            if not self.last_play and pattern.kind in ("single", "pair") and pattern.size in enemy_counts:
                lead_cost = 10 + (17 - pattern.rank)
            cost = split + bomb_cost + facts["wildcards_used"] * 1.5 + lead_cost + pattern.rank * 0.08 - len(cards) * 0.15
            ranked.append([estimate(rest) * 8 + cost, cards, pattern, rest, cost, split])
        ranked.sort(key=lambda item: item[0])
        for item in ranked[:8]:
            item[0] = estimate(item[3], budget=24) * 8 + item[4]
        _, cards, pattern, rest, _, split = min(ranked, key=lambda item: item[0])
        if self.last_play and not danger and estimate(rest) > 1:
            if (split >= 10 or estimate(rest) > current_turns
                    or bomb_order(pattern)[0] and estimate(rest) >= current_turns):
                return {"action": "pass"}
        return {"action": "play", "cards": cards, "combo": pattern.combo}

    choose_bot_action = suggest_action

    def act(self, user_id, action, **payload):
        uid = str(user_id)
        if action not in self._legal_actions(uid):
            raise ValueError("当前不能执行此动作")
        if action == "pass":
            self.passed.add(uid)
            self.seat_actions[uid] = {"action": "pass", "cards": [], "label": "不出"}
            waiting = {other for other in self.player_ids if self.hands[other] and other != self.last_play["user_id"]}
            if waiting <= self.passed:
                leader = self.last_play["user_id"]
                if not self.hands[leader]:
                    leader = self._partner(leader)
                following_partner = not self.hands[self.last_play["user_id"]]
                self.turn_index = self.player_ids.index(leader)
                self.last_play, self.last_pattern, self.passed = None, None, set()
                self.message = "本轮结束，由对家接风。" if following_partner else "本轮结束，重新领出。"
            else:
                self.turn_index = self.player_ids.index(self._next(uid))
            return
        cards = payload.get("cards")
        patterns = classify_guandan_cards(cards, self.level)
        if not set(cards) <= set(self.hands[uid]):
            raise ValueError("所选牌不在手中")
        patterns = [pattern for pattern in patterns if (not self.last_pattern or guandan_beats(pattern, self.last_pattern))
                    and (payload.get("combo") is None or pattern.combo == payload["combo"])]
        if not patterns:
            raise ValueError("牌型不能压过上家或指定解释不合法")
        pattern = patterns[0]
        if self.last_play is None:
            self.seat_actions.clear()
        for card in cards:
            self.hands[uid].remove(card)
        self.last_pattern = pattern
        self.last_play = {"user_id": uid, "cards": list(cards), **pattern.to_dict()}
        self.seat_actions[uid] = {"action": "play", "cards": list(cards), "label": NAMES[pattern.kind]}
        self.passed.clear()
        self.message = f"打出{NAMES[pattern.kind]}。"
        if not self.hands[uid]:
            self.finish_order.append(uid)
            if self._partner(uid) in self.finish_order:
                self.finish_order.extend(other for other in self.player_ids if other not in self.finish_order)
                self._finish()
                return
            if len(self.finish_order) == 3:
                self.finish_order.extend(other for other in self.player_ids if other not in self.finish_order)
                self._finish()
                return
        self.turn_index = self.player_ids.index(self._next(uid))

    def _finish(self):
        first = self.finish_order[0]
        team = self.teams[first]
        partner_rank = self.finish_order.index(self._partner(first)) + 1
        self.level_gain = {2: 3, 3: 2, 4: 1}[partner_rank]
        self.winners = [uid for uid in self.player_ids if self.teams[uid] == team]
        if self.declarer_team == team and self.level == "A" and partner_rank < 4:
            self.match_finished, self.match_winner_team = True, team
        else:
            self.team_levels[team] = RANKS[min(12, RANKS.index(self.team_levels[team]) + self.level_gain)]
        self.declarer_team = team
        stake = min(self.buy_in, self.base_stake * self.level_gain)
        self.scores = {uid: stake if self.teams[uid] == team else -stake for uid in self.player_ids}
        self.phase, self.finished = "finished", True
        self.message = f"第{team + 1}队获胜，升{self.level_gain}级。" + ("成功过A，本场结束。" if self.match_finished else "同桌续局保留级数并自动贡还贡。")

    def next_match_state(self):
        if not self.finished:
            raise ValueError("本局尚未结束")
        return {"player_ids": list(self.player_ids), "team_levels": list(self.team_levels),
                "declarer_team": self.declarer_team, "finish_order": list(self.finish_order),
                "match_finished": self.match_finished}

    def settlement(self):
        return dict(self.scores) if self.finished else {}

    def public_state(self, viewer_id):
        viewer = str(viewer_id)
        options = self._play_options(viewer) if viewer == self.current_player_id else []
        return {
            "phase": self.phase, "finished": self.finished, "current_player_id": self.current_player_id,
            "players": [{"user_id": uid, "hand": list(self.hands[uid]) if uid == viewer or self.finished else [],
                         "hand_count": len(self.hands[uid]), "team": self.teams[uid], "score": self.scores[uid],
                         "score_delta": self.scores[uid], "stack": self.buy_in + self.scores[uid],
                         "finished_rank": self.finish_order.index(uid) + 1 if uid in self.finish_order else None}
                        for uid in self.player_ids],
            "legal_actions": self._legal_actions(viewer), "message": self.message, "winners": list(self.winners),
            "settlement": self.settlement(), "buy_in": self.buy_in, "base_stake": self.base_stake,
            "level": self.level, "team_levels": list(self.team_levels), "finish_order": list(self.finish_order),
            "tribute_events": deepcopy(self.tribute_events), "match_finished": self.match_finished,
            "match_winner_team": self.match_winner_team, "level_gain": self.level_gain,
            "last_play": {**self.last_play, "cards": list(self.last_play["cards"])} if self.last_play else None,
            "seat_actions": {uid: {**event, "cards": list(event["cards"])} for uid, event in self.seat_actions.items()},
            "play_options": options,
        }
