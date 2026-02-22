import random
import string
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


SUITS = ["Club", "Diamond", "Heart", "Spade"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def _create_deck() -> List[str]:
    return [f"{suit}{rank}" for suit in SUITS for rank in RANKS]


def _get_card_value(card: str) -> int:
    if card.endswith("10"):
        return 10
    rank = card[-1]
    if rank in ["J", "Q", "K"]:
        return 10
    if rank == "A":
        return 11
    return int(rank)


def _calculate_hand_score(hand: List[str]) -> int:
    score = 0
    ace_count = 0
    for card in hand:
        if card == "Hidden":
            continue
        score += _get_card_value(card)
        if card.endswith("A"):
            ace_count += 1

    while score > 21 and ace_count > 0:
        score -= 10
        ace_count -= 1
    return score


@dataclass
class MultiplayerPlayerState:
    user_id: int
    username: str
    avatar_url: str
    seat_index: int
    bet_amount: int = 0
    hand: List[str] = field(default_factory=list)
    status: str = "waiting"  # waiting | playing | stood | bust | blackjack | finished
    result: Optional[str] = None  # win | loss | push | blackjack
    payout_amount: int = 0
    is_ready: bool = False


@dataclass
class MultiplayerRoom:
    room_id: str
    host_user_id: int
    players: Dict[int, MultiplayerPlayerState] = field(default_factory=dict)
    state: str = "waiting"  # waiting | playing | dealer_turn | finished
    deck: List[str] = field(default_factory=list)
    dealer_hand: List[str] = field(default_factory=list)
    turn_order: List[int] = field(default_factory=list)
    current_turn_index: int = 0
    payouts_committed: bool = False
    updated_at: float = field(default_factory=lambda: time.time())


class MultiplayerBlackjackService:
    MAX_PLAYERS = 3

    def __init__(self):
        self._rooms: Dict[str, MultiplayerRoom] = {}

    def _touch(self, room: MultiplayerRoom) -> None:
        room.updated_at = time.time()

    def _generate_room_id(self) -> str:
        alphabet = string.ascii_uppercase + string.digits
        for _ in range(100):
            room_id = "".join(random.choices(alphabet, k=6))
            if room_id not in self._rooms:
                return room_id
        raise ValueError("无法生成房间号，请稍后再试")

    def _resolve_dealer_expression(self, room: MultiplayerRoom) -> str:
        if room.state != "finished":
            return "normal"

        has_player_win = any(
            p.result in ("win", "blackjack") for p in room.players.values() if p.bet_amount > 0
        )
        if has_player_win:
            return "lose"

        has_push = any(p.result == "push" for p in room.players.values() if p.bet_amount > 0)
        if has_push:
            return "normal"

        return "win"

    def _get_room_or_raise(self, room_id: str) -> MultiplayerRoom:
        room = self._rooms.get(room_id)
        if not room:
            raise ValueError("房间不存在或已关闭")
        return room

    def _to_player_dict(self, player: MultiplayerPlayerState, room: MultiplayerRoom) -> Dict[str, Any]:
        current_turn_user_id = None
        if room.state == "playing" and room.turn_order and room.current_turn_index < len(room.turn_order):
            current_turn_user_id = room.turn_order[room.current_turn_index]

        return {
            "user_id": player.user_id,
            "username": player.username,
            "avatar_url": player.avatar_url,
            "seat_index": player.seat_index,
            "bet_amount": player.bet_amount,
            "hand": player.hand,
            "score": _calculate_hand_score(player.hand),
            "status": player.status,
            "result": player.result,
            "payout_amount": player.payout_amount,
            "is_ready": player.is_ready,
            "is_current_turn": current_turn_user_id == player.user_id,
        }

    def _to_room_state(self, room: MultiplayerRoom) -> Dict[str, Any]:
        players = sorted(room.players.values(), key=lambda p: p.seat_index)

        current_turn_user_id = None
        if room.state == "playing" and room.turn_order and room.current_turn_index < len(room.turn_order):
            current_turn_user_id = room.turn_order[room.current_turn_index]

        dealer_expression = self._resolve_dealer_expression(room)
        show_all_dealer_cards = room.state in ("dealer_turn", "finished")

        if show_all_dealer_cards:
            dealer_hand = room.dealer_hand
            dealer_score = _calculate_hand_score(room.dealer_hand) if room.dealer_hand else 0
        else:
            if len(room.dealer_hand) >= 2:
                dealer_hand = [room.dealer_hand[0], "Hidden"]
                dealer_score = _calculate_hand_score([room.dealer_hand[0]])
            elif len(room.dealer_hand) == 1:
                dealer_hand = [room.dealer_hand[0]]
                dealer_score = _calculate_hand_score(room.dealer_hand)
            else:
                dealer_hand = []
                dealer_score = 0

        ready_player_count = sum(
            1 for p in players if p.bet_amount > 0 and p.is_ready
        )
        all_players_ready = bool(players) and all(
            p.bet_amount > 0 and p.is_ready for p in players
        )

        return {
            "room_id": room.room_id,
            "host_user_id": room.host_user_id,
            "max_players": self.MAX_PLAYERS,
            "state": room.state,
            "current_turn_user_id": current_turn_user_id,
            "ready_player_count": ready_player_count,
            "all_players_ready": all_players_ready,
            "dealer": {
                "name": "月月",
                "avatar_path": f"/character/{dealer_expression}.webp",
                "expression": dealer_expression,
                "hand": dealer_hand,
                "score": dealer_score,
            },
            "players": [self._to_player_dict(p, room) for p in players],
        }

    def create_room(self, user_id: int, username: str, avatar_url: str) -> Dict[str, Any]:
        room_id = self._generate_room_id()
        room = MultiplayerRoom(room_id=room_id, host_user_id=user_id)
        room.players[user_id] = MultiplayerPlayerState(
            user_id=user_id,
            username=username,
            avatar_url=avatar_url,
            seat_index=0,
        )
        self._rooms[room_id] = room
        self._touch(room)
        return self._to_room_state(room)

    def join_room(self, room_id: str, user_id: int, username: str, avatar_url: str) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)

        if user_id in room.players:
            return self._to_room_state(room)

        if len(room.players) >= self.MAX_PLAYERS:
            raise ValueError("房间人数已满（最多3人）")

        if room.state in ("playing", "dealer_turn"):
            raise ValueError("本局游戏进行中，暂时无法加入")

        used_seats = {p.seat_index for p in room.players.values()}
        seat_index = next((i for i in range(self.MAX_PLAYERS) if i not in used_seats), None)
        if seat_index is None:
            raise ValueError("房间座位分配失败")

        room.players[user_id] = MultiplayerPlayerState(
            user_id=user_id,
            username=username,
            avatar_url=avatar_url,
            seat_index=seat_index,
        )
        self._touch(room)
        return self._to_room_state(room)

    def get_room_state(self, room_id: str) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)
        return self._to_room_state(room)

    def leave_room(self, room_id: str, user_id: int) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)

        if user_id not in room.players:
            return self._to_room_state(room)

        del room.players[user_id]

        # 若房间空了，直接销毁
        if not room.players:
            del self._rooms[room_id]
            return {"room_closed": True, "room_id": room_id}

        # 主持人离开时转移主持权
        if room.host_user_id == user_id:
            room.host_user_id = sorted(room.players.keys())[0]

        # 若游戏中离开，移除其行动位；玩家已下注则默认判负（不退赌注）
        if room.state in ("playing", "dealer_turn"):
            room.turn_order = [uid for uid in room.turn_order if uid in room.players]
            if room.current_turn_index >= len(room.turn_order):
                room.current_turn_index = max(0, len(room.turn_order) - 1)

        self._touch(room)
        return self._to_room_state(room)

    def set_bet(self, room_id: str, user_id: int, amount: int) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)
        player = room.players.get(user_id)
        if not player:
            raise ValueError("你不在该房间中")

        if amount <= 0:
            raise ValueError("下注金额必须大于0")

        if room.state in ("playing", "dealer_turn"):
            raise ValueError("本局进行中，无法修改下注")

        # 新一局下注前清理旧局信息
        if room.state == "finished":
            self._reset_round(room)

        player.bet_amount = amount
        player.is_ready = False
        self._touch(room)
        return self._to_room_state(room)

    def set_ready(self, room_id: str, user_id: int, ready: bool) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)
        player = room.players.get(user_id)
        if not player:
            raise ValueError("你不在该房间中")

        if room.state in ("playing", "dealer_turn"):
            raise ValueError("本局进行中，无法修改准备状态")

        if ready and player.bet_amount <= 0:
            raise ValueError("请先下注再准备")

        player.is_ready = bool(ready)
        self._touch(room)
        return self._to_room_state(room)

    def start_round(self, room_id: str, user_id: int) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)
        if room.host_user_id != user_id:
            raise ValueError("只有房主可以开始游戏")

        if room.state in ("playing", "dealer_turn"):
            raise ValueError("本局游戏已经开始")

        # 支持 finished 后继续开新局
        if room.state == "finished":
            self._reset_round(room)

        if not room.players:
            raise ValueError("房间内没有玩家")

        not_ready_names: List[str] = []
        for p in room.players.values():
            if p.bet_amount <= 0 or not p.is_ready:
                not_ready_names.append(p.username)

        if not_ready_names:
            raise ValueError(
                "以下玩家尚未完成下注并准备: " + "、".join(not_ready_names)
            )

        participants = [p for p in room.players.values() if p.bet_amount > 0]
        if not participants:
            raise ValueError("至少需要一名已下注玩家才能开始")

        room.deck = _create_deck()
        random.shuffle(room.deck)
        room.dealer_hand = [room.deck.pop(), room.deck.pop()]
        room.turn_order = []
        room.current_turn_index = 0
        room.payouts_committed = False

        for p in participants:
            p.hand = [room.deck.pop(), room.deck.pop()]
            score = _calculate_hand_score(p.hand)
            p.result = None
            p.payout_amount = 0
            p.is_ready = False
            if score == 21:
                p.status = "blackjack"
            else:
                p.status = "playing"
                room.turn_order.append(p.user_id)

        for p in room.players.values():
            if p.bet_amount <= 0:
                p.hand = []
                p.result = None
                p.payout_amount = 0
                p.status = "waiting"
                p.is_ready = False

        room.state = "playing"

        if not room.turn_order:
            self._resolve_dealer_and_settle(room)

        self._touch(room)
        return self._to_room_state(room)

    def hit(self, room_id: str, user_id: int) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)
        if room.state != "playing":
            raise ValueError("当前不在玩家操作阶段")

        current_uid = self._current_turn_user_id(room)
        if current_uid != user_id:
            raise ValueError("还没轮到你操作")

        player = room.players[user_id]
        if player.status != "playing":
            raise ValueError("你当前无法要牌")

        player.hand.append(room.deck.pop())
        score = _calculate_hand_score(player.hand)

        if score > 21:
            player.status = "bust"
            player.result = "loss"
            self._advance_turn(room)
        elif score == 21:
            player.status = "stood"
            self._advance_turn(room)

        self._touch(room)
        return self._to_room_state(room)

    def stand(self, room_id: str, user_id: int) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)
        if room.state != "playing":
            raise ValueError("当前不在玩家操作阶段")

        current_uid = self._current_turn_user_id(room)
        if current_uid != user_id:
            raise ValueError("还没轮到你操作")

        player = room.players[user_id]
        if player.status != "playing":
            raise ValueError("你当前无法停牌")

        player.status = "stood"
        self._advance_turn(room)

        self._touch(room)
        return self._to_room_state(room)

    def settle_if_finished(self, room_id: str) -> Dict[str, Any]:
        """
        仅在房间状态 finished 且尚未提交结算时返回一次性结算数据。
        返回:
            {
                "committed": bool,
                "payouts": {user_id: payout_amount(>0)},
                "bet_total": int,
                "payout_total": int
            }
        """
        room = self._get_room_or_raise(room_id)
        bet_total = sum(p.bet_amount for p in room.players.values() if p.bet_amount > 0)
        payout_total = sum(p.payout_amount for p in room.players.values() if p.bet_amount > 0)

        if room.state != "finished" or room.payouts_committed:
            return {
                "committed": False,
                "payouts": {},
                "bet_total": bet_total,
                "payout_total": payout_total,
            }

        room.payouts_committed = True
        self._touch(room)
        payouts = {
            p.user_id: p.payout_amount
            for p in room.players.values()
            if p.bet_amount > 0 and p.payout_amount > 0
        }
        return {
            "committed": True,
            "payouts": payouts,
            "bet_total": bet_total,
            "payout_total": payout_total,
        }

    def get_round_bet_total(self, room_id: str) -> int:
        room = self._get_room_or_raise(room_id)
        return sum(p.bet_amount for p in room.players.values() if p.bet_amount > 0)

    def get_round_payout_total(self, room_id: str) -> int:
        room = self._get_room_or_raise(room_id)
        return sum(p.payout_amount for p in room.players.values() if p.bet_amount > 0)

    def _current_turn_user_id(self, room: MultiplayerRoom) -> Optional[int]:
        if room.state != "playing" or not room.turn_order:
            return None
        if room.current_turn_index >= len(room.turn_order):
            return None
        return room.turn_order[room.current_turn_index]

    def _advance_turn(self, room: MultiplayerRoom) -> None:
        if room.state != "playing":
            return

        next_index = room.current_turn_index + 1
        while next_index < len(room.turn_order):
            uid = room.turn_order[next_index]
            player = room.players.get(uid)
            if player and player.status == "playing":
                room.current_turn_index = next_index
                return
            next_index += 1

        self._resolve_dealer_and_settle(room)

    def _resolve_dealer_and_settle(self, room: MultiplayerRoom) -> None:
        room.state = "dealer_turn"

        while _calculate_hand_score(room.dealer_hand) < 17:
            room.dealer_hand.append(room.deck.pop())

        dealer_score = _calculate_hand_score(room.dealer_hand)
        dealer_bust = dealer_score > 21

        for player in room.players.values():
            if player.bet_amount <= 0:
                continue

            player_score = _calculate_hand_score(player.hand)

            if player.status == "blackjack":
                player.result = "blackjack"
                player.payout_amount = int(player.bet_amount * 2.5)
            elif player.status == "bust":
                player.result = "loss"
                player.payout_amount = 0
            else:
                if dealer_bust or player_score > dealer_score:
                    player.result = "win"
                    player.payout_amount = player.bet_amount * 2
                elif player_score == dealer_score:
                    player.result = "push"
                    player.payout_amount = player.bet_amount
                else:
                    player.result = "loss"
                    player.payout_amount = 0

            player.status = "finished"

        room.turn_order = []
        room.current_turn_index = 0
        room.state = "finished"

    def _reset_round(self, room: MultiplayerRoom) -> None:
        room.state = "waiting"
        room.deck = []
        room.dealer_hand = []
        room.turn_order = []
        room.current_turn_index = 0
        room.payouts_committed = False

        for player in room.players.values():
            player.hand = []
            player.status = "waiting"
            player.result = None
            player.payout_amount = 0
            player.bet_amount = 0
            player.is_ready = False


multiplayer_blackjack_service = MultiplayerBlackjackService()