from uuid import uuid4
import random
import string
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Set


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


def _companion_should_hit(hand: List[str], dealer_upcard: str) -> bool:
    """陪玩按自己的软硬点数和荷官明牌决策；多人规则仅支持要牌与停牌。"""
    score = _calculate_hand_score(hand)
    if score >= 21:
        return False
    minimum = sum(1 if card.endswith("A") else _get_card_value(card) for card in hand)
    soft = score != minimum
    upcard = _get_card_value(dealer_upcard)
    if soft:
        # 软 17 仍可安全改善；软 18 遇到荷官强明牌时继续要牌。
        return score <= 17 or (score == 18 and upcard >= 9)
    if score >= 17:
        return False
    if score <= 11:
        return True
    if score == 12:
        return upcard not in (4, 5, 6)
    return upcard not in (2, 3, 4, 5, 6)


@dataclass
class MultiplayerPlayerState:
    user_id: int
    username: str
    avatar_url: str
    seat_index: int
    bet_amount: int = 0
    last_bet_amount: int = 0
    hand: List[str] = field(default_factory=list)
    status: str = "waiting"  # waiting | playing | stood | bust | blackjack | finished
    result: Optional[str] = None  # win | loss | push | blackjack
    payout_amount: int = 0
    is_ready: bool = False
    is_bot: bool = False


@dataclass
class MultiplayerRoom:
    room_id: str
    host_user_id: int
    turn_timeout_seconds: int = 60
    players: Dict[int, MultiplayerPlayerState] = field(default_factory=dict)
    state: str = "waiting"  # waiting | playing | dealer_turn | finished
    deck: List[str] = field(default_factory=list)
    dealer_hand: List[str] = field(default_factory=list)
    turn_order: List[int] = field(default_factory=list)
    current_turn_index: int = 0
    turn_deadline: Optional[float] = None
    payouts_committed: bool = False
    committed_payout_user_ids: Set[int] = field(default_factory=set)
    forfeited_bet_total: int = 0
    round_key: str = field(default_factory=lambda: f"blackjack:multi:{uuid4().hex}")
    updated_at: float = field(default_factory=lambda: time.time())


class MultiplayerBlackjackService:
    MAX_PLAYERS = 3
    TURN_TIMEOUT_SECONDS = 60
    YUEYUE_USER_ID = -1

    def __init__(self):
        self._rooms: Dict[str, MultiplayerRoom] = {}
        self.bot_action_provider = None

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
        self._drive_bot_turns(room)
        self._expire_turn_if_needed(room)
        self._drive_bot_turns(room)
        return room

    def _to_player_dict(self, player: MultiplayerPlayerState, room: MultiplayerRoom) -> Dict[str, Any]:
        current_turn_user_id = None
        if room.state == "playing" and room.turn_order and room.current_turn_index < len(room.turn_order):
            current_turn_user_id = room.turn_order[room.current_turn_index]

        return {
            "user_id": str(player.user_id),
            "username": player.username,
            "avatar_url": player.avatar_url,
            "seat_index": player.seat_index,
            "bet_amount": player.bet_amount,
            "last_bet_amount": player.last_bet_amount,
            "hand": list(player.hand),
            "score": _calculate_hand_score(player.hand),
            "status": player.status,
            "result": player.result,
            "payout_amount": player.payout_amount,
            "is_ready": player.is_ready,
            "is_bot": player.is_bot,
            "is_current_turn": current_turn_user_id == player.user_id,
        }

    def _to_room_state(self, room: MultiplayerRoom) -> Dict[str, Any]:
        self._drive_bot_turns(room)
        players = sorted(room.players.values(), key=lambda p: p.seat_index)

        current_turn_user_id = None
        if room.state == "playing" and room.turn_order and room.current_turn_index < len(room.turn_order):
            current_turn_user_id = room.turn_order[room.current_turn_index]

        dealer_expression = self._resolve_dealer_expression(room)
        show_all_dealer_cards = room.state in ("dealer_turn", "finished")

        if show_all_dealer_cards:
            dealer_hand = list(room.dealer_hand)
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
            "host_user_id": str(room.host_user_id),
            "max_players": self.MAX_PLAYERS,
            "include_yueyue": self.YUEYUE_USER_ID in room.players,
            "state": room.state,
            "current_turn_user_id": (
                str(current_turn_user_id) if current_turn_user_id is not None else None
            ),
            "turn_deadline": room.turn_deadline,
            "turn_timeout_seconds": room.turn_timeout_seconds,
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

    def llm_public_state(self, room: MultiplayerRoom) -> Dict[str, Any]:
        """为陪玩提供只读局面，不推进牌局，也不暴露荷官暗牌或牌堆。"""
        dealer_hand = list(room.dealer_hand[:1])
        if len(room.dealer_hand) > 1:
            dealer_hand.append("Hidden")
        return {
            "state": room.state,
            "current_turn_user_id": str(self._current_turn_user_id(room)) if self._current_turn_user_id(room) is not None else None,
            "dealer": {"hand": dealer_hand},
            "players": [{
                "user_id": str(player.user_id), "hand": list(player.hand),
                "score": _calculate_hand_score(player.hand), "status": player.status,
                "bet_amount": player.bet_amount,
            } for player in sorted(room.players.values(), key=lambda item: item.seat_index)],
        }

    def list_rooms(self, viewer_id: int) -> List[Dict[str, Any]]:
        """房间列表仅暴露大厅信息，不能因浏览列表自动发牌或派彩。"""
        rooms = []
        for room in self._rooms.values():
            host = room.players.get(room.host_user_id)
            if host is None or not any(not p.is_bot for p in room.players.values()):
                continue
            is_member = viewer_id in room.players and not room.players[viewer_id].is_bot
            rooms.append({
                "room_id": room.room_id,
                "game_type": "blackjack",
                "host_username": host.username,
                "host_avatar_url": host.avatar_url,
                "state": room.state,
                "player_count": len(room.players),
                "max_players": self.MAX_PLAYERS,
                "room_tier": "custom",
                "base_stake": None,
                "entry_min": 0,
                "loss_limit": None,
                "turn_timeout_seconds": room.turn_timeout_seconds,
                "is_member": is_member,
                "can_join": is_member or (
                    room.state in ("waiting", "finished") and len(room.players) < self.MAX_PLAYERS
                ),
                "updated_at": room.updated_at,
            })
        return rooms

    def create_room(self, user_id: int, username: str, avatar_url: str, turn_timeout_seconds: int = 60) -> Dict[str, Any]:
        self._validate_turn_timeout(turn_timeout_seconds)
        if user_id == self.YUEYUE_USER_ID:
            raise ValueError("该用户编号保留给月月陪玩")
        room_id = self._generate_room_id()
        room = MultiplayerRoom(room_id=room_id, host_user_id=user_id, turn_timeout_seconds=turn_timeout_seconds)
        room.players[user_id] = MultiplayerPlayerState(
            user_id=user_id,
            username=username,
            avatar_url=avatar_url,
            seat_index=0,
        )
        self._rooms[room_id] = room
        self._touch(room)
        return self._to_room_state(room)

    @staticmethod
    def _validate_turn_timeout(seconds: int) -> None:
        if type(seconds) is not int or not 15 <= seconds <= 300:
            raise ValueError("操作等待时长必须为15至300秒的整数")

    def settings(self, room_id: str, user_id: int, turn_timeout_seconds: int) -> Dict[str, Any]:
        self._validate_turn_timeout(turn_timeout_seconds)
        room = self._get_room_or_raise(room_id)
        if room.host_user_id != user_id:
            raise PermissionError("只有房主可以修改房间设置")
        if room.state in ("playing", "dealer_turn"):
            raise ValueError("请在本局结束后修改等待时长")
        if room.turn_timeout_seconds != turn_timeout_seconds:
            room.turn_timeout_seconds = turn_timeout_seconds
            for player in room.players.values():
                if not player.is_bot:
                    player.is_ready = False
            self._touch(room)
        return self._to_room_state(room)

    def join_room(self, room_id: str, user_id: int, username: str, avatar_url: str) -> Dict[str, Any]:
        if user_id == self.YUEYUE_USER_ID:
            raise ValueError("该用户编号保留给月月陪玩")
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

    def configure_bot(self, room_id: str, host_user_id: int, include_yueyue: bool) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)
        if host_user_id != room.host_user_id:
            raise ValueError("只有房主可以配置月月陪玩")
        if type(include_yueyue) is not bool:
            raise ValueError("是否加入月月必须为布尔值")
        if room.state not in ("waiting", "finished"):
            raise ValueError("本局进行中，不能增减月月陪玩")
        if room.state == "finished" and not room.payouts_committed:
            raise ValueError("本局仍在结算，请稍后配置月月陪玩")
        if include_yueyue and self.YUEYUE_USER_ID not in room.players:
            if len(room.players) >= self.MAX_PLAYERS:
                raise ValueError("房间人数已满（最多3人），请先腾出一个座位")
            used_seats = {player.seat_index for player in room.players.values()}
            seat_index = next(index for index in range(self.MAX_PLAYERS) if index not in used_seats)
            room.players[self.YUEYUE_USER_ID] = MultiplayerPlayerState(
                user_id=self.YUEYUE_USER_ID,
                username="月月（陪玩）",
                avatar_url="/character/normal.webp",
                seat_index=seat_index,
                is_ready=True,
                is_bot=True,
            )
        elif not include_yueyue:
            room.players.pop(self.YUEYUE_USER_ID, None)
        if room.state == "waiting":
            self._sync_bot_bet(room)
        self._touch(room)
        return self._to_room_state(room)

    def _sync_bot_bet(self, room: MultiplayerRoom) -> None:
        bot = room.players.get(self.YUEYUE_USER_ID)
        host = room.players.get(room.host_user_id)
        if bot and host and room.state == "waiting":
            bot.bet_amount = host.bet_amount
            bot.last_bet_amount = host.bet_amount
            bot.is_ready = True

    def get_room_state(self, room_id: str) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)
        return self._to_room_state(room)

    def leave_room(self, room_id: str, user_id: int) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)

        if user_id not in room.players:
            return self._to_room_state(room)

        previous_turn_user_id = self._current_turn_user_id(room)
        departing_player = room.players.pop(user_id)
        if room.state in ("playing", "dealer_turn") and not departing_player.is_bot:
            room.forfeited_bet_total += departing_player.bet_amount

        # 真人全部离开时关闭房间，不能留下机器人主持的孤立牌桌。
        human_players = [player for player in room.players.values() if not player.is_bot]
        if not human_players:
            del self._rooms[room_id]
            return {"room_closed": True, "room_id": room_id}

        # 主持人离开时转移主持权
        if room.host_user_id == user_id:
            room.host_user_id = min(
                human_players, key=lambda player: player.seat_index
            ).user_id
            self._sync_bot_bet(room)

        # 若游戏中离开，移除其行动位；玩家已下注则默认判负（不退赌注）
        if room.state in ("playing", "dealer_turn"):
            # 已操作玩家不应因前方座位离开而再次获得回合，也不能跳过下一位。
            room.turn_order = [
                uid
                for uid in room.turn_order
                if uid in room.players and room.players[uid].status == "playing"
            ]
            room.current_turn_index = 0
            if not room.turn_order:
                self._resolve_dealer_and_settle(room)
            elif self._current_turn_user_id(room) != previous_turn_user_id:
                self._start_turn_timer(room)

        self._touch(room)
        return self._to_room_state(room)

    def set_bet(self, room_id: str, user_id: int, amount: int) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)
        player = room.players.get(user_id)
        if not player:
            raise ValueError("你不在该房间中")
        if player.is_bot:
            raise ValueError("月月陪玩的下注随房主自动调整")

        if amount <= 0:
            raise ValueError("下注金额必须大于0")

        if room.state in ("playing", "dealer_turn"):
            raise ValueError("本局进行中，无法修改下注")

        # 新一局下注前清理旧局信息
        if room.state == "finished":
            self._reset_round(room)

        player.bet_amount = amount
        player.last_bet_amount = amount
        player.is_ready = False
        self._sync_bot_bet(room)
        self._touch(room)
        return self._to_room_state(room)

    def set_ready(self, room_id: str, user_id: int, ready: bool) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)
        player = room.players.get(user_id)
        if not player:
            raise ValueError("你不在该房间中")
        if player.is_bot:
            raise ValueError("月月陪玩自动准备")

        if room.state in ("playing", "dealer_turn"):
            raise ValueError("本局进行中，无法修改准备状态")

        if room.state == "finished":
            raise ValueError("请先下注或沿用上局下注，再准备新一局")

        if ready and player.bet_amount <= 0:
            raise ValueError("请先下注再准备")

        player.is_ready = bool(ready)
        self._touch(room)
        return self._to_room_state(room)

    def continue_ready(self, room_id: str, user_id: int) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)
        player = room.players.get(user_id)
        if not player:
            raise ValueError("你不在该房间中")
        if player.is_bot:
            raise ValueError("月月陪玩的下注随房主自动调整")

        if room.state in ("playing", "dealer_turn"):
            raise ValueError("本局进行中，暂时无法继续准备")

        target_bet = int(player.last_bet_amount or 0)
        if target_bet <= 0:
            raise ValueError("你上一局没有有效下注，请先手动设置下注")

        if room.state == "finished":
            self._reset_round(room)

        player.bet_amount = target_bet
        player.is_ready = True
        self._sync_bot_bet(room)
        self._touch(room)
        return self._to_room_state(room)

    def start_round(self, room_id: str, user_id: int) -> Dict[str, Any]:
        room = self._get_room_or_raise(room_id)
        if room.host_user_id != user_id:
            raise ValueError("只有房主可以开始游戏")

        if room.state in ("playing", "dealer_turn"):
            raise ValueError("本局游戏已经开始")

        if room.state == "finished":
            raise ValueError("请先完成新一局下注并准备")

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

        participants = sorted(
            (p for p in room.players.values() if p.bet_amount > 0),
            key=lambda player: player.seat_index,
        )
        if not participants:
            raise ValueError("至少需要一名已下注玩家才能开始")

        room.deck = _create_deck()
        random.shuffle(room.deck)
        room.dealer_hand = [room.deck.pop(), room.deck.pop()]
        room.turn_order = []
        room.current_turn_index = 0
        room.turn_deadline = None
        room.payouts_committed = False
        room.committed_payout_user_ids.clear()
        room.forfeited_bet_total = 0
        room.round_key = f"blackjack:multi:{uuid4().hex}"

        for p in participants:
            p.hand = [room.deck.pop(), room.deck.pop()]
            score = _calculate_hand_score(p.hand)
            p.result = None
            p.payout_amount = 0
            p.is_ready = p.is_bot
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
                p.is_ready = p.is_bot

        room.state = "playing"

        if _calculate_hand_score(room.dealer_hand) == 21 or not room.turn_order:
            self._resolve_dealer_and_settle(room)
        else:
            self._start_turn_timer(room)

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
        返回尚未派彩的玩家。调用方必须在实际入账后逐人确认，最终确认整局。
        返回:
            {
                "committed": bool,
                "payouts": {user_id: payout_amount(>0)},
                "bet_total": int,
                "payout_total": int
            }
        """
        room = self._get_room_or_raise(room_id)
        bet_total = room.forfeited_bet_total + sum(
            p.bet_amount for p in room.players.values() if p.bet_amount > 0 and not p.is_bot
        )
        payout_total = sum(p.payout_amount for p in room.players.values() if p.bet_amount > 0 and not p.is_bot)

        if room.state != "finished" or room.payouts_committed:
            return {
                "committed": False,
                "payouts": {},
                "bet_total": bet_total,
                "payout_total": payout_total,
            }

        payouts = {
            p.user_id: p.payout_amount
            for p in room.players.values()
            if p.bet_amount > 0 and p.payout_amount > 0 and not p.is_bot
            and p.user_id not in room.committed_payout_user_ids
        }
        return {
            "committed": True,
            "payouts": payouts,
            "bet_total": bet_total,
            "payout_total": payout_total,
        }

    def mark_payout_committed(self, room_id: str, user_id: int) -> None:
        room = self._get_room_or_raise(room_id)
        if room.state != "finished":
            raise ValueError("本局尚未结束，无法确认派彩")
        player = room.players.get(user_id)
        if not player or player.is_bot or player.payout_amount <= 0:
            raise ValueError("该玩家没有待确认的派彩")
        room.committed_payout_user_ids.add(user_id)
        self._touch(room)

    def mark_round_committed(self, room_id: str) -> None:
        room = self._get_room_or_raise(room_id)
        if room.state != "finished":
            raise ValueError("本局尚未结束，无法确认结算")
        if any(
            not player.is_bot and player.payout_amount > 0
            and player.user_id not in room.committed_payout_user_ids
            for player in room.players.values()
        ):
            raise ValueError("尚有玩家派彩未完成，无法确认结算")
        room.payouts_committed = True
        self._touch(room)

    def get_round_bet_total(self, room_id: str) -> int:
        room = self._get_room_or_raise(room_id)
        return room.forfeited_bet_total + sum(
            p.bet_amount for p in room.players.values() if p.bet_amount > 0 and not p.is_bot
        )

    def get_round_payout_total(self, room_id: str) -> int:
        room = self._get_room_or_raise(room_id)
        return sum(p.payout_amount for p in room.players.values() if p.bet_amount > 0 and not p.is_bot)

    def _drive_bot_turns(self, room: MultiplayerRoom) -> None:
        """月月按自身手牌与荷官明牌行动，然后立即交回下一真人。"""
        while room.state == "playing":
            player = room.players.get(self._current_turn_user_id(room))
            if not player or not player.is_bot:
                return
            if self.bot_action_provider is not None:
                action = self.bot_action_provider(room, player.user_id)
                if action is not None:
                    self.apply_bot_action(room, action)
                # 模型每次只执行一步，要牌后的新决策由下一次轮询申请。
                return
            while _companion_should_hit(player.hand, room.dealer_hand[0]):
                player.hand.append(room.deck.pop())
            if _calculate_hand_score(player.hand) > 21:
                player.status = "bust"
                player.result = "loss"
            else:
                player.status = "stood"
            self._advance_turn(room)
            self._touch(room)

    def apply_bot_action(self, room: MultiplayerRoom, action: dict) -> None:
        """执行已校验的单次陪玩动作，可在房间副本上验证，不涉及账户派彩。"""
        if not isinstance(action, dict) or set(action) != {"action"} or action["action"] not in ("hit", "stand"):
            raise ValueError("陪玩动作只能为 hit 或 stand")
        player = room.players.get(self._current_turn_user_id(room))
        if room.state != "playing" or player is None or not player.is_bot or player.status != "playing":
            raise ValueError("当前没有可行动的陪玩")
        if action["action"] == "stand":
            player.status = "stood"
            self._advance_turn(room)
        else:
            if not room.deck:
                raise ValueError("牌堆已空，无法要牌")
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

    def _current_turn_user_id(self, room: MultiplayerRoom) -> Optional[int]:
        if room.state != "playing" or not room.turn_order:
            return None
        if room.current_turn_index >= len(room.turn_order):
            return None
        return room.turn_order[room.current_turn_index]

    def _start_turn_timer(self, room: MultiplayerRoom) -> None:
        room.turn_deadline = time.time() + room.turn_timeout_seconds

    def _expire_turn_if_needed(self, room: MultiplayerRoom) -> None:
        if (
            room.state != "playing"
            or room.turn_deadline is None
            or time.time() < room.turn_deadline
        ):
            return
        player = room.players.get(self._current_turn_user_id(room))
        if player and player.is_bot and self.bot_action_provider is not None:
            # 模型等待由异步请求超时及算法回退控制，不能套用真人自动停牌。
            return
        if player and player.status == "playing":
            # 断线或长时间不操作时自动停牌，其他玩家可以继续本局。
            player.status = "stood"
        self._advance_turn(room)
        self._touch(room)

    def _advance_turn(self, room: MultiplayerRoom) -> None:
        if room.state != "playing":
            return

        next_index = room.current_turn_index + 1
        while next_index < len(room.turn_order):
            uid = room.turn_order[next_index]
            player = room.players.get(uid)
            if player and player.status == "playing":
                room.current_turn_index = next_index
                self._start_turn_timer(room)
                return
            next_index += 1

        self._resolve_dealer_and_settle(room)

    def _resolve_dealer_and_settle(self, room: MultiplayerRoom) -> None:
        room.state = "dealer_turn"

        while _calculate_hand_score(room.dealer_hand) < 17:
            room.dealer_hand.append(room.deck.pop())

        dealer_score = _calculate_hand_score(room.dealer_hand)
        dealer_bust = dealer_score > 21
        dealer_blackjack = len(room.dealer_hand) == 2 and dealer_score == 21

        for player in room.players.values():
            if player.bet_amount <= 0:
                continue

            player_score = _calculate_hand_score(player.hand)

            if dealer_blackjack:
                # 双方天然黑杰克为平局，补牌得到的 21 点不能与之打平。
                player.result = "push" if player.status == "blackjack" else "loss"
                player.payout_amount = (
                    player.bet_amount if player.result == "push" else 0
                )
            elif player.status == "blackjack":
                player.result = "blackjack"
                player.payout_amount = player.bet_amount * 5 // 2
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
        room.turn_deadline = None
        room.state = "finished"

    def _reset_round(self, room: MultiplayerRoom) -> None:
        if room.state == "finished" and not room.payouts_committed:
            raise ValueError("上一局仍在结算，请稍后再开始新一局")
        room.state = "waiting"
        room.deck = []
        room.dealer_hand = []
        room.turn_order = []
        room.current_turn_index = 0
        room.turn_deadline = None
        room.payouts_committed = False
        room.committed_payout_user_ids.clear()
        room.forfeited_bet_total = 0
        room.round_key = f"blackjack:multi:{uuid4().hex}"

        for player in room.players.values():
            player.hand = []
            player.status = "waiting"
            player.result = None
            player.payout_amount = 0
            player.bet_amount = 0
            player.is_ready = player.is_bot


multiplayer_blackjack_service = MultiplayerBlackjackService()
