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

    def _candidates(self, user_id: str):
        """按点数生成代表性组合；不读取对手暗牌。"""
        groups: dict[int, list[str]] = {}
        for card in self.hands[user_id]:
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
            signature = tuple(sorted(cards))
            if signature in seen:
                continue
            seen.add(signature)
            try:
                pattern = classify_landlord_cards(cards)
            except ValueError:
                continue
            if self.last_pattern is None or landlord_beats(pattern, self.last_pattern):
                yield cards, pattern

    def suggest_action(self, user_id: str) -> dict:
        user_id = str(user_id)
        if not self._legal_actions(user_id):
            raise ValueError("尚未轮到该玩家")
        if self.phase == "bidding":
            strength = sum(poker_value(card) >= 15 for card in self.hands[user_id])
            desired = 3 if strength >= 4 else 2 if strength >= 3 else 1
            bid = desired if desired > self.highest_bid else 0
            return {"action": "bid", "bid": bid}
        candidates = list(self._candidates(user_id))
        if not candidates:
            return {"action": "pass"}
        # 农民不抢队友已出的普通牌；可直接出完则优先结束。
        finishing = [item for item in candidates if len(item[0]) == len(self.hands[user_id])]
        if (not finishing and self.last_play and user_id != self.landlord_id
                and self.last_play["user_id"] != self.landlord_id):
            return {"action": "pass"}
        cards, _ = min(finishing or candidates, key=lambda item: (
            item[1].kind in ("bomb", "rocket"), -len(item[0]), item[1].rank))
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
