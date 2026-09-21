"""经典定缺四川血战：只计算零和理论分，由房间钱包统一限损结算。"""

from collections import Counter
from copy import deepcopy
from functools import lru_cache
import random

from .traditional_games import (
    _validate_base_stake,
    _validate_buy_in,
    _validate_players,
    is_mahjong_win,
)


SUITS = ("m", "p", "s")
SUIT_NAMES = {"m": "万", "p": "筒", "s": "条"}
TILES = tuple(f"{suit}{number}" for suit in SUITS for number in range(1, 10))
TILE_INDEX = {tile: index for index, tile in enumerate(TILES)}
MAX_FAN = 6
# 预留累计胡/杠、查花猪和查大叫的最坏账分空间，保证快照整数无精度损失。
MAX_BUY_IN = (2**53 - 1) // 8
MAX_BASE_STAKE = (2**53 - 1) // 512


def is_sichuan_win(tiles: list[str], melds: list[dict] | None = None) -> bool:
    """缺一门的四组一对或七对，四张同牌在七对中计两对。"""
    melds = melds or []
    if len(tiles) != 14 - 3 * len(melds) or len(melds) > 4:
        return False
    all_tiles = tiles + [tile for meld in melds for tile in meld["tiles"]]
    if any(not isinstance(tile, str) or tile not in TILE_INDEX for tile in all_tiles):
        return False
    counts = Counter(all_tiles)
    if any(count > 4 for count in counts.values()) or len({tile[0] for tile in all_tiles}) > 2:
        return False
    if not melds and all(count % 2 == 0 for count in counts.values()):
        return True
    return is_mahjong_win(tiles, len(melds))


@lru_cache(maxsize=16384)
def _terminal_groups(counts: tuple[int, ...]) -> bool:
    """带幺九的每组必须包含一或九，枚举合法拆分避免贪心误判。"""
    first = next((index for index, count in enumerate(counts) if count), None)
    if first is None:
        return True
    rest = list(counts)
    if first % 9 in (0, 8) and rest[first] >= 3:
        rest[first] -= 3
        if _terminal_groups(tuple(rest)):
            return True
        rest[first] += 3
    if first % 9 in (0, 6) and rest[first + 1] and rest[first + 2]:
        for index in (first, first + 1, first + 2):
            rest[index] -= 1
        if _terminal_groups(tuple(rest)):
            return True
    return False


def _all_terminals(tiles: list[str], melds: list[dict]) -> bool:
    if any(any(tile[1] not in "19" for tile in meld["tiles"]) for meld in melds):
        return False
    counts = Counter(tiles)
    for tile in counts:
        if tile[1] in "19" and counts[tile] >= 2:
            remainder = counts.copy()
            remainder[tile] -= 2
            if _terminal_groups(tuple(remainder[value] for value in TILES)):
                return True
    return False


def score_hand(tiles: list[str], melds: list[dict] | None = None, *,
               self_draw: bool = False, after_kong: bool = False,
               kong_discard: bool = False, rob_kong: bool = False) -> dict:
    """返回一番一倍、六番封顶的实际牌型，不累加互斥基础牌型。"""
    melds = melds or []
    if not is_sichuan_win(tiles, melds):
        raise ValueError("不符合四川麻将胡牌结构或缺门要求")
    counts = Counter(tiles)
    all_tiles = tiles + [tile for meld in melds for tile in meld["tiles"]]
    all_counts = Counter(all_tiles)
    pure = len({tile[0] for tile in all_tiles}) == 1
    seven_pairs = not melds and all(count % 2 == 0 for count in counts.values())
    triplets = sum(count % 3 == 2 for count in counts.values()) == 1 and all(count % 3 in (0, 2) for count in counts.values())
    terminals = _all_terminals(tiles, melds)
    roots = sum(count == 4 for count in all_counts.values())
    candidates = [(1, "平胡", 0)]
    if triplets:
        candidates.append((4 if pure else 2, "清对" if pure else "对对胡", 0))
    if pure:
        candidates.append((3, "清一色", 0))
    if seven_pairs:
        candidates.append((5 if pure else 3, "清七对" if pure else "七对", 0))
        if roots:
            candidates.append((6 if pure else 5, "清龙七对" if pure else "龙七对", 1))
    if terminals:
        candidates.append((5 if pure else 3, "清幺九" if pure else "带幺九", 0))
    if all(tile[1] in "258" for tile in all_tiles) and triplets:
        candidates.append((4, "将对", 0))
    if len(melds) == 4 and all(meld["type"] == "kong" for meld in melds):
        candidates.append((5, "十八罗汉", 0))
    base, label, included_roots = max(candidates, key=lambda item: (item[0] + roots - item[2], item[0]))
    bonuses = []
    extra_roots = roots - included_roots
    if extra_roots:
        bonuses.append((extra_roots, f"{extra_roots}根"))
    if len(melds) == 4 and len(tiles) == 2:
        bonuses.append((1, "金钩钓"))
    if self_draw:
        bonuses.append((1, "自摸"))
    if after_kong:
        bonuses.append((1, "杠上花"))
    if kong_discard:
        bonuses.append((1, "杠上炮"))
    if rob_kong:
        bonuses.append((1, "抢杠胡"))
    raw_fan = base + sum(extra for extra, _ in bonuses)
    fan = min(MAX_FAN, raw_fan)
    return {"fan": fan, "multiplier": 2 ** (fan - 1), "label": "·".join([label] + [name for _, name in bonuses]),
            "base_label": label, "roots": roots, "capped": raw_fan > MAX_FAN}


class SichuanMahjongGame:
    """108 张经典定缺血战；不换三张，三人胡或流局后统一结算。"""

    SCORE_UNIT = 1

    def __init__(self, player_ids: list[str], seed: int | None = None, buy_in: int = 100, base_stake: int = 1):
        self.player_ids = _validate_players(player_ids, 4)
        self.buy_in = _validate_buy_in(buy_in)
        self.SCORE_UNIT = _validate_base_stake(base_stake, self.buy_in)
        if self.buy_in > MAX_BUY_IN or self.SCORE_UNIT > MAX_BASE_STAKE:
            raise ValueError("四川血战金额过大：请降低单局上限或底分以保证累计账分精度")
        self.random = random.Random(seed)
        self.wall = [tile for tile in TILES for _ in range(4)]
        self.random.shuffle(self.wall)
        self.hands = {uid: self._sorted([self.wall.pop() for _ in range(13)]) for uid in self.player_ids}
        self.hands[self.player_ids[0]].append(self.wall.pop())
        self.hands[self.player_ids[0]] = self._sorted(self.hands[self.player_ids[0]])
        self.melds: dict[str, list[dict]] = {uid: [] for uid in self.player_ids}
        self.discards: dict[str, list[str]] = {uid: [] for uid in self.player_ids}
        self.missing_suits: dict[str, str] = {}
        self.scores = {uid: 0 for uid in self.player_ids}
        self.winners: list[str] = []
        self.win_events: list[dict] = []
        self.win_details: dict[str, dict] = {}
        self.kong_payments: list[dict] = []
        self.flow_details: dict = {}
        self.phase = "dingque"
        self._finished = False
        self._current_id = self.player_ids[0]
        self._can_self_win = True
        self.last_drawn_tile: str | None = None
        self.last_discard: dict | None = None
        self.reaction_queue: list[tuple[str, list[str]]] = []
        self.reaction_kind: str | None = None
        self.pending_kong: dict | None = None
        self._reaction_winners: list[str] = []
        self._draw_after_kong = False
        self.message = "请先定缺万、筒或条；四家定缺后，东家先出牌。"

    @staticmethod
    def _sorted(tiles):
        return sorted(tiles, key=TILE_INDEX.__getitem__)

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def current_player_id(self) -> str | None:
        return None if self.finished else self._current_id

    def _active(self) -> list[str]:
        return [uid for uid in self.player_ids if uid not in self.winners]

    def _ordered_after(self, user_id: str) -> list[str]:
        start = self.player_ids.index(user_id)
        return [self.player_ids[(start + distance) % 4] for distance in range(1, 4)
                if self.player_ids[(start + distance) % 4] not in self.winners]

    def _next_after(self, user_id: str) -> str:
        return self._ordered_after(user_id)[0]

    def _has_missing(self, user_id: str, tiles: list[str] | None = None) -> bool:
        suit = self.missing_suits.get(user_id)
        return any(tile[0] == suit for tile in (self.hands[user_id] if tiles is None else tiles))

    def _can_win(self, user_id: str, tile: str | None = None) -> bool:
        hand = self.hands[user_id] + ([tile] if tile is not None else [])
        return (user_id not in self.winners and user_id in self.missing_suits
                and not self._has_missing(user_id, hand) and is_sichuan_win(hand, self.melds[user_id]))

    def _kong_options(self, user_id: str) -> list[str]:
        if not self.wall or self._has_missing(user_id):
            return []
        counts = Counter(self.hands[user_id])
        options = [tile for tile, count in counts.items() if count == 4]
        options += [meld["tiles"][0] for meld in self.melds[user_id]
                    if meld["type"] == "pung" and counts[meld["tiles"][0]]]
        return self._sorted(options)

    def _legal_actions(self, user_id: str) -> list[str]:
        if self.finished or user_id != self.current_player_id or user_id in self.winners:
            return []
        if self.phase == "dingque":
            return ["dingque"]
        if self.phase == "reaction":
            return list(self.reaction_queue[0][1]) + ["pass"]
        actions = ["discard"]
        if self._can_self_win and self._can_win(user_id):
            actions.append("win")
        if self._kong_options(user_id):
            actions.append("kong")
        return actions

    def public_state(self, viewer_id: str) -> dict:
        viewer_id = str(viewer_id)
        players = []
        for seat, uid in enumerate(self.player_ids):
            visible = uid == viewer_id or self.finished
            detail = self.win_details.get(uid, {})
            melds = [{**meld, "tiles": list(meld["tiles"]) if visible or not meld.get("concealed") else [],
                      "tile_count": len(meld["tiles"])} for meld in self.melds[uid]]
            players.append({"user_id": uid, "hand": list(self.hands[uid]) if visible else [],
                            "hand_count": len(self.hands[uid]), "melds": melds,
                            "discards": list(self.discards[uid]), "score": self.scores[uid],
                            "score_delta": self.scores[uid], "stack": self.buy_in + self.scores[uid],
                            "seat_wind": ("东", "南", "西", "北")[seat],
                            "missing_suit": self.missing_suits.get(uid) if len(self.missing_suits) == 4 or uid == viewer_id else None,
                            "has_won": uid in self.winners, "win_order": detail.get("order"),
                            "win_fan": detail.get("fan"), "win_label": detail.get("label"),
                            "winning_tile": detail.get("tile")})
        actions = self._legal_actions(viewer_id)
        reaction_tile = (self.pending_kong or {}).get("tile") if self.reaction_kind == "rob_kong" else (self.last_discard or {}).get("tile")
        return {"mahjong_variant": "sichuan", "phase": self.phase, "finished": self.finished,
                "current_player_id": self.current_player_id, "players": players,
                "legal_actions": actions, "message": self.message, "winners": list(self.winners),
                "win_events": deepcopy(self.win_events), "flow_details": deepcopy(self.flow_details),
                "score_unit": self.SCORE_UNIT, "base_stake": self.SCORE_UNIT, "buy_in": self.buy_in,
                "max_fan": MAX_FAN, "wall_count": len(self.wall),
                "settlement": dict(self.scores) if self.finished else {},
                "last_discard": dict(self.last_discard) if self.last_discard else None,
                "discards": {uid: list(tiles) for uid, tiles in self.discards.items()},
                "missing_suit_options": list(SUITS) if "dingque" in actions else [],
                "chow_options": [], "reaction_kind": self.reaction_kind,
                "kong_options": ([reaction_tile] if self.phase == "reaction" else self._kong_options(viewer_id)) if "kong" in actions else [],
                "drawn_tile": self.last_drawn_tile if viewer_id == self.current_player_id and self.phase == "playing" else None}

    def _transfer(self, payer: str, recipient: str, amount: int):
        self.scores[payer] -= amount
        self.scores[recipient] += amount

    def _pay_kong(self, recipient: str, kind: str, source: str | None = None):
        payers = [source] if kind == "exposed" else [uid for uid in self._active() if uid != recipient]
        unit = self.SCORE_UNIT * (1 if kind == "added" else 2)
        for payer in payers:
            self._transfer(payer, recipient, unit)
            self.kong_payments.append({"payer": payer, "recipient": recipient, "amount": unit, "refunded": False})

    def _finish(self, message: str):
        self._finished = True
        self.phase = "finished"
        self.reaction_queue = []
        self.reaction_kind = None
        self.pending_kong = None
        self.last_drawn_tile = None
        self.message = message

    def _ready_value(self, user_id: str) -> dict | None:
        if self._has_missing(user_id):
            return None
        own = Counter(self.hands[user_id] + [tile for meld in self.melds[user_id] for tile in meld["tiles"]])
        candidates = []
        for tile in TILES:
            if own[tile] < 4 and self._can_win(user_id, tile):
                candidates.append(score_hand(self.hands[user_id] + [tile], self.melds[user_id]))
        return max(candidates, key=lambda value: value["multiplier"], default=None)

    def _finish_draw(self):
        active = self._active()
        pigs = [uid for uid in active if self._has_missing(uid)]
        ready = {uid: self._ready_value(uid) for uid in active if uid not in pigs}
        # 先还原未听牌者收到的杠分，再按流局规则转移，全部记入同一本理论账。
        refunds = []
        for payment in self.kong_payments:
            if not payment["refunded"] and payment["recipient"] in active and not ready.get(payment["recipient"]):
                self._transfer(payment["recipient"], payment["payer"], payment["amount"])
                payment["refunded"] = True
                refunds.append(dict(payment))
        for payer in pigs:
            for recipient in self.player_ids:
                if recipient not in pigs:
                    self._transfer(payer, recipient, self.SCORE_UNIT * 2 ** (MAX_FAN - 1))
        for payer, value in ready.items():
            if value is None:
                for recipient, winner_value in ready.items():
                    if winner_value:
                        self._transfer(payer, recipient, self.SCORE_UNIT * winner_value["multiplier"])
        self.flow_details = {"flower_pigs": pigs, "ready_players": {uid: value for uid, value in ready.items() if value},
                             "not_ready_players": [uid for uid, value in ready.items() if value is None], "kong_refunds": refunds}
        self._finish("牌墙摸尽：已完成查花猪、查大叫与未听退杠；等待账户统一结算。")

    def _draw(self, user_id: str, replacement: bool = False):
        if not self.wall:
            self._finish_draw()
            return
        self.last_drawn_tile = self.wall.pop(0) if replacement else self.wall.pop()
        self.hands[user_id].append(self.last_drawn_tile)
        self.hands[user_id] = self._sorted(self.hands[user_id])
        self._current_id = user_id
        self.phase = "playing"
        self._can_self_win = True
        self._draw_after_kong = replacement
        self.reaction_kind = None

    def _record_win(self, winner: str, source: str | None, *, kind: str, tile: str | None = None):
        hand = self.hands[winner] + ([tile] if tile is not None else [])
        value = score_hand(hand, self.melds[winner], self_draw=source is None,
                           after_kong=source is None and self._draw_after_kong,
                           kong_discard=source is not None and kind == "discard" and bool((self.last_discard or {}).get("after_kong")),
                           rob_kong=kind == "rob_kong")
        payers = [uid for uid in self._active() if uid != winner] if source is None else [source]
        unit = self.SCORE_UNIT * value["multiplier"]
        for payer in payers:
            self._transfer(payer, winner, unit)
        self.winners.append(winner)
        winning_tile = tile if tile is not None else self.last_drawn_tile
        self.win_details[winner] = {**value, "order": len(self.winners), "tile": winning_tile, "kind": kind}
        self.win_events.append({"id": len(self.win_events) + 1, "user_id": winner, "source_id": source,
                                "kind": kind, "fan": value["fan"], "label": value["label"], "amount": unit * len(payers),
                                "tile": winning_tile})
        # 多人共胡同一实体弃牌：手牌保留原牌，事件记录胡张，避免复制实体牌。
        self.message = f"玩家 {winner} {value['label']}胡牌（{value['fan']}番），理论得分 +{unit * len(payers)}；血战继续，整局结束后结算。"

    def _resolve_reactions(self):
        if self.reaction_queue:
            self._current_id = self.reaction_queue[0][0]
            self.phase = "reaction"
            return
        kind = self.reaction_kind
        if kind == "rob_kong":
            pending = self.pending_kong
            uid, tile = pending["user_id"], pending["tile"]
            if not self._reaction_winners:
                self._complete_added_kong(uid, tile)
                return
            self.pending_kong = None
            source = uid
        else:
            source = self.last_discard["user_id"]
        self.reaction_kind = None
        if len(self.winners) >= 3:
            self._finish("三家已胡，血战结束；等待账户统一结算。")
        else:
            self._draw(self._next_after(source))

    def _begin_reactions(self, discarder: str, tile: str):
        wins, melds = [], []
        for uid in self._ordered_after(discarder):
            if self._can_win(uid, tile):
                wins.append((uid, ["win"]))
            if self._has_missing(uid) or tile[0] == self.missing_suits[uid]:
                continue
            count = self.hands[uid].count(tile)
            actions = (["kong"] if count >= 3 and self.wall else []) + (["pung"] if count >= 2 else [])
            if actions:
                melds.append((uid, actions))
        self.reaction_kind = "discard"
        self._reaction_winners = []
        self.reaction_queue = wins + melds
        self._resolve_reactions()

    def _complete_added_kong(self, user_id: str, tile: str):
        self.hands[user_id].remove(tile)
        meld = next(meld for meld in self.melds[user_id] if meld["type"] == "pung" and meld["tiles"][0] == tile)
        meld["type"] = "kong"
        meld["tiles"].append(tile)
        self.pending_kong = None
        self.reaction_kind = None
        self._pay_kong(user_id, "added")
        self.message = "补杠成功；其余未胡者各付一倍底分，杠后补牌。"
        self._draw(user_id, replacement=True)

    def _kong(self, user_id: str, tile: str):
        if tile not in self._kong_options(user_id):
            raise ValueError("请选择可杠牌；有缺门牌或无补牌时不能杠")
        if self.hands[user_id].count(tile) == 4:
            for _ in range(4):
                self.hands[user_id].remove(tile)
            self.melds[user_id].append({"type": "kong", "tiles": [tile] * 4, "concealed": True})
            self._pay_kong(user_id, "concealed")
            self.message = "暗杠；其余未胡者各付两倍底分，杠后补牌。"
            self._draw(user_id, replacement=True)
            return
        self.pending_kong = {"user_id": user_id, "tile": tile}
        self.reaction_kind = "rob_kong"
        self._reaction_winners = []
        self.reaction_queue = [(uid, ["win"]) for uid in self._ordered_after(user_id) if self._can_win(uid, tile)]
        self.message = "等待抢补杠响应。"
        self._resolve_reactions()

    def _react(self, user_id: str, action: str, payload: dict):
        source = self.pending_kong["user_id"] if self.reaction_kind == "rob_kong" else self.last_discard["user_id"]
        tile = self.pending_kong["tile"] if self.reaction_kind == "rob_kong" else self.last_discard["tile"]
        if payload.get("tile") is not None and payload["tile"] != tile:
            raise ValueError("响应牌必须是刚打出或补杠的牌")
        if action == "pass":
            self.reaction_queue.pop(0)
            self._resolve_reactions()
            return
        if action == "win":
            if self.reaction_kind == "rob_kong" and not self._reaction_winners:
                self.hands[source].remove(tile)
                self.discards[source].append(tile)
                self.last_discard = {"user_id": source, "tile": tile, "after_kong": False}
            self._record_win(user_id, source, kind=self.reaction_kind, tile=tile)
            self._reaction_winners.append(user_id)
            self.reaction_queue = [(uid, actions) for uid, actions in self.reaction_queue[1:]
                                   if uid not in self.winners and "win" in actions]
            self._resolve_reactions()
            return
        count = 3 if action == "kong" else 2
        # action来自服务端合法动作；在提交前再次校验实体牌与补牌资源。
        if self.hands[user_id].count(tile) < count or (action == "kong" and not self.wall):
            raise ValueError("手牌不足或无可补牌")
        for _ in range(count):
            self.hands[user_id].remove(tile)
        self.discards[source].pop()
        self.melds[user_id].append({"type": action, "tiles": [tile] * (count + 1), "concealed": False, "from_user_id": source})
        self.reaction_queue = []
        self.reaction_kind = None
        self._current_id = user_id
        self.phase = "playing"
        self._can_self_win = False
        self._draw_after_kong = False
        self.last_drawn_tile = None
        self.message = "碰牌后请打出一张牌。"
        if action == "kong":
            self._pay_kong(user_id, "exposed", source)
            self.message = "直杠；点杠者付两倍底分，杠后补牌。"
            self._draw(user_id, replacement=True)

    def act(self, user_id: str, action: str, **payload) -> None:
        user_id = str(user_id)
        if action not in self._legal_actions(user_id):
            raise ValueError("当前不能执行此操作、已胡牌或尚未轮到你")
        if action == "dingque":
            suit = payload.get("suit")
            if suit not in SUITS:
                raise ValueError("定缺只能选择万、筒或条")
            self.missing_suits[user_id] = suit
            if len(self.missing_suits) == 4:
                self.phase = "playing"
                self._current_id = self.player_ids[0]
                self.message = "定缺完成；有缺门牌时必须先打缺门，不能吃牌。"
            else:
                self._current_id = self.player_ids[len(self.missing_suits)]
            return
        if self.phase == "reaction":
            self._react(user_id, action, payload)
            return
        if action == "win":
            self._record_win(user_id, None, kind="self_draw")
            if len(self.winners) >= 3:
                self._finish("三家已胡，血战结束；等待账户统一结算。")
            else:
                self._draw(self._next_after(user_id))
            return
        if action == "kong":
            self._kong(user_id, payload.get("tile"))
            return
        tile = payload.get("tile")
        if not isinstance(tile, str) or tile not in self.hands[user_id]:
            raise ValueError("请选择手牌中的一张牌打出")
        if self._has_missing(user_id) and tile[0] != self.missing_suits[user_id]:
            raise ValueError("必须先打完定缺花色的牌")
        self.hands[user_id].remove(tile)
        self.discards[user_id].append(tile)
        self.last_discard = {"user_id": user_id, "tile": tile, "after_kong": self._draw_after_kong}
        self.last_drawn_tile = None
        self._can_self_win = False
        self.message = "等待其他未胡玩家响应。"
        self._begin_reactions(user_id, tile)

    def suggest_action(self, user_id: str) -> dict:
        """建议只读取本人手牌和公开状态，机器人不会获知对手暗牌。"""
        user_id = str(user_id)
        actions = self._legal_actions(user_id)
        if not actions:
            raise ValueError("尚未轮到该玩家或该玩家已胡")
        counts = Counter(self.hands[user_id])
        if "dingque" in actions:
            suit = min(SUITS, key=lambda value: sum(count for tile, count in counts.items() if tile[0] == value))
            return {"action": "dingque", "suit": suit}
        if "win" in actions:
            return {"action": "win"}
        if "kong" in actions:
            tile = self.last_discard["tile"] if self.phase == "reaction" else self._kong_options(user_id)[0]
            return {"action": "kong", "tile": tile}
        if "pung" in actions:
            return {"action": "pung", "tile": self.last_discard["tile"]}
        if self.phase == "reaction":
            return {"action": "pass"}
        candidates = [tile for tile in counts if tile[0] == self.missing_suits[user_id]] or list(counts)

        def usefulness(tile):
            value = 5 * (counts[tile] - 1)
            number = int(tile[1])
            for offset, weight in ((-2, 1), (-1, 3), (1, 3), (2, 1)):
                if 1 <= number + offset <= 9:
                    value += weight * min(counts[f"{tile[0]}{number + offset}"], 1)
            return value, -TILE_INDEX[tile]

        return {"action": "discard", "tile": min(candidates, key=usefulness)}
