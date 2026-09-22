"""桌游房间管理：隔离手牌、月月陪玩、回合超时和重连。"""

from dataclasses import dataclass, field
from copy import deepcopy
from importlib import import_module
import logging
import random
import secrets
import time
import uuid
from typing import Any


log = logging.getLogger(__name__)


GAME_SPECS = {
    "texas": (2, 8, "poker_games", "TexasHoldemGame"),
    "golden_flower": (2, 5, "poker_games", "GoldenFlowerGame"),
    "landlord": (3, 3, "traditional_games", "LandlordGame"),
    "guandan": (4, 4, "guandan_game", "GuandanGame"),
    "mahjong": (4, 4, "traditional_games", "MahjongGame"),
    "sichuan_mahjong": (4, 4, "sichuan_mahjong", "SichuanMahjongGame"),
}
MAX_TABLE_AMOUNT = (2**53 - 1) // 8
ROOM_TIERS = {
    "beginner": (1, 100, 100),
    "intermediate": (5, 1000, 500),
    "advanced": (20, 5000, 2000),
}


def room_settings(room_tier: str, base_stake: int | None = None, loss_limit: int | None = None) -> tuple[int, int, int]:
    """返回底分、准入余额和单局损失上限，固定场次禁止覆写。"""
    if room_tier in ROOM_TIERS:
        if base_stake is not None or loss_limit is not None:
            raise ValueError("固定场次不能修改底分或单局上限")
        return ROOM_TIERS[room_tier]
    if room_tier != "custom":
        raise ValueError("不支持的场次")
    base_stake = 1 if base_stake is None else base_stake
    loss_limit = 100 if loss_limit is None else loss_limit
    if type(base_stake) is not int or not 1 <= base_stake <= MAX_TABLE_AMOUNT // 10:
        raise ValueError("底分必须是安全范围内的正整数")
    if type(loss_limit) is not int or not max(100, 10 * base_stake) <= loss_limit <= MAX_TABLE_AMOUNT:
        raise ValueError("单局最多输须为整数，至少100灵石且不低于底分的10倍")
    return base_stake, loss_limit, loss_limit


def validate_game_stake(game_type: str, base_stake: int):
    """在建房/改房时拒绝会使累计番分超出前端整数精度的底分。"""
    if game_type == "sichuan_mahjong":
        rules = import_module("src.chat.features.games.blackjack-web.sichuan_mahjong")
        if base_stake > rules.MAX_BASE_STAKE:
            raise ValueError(f"四川血战底分不能超过 {rules.MAX_BASE_STAKE}，以保证累计分数精度")


class StaleTableAction(ValueError):
    """客户端操作对应的回合已更新。"""


@dataclass
class TablePlayer:
    user_id: str
    username: str
    avatar_url: str
    is_bot: bool = False
    is_ready: bool = False
    connected: bool = True


@dataclass
class GameTable:
    room_id: str
    game_type: str
    host_user_id: str
    mode: str
    buy_in: int = 100
    players: dict[str, TablePlayer] = field(default_factory=dict)
    state: str = "waiting"
    revision: int = 0
    engine: Any = None
    turn_deadline: float | None = None
    updated_at: float = field(default_factory=time.time)
    last_turn: str | None = None
    round_number: int = 0
    escrow_key: str | None = None
    settlement_status: str = "none"
    actual_settlement: dict[str, int] = field(default_factory=dict)
    room_tier: str = "beginner"
    base_stake: int = 1
    entry_min: int = 100
    next_bot_number: int = 1
    auto_start_when_ready: bool = False
    removed_user_ids: set[str] = field(default_factory=set)
    turn_timeout_seconds: int = 60
    started_at: float | None = None
    public_action_history: list[dict] = field(default_factory=list)
    history_deal_count: int = 0
    history_truncated: bool = False


class TableService:
    TURN_SECONDS = 60
    BOT_DELAY_SECONDS = 0.8
    EMPTY_ROOM_TTL = 3600
    MAX_ROOMS = 1000
    STAKE = 100

    def __init__(self, clock=time.time):
        self.rooms: dict[str, GameTable] = {}
        self.clock = clock
        self.bot_action_provider = None
        self.action_observer = None

    def _observe_action(self, room: GameTable, user_id: str, action: str, payload: dict):
        """成功执行后记录公开动作；记忆失败不能中断牌局和资金流程。"""
        try:
            state = room.engine.public_state(str(user_id))
            deal_count = int(state.get("deal_count", 0))
            redealt = room.game_type == "landlord" and deal_count != room.history_deal_count
            if redealt:
                room.public_action_history.clear()
                room.history_truncated = False
            room.history_deal_count = deal_count
            if not (redealt and action == "bid" and not state.get("bids")):
                event = {"user_id": str(user_id), "action": action, "time": self.clock()}
                public_fields = {"raise": ("amount",), "bid": ("bid",), "play": ("cards",),
                                 "discard": ("tile",), "chow": ("tiles",), "compare": ("target_id",)}
                for key in public_fields.get(action, ()):
                    if key in payload:
                        event[key] = deepcopy(payload[key])
                if room.game_type == "guandan" and action == "play":
                    played = state.get("last_play") or {}
                    event.update({key: played[key] for key in ("combo", "kind", "name") if key in played})
                if room.game_type in {"texas", "golden_flower"}:
                    actor = next(player for player in state["players"] if str(player["user_id"]) == str(user_id))
                    event.update({key: state[key] for key in ("phase", "pot") if key in state})
                    event.update({key: actor[key] for key in ("stack", "total_bet", "round_bet") if key in actor})
                if len(room.public_action_history) < 1000:
                    room.public_action_history.append(event)
                else:
                    room.history_truncated = True
        except Exception as exc:
            log.warning("桌游公开记录更新失败，原因类型=%s", type(exc).__name__)
        if self.action_observer is None:
            return
        try:
            self.action_observer(room, user_id, action, dict(payload))
        except Exception as exc:
            log.warning("桌游动作记忆更新失败，原因类型=%s", type(exc).__name__)

    def _room(self, room_id: str) -> GameTable:
        room = self.rooms.get(str(room_id).strip().upper())
        if room is None:
            raise ValueError("房间不存在或已关闭")
        return room

    @staticmethod
    def _member(room: GameTable, user_id: str) -> TablePlayer:
        player = room.players.get(str(user_id))
        if player is None or player.is_bot:
            raise PermissionError("请先加入该房间")
        return player

    def _changed(self, room: GameTable):
        room.revision += 1
        room.updated_at = self.clock()

    def _set_turn(self, room: GameTable):
        if room.engine.finished:
            room.state = "finished"
            room.turn_deadline = None
            room.last_turn = None
            for player in room.players.values():
                player.is_ready = player.is_bot
            return
        current = str(room.engine.current_player_id)
        room.last_turn = current
        player = room.players.get(current)
        is_automatic = player is not None and (player.is_bot or not player.connected)
        room.turn_deadline = self.clock() + (
            self.BOT_DELAY_SECONDS if is_automatic else room.turn_timeout_seconds
        )

    def _advance_due_turn(self, room: GameTable):
        """每次轮询最多推进一个动作，保证真人能看清 AI 出牌且请求不会长时间占用。"""
        if room.state != "playing":
            return
        if room.engine.finished:
            self._set_turn(room)
            self._changed(room)
            return
        if room.turn_deadline is None or self.clock() < room.turn_deadline:
            return
        user_id = str(room.engine.current_player_id)
        player = room.players.get(user_id)
        if player is not None and player.is_bot and self.bot_action_provider is not None:
            proposed = self.bot_action_provider(room, user_id)
            if proposed is None:
                return
            suggestion = dict(proposed)
        else:
            suggestion = dict(room.engine.suggest_action(user_id))
        action = suggestion.pop("action")
        room.engine.act(user_id, action, **suggestion)
        self._observe_action(room, user_id, action, suggestion)
        self._changed(room)
        self._set_turn(room)

    def _snapshot(self, room: GameTable, viewer_id: str) -> dict:
        minimum, maximum, _, _ = GAME_SPECS[room.game_type]
        game_state = room.engine.public_state(str(viewer_id)) if room.engine else None
        if game_state:
            message = str(game_state.get("message") or "")
            for uid, player in sorted(room.players.items(), key=lambda item: -len(item[0])):
                message = message.replace(f"玩家 {uid}", player.username)
            game_state["message"] = message
        return {
            "room_id": room.room_id,
            "game_type": room.game_type,
            "host_user_id": room.host_user_id,
            "mode": room.mode,
            "state": room.state,
            "revision": room.revision,
            "round_number": room.round_number,
            "stake": room.buy_in,
            "buy_in": room.buy_in,
            "room_tier": room.room_tier,
            "base_stake": room.base_stake,
            "entry_min": room.entry_min,
            "loss_limit": room.buy_in,
            "auto_start_when_ready": room.auto_start_when_ready,
            "turn_timeout_seconds": room.turn_timeout_seconds,
            "last_public_action": ({**deepcopy(room.public_action_history[-1]),
                                    "id": f"{room.round_number}:{room.history_deal_count}:{len(room.public_action_history)}"}
                                   if room.public_action_history else None),
            "settlement_status": room.settlement_status,
            "actual_settlement": dict(room.actual_settlement),
            "include_yueyue": "bot:yueyue" in room.players,
            "min_players": minimum,
            "max_players": maximum,
            "players": [vars(player).copy() for player in room.players.values()],
            "game": game_state,
            "turn_deadline": room.turn_deadline,
            "server_time": self.clock(),
        }

    def list_rooms(self, viewer_id: str, game_type: str | None = None) -> list[dict]:
        """大厅只读取多人房间摘要，不读取手牌，也不推进回合或触发结算。"""
        viewer_id = str(viewer_id)
        rooms = []
        other_memberships = {
            room.room_id for room in self.rooms.values()
            if viewer_id in room.players and room.players[viewer_id].connected
        }
        for room in self.rooms.values():
            if room.mode != "multi" or (game_type and room.game_type != game_type):
                continue
            connected_human = any(p.connected and not p.is_bot for p in room.players.values())
            if not connected_human and room.settlement_status != "reserved" and self.clock() - room.updated_at > self.EMPTY_ROOM_TTL:
                continue
            is_member = viewer_id in room.players and not room.players[viewer_id].is_bot
            if not is_member and not any(p.connected and not p.is_bot for p in room.players.values()):
                continue
            host = room.players.get(room.host_user_id)
            if host is None:
                continue
            maximum = GAME_SPECS[room.game_type][1]
            # 与 join 保持一致：普通陪玩可让位，月月席位保留给房主手动调整。
            has_seat = len(room.players) < maximum or any(
                p.is_bot and p.user_id != "bot:yueyue" for p in room.players.values()
            )
            can_join = not (other_memberships - {room.room_id}) and (
                is_member or (room.state != "playing" and has_seat)
            )
            rooms.append({
                "room_id": room.room_id,
                "game_type": room.game_type,
                "host_username": host.username,
                "host_avatar_url": host.avatar_url,
                "state": room.state,
                "player_count": len(room.players),
                "max_players": maximum,
                "room_tier": room.room_tier,
                "base_stake": room.base_stake,
                "entry_min": room.entry_min,
                "loss_limit": room.buy_in,
                "turn_timeout_seconds": room.turn_timeout_seconds,
                "is_member": is_member,
                "can_join": can_join,
                "updated_at": room.updated_at,
            })
        return rooms

    def create(self, user: dict, game_type: str, mode: str, include_yueyue: bool,
               room_tier: str = "beginner", base_stake: int | None = None,
               loss_limit: int | None = None, auto_start_when_ready: bool = False,
               turn_timeout_seconds: int = 60) -> dict:
        if game_type not in GAME_SPECS:
            raise ValueError("不支持的游戏类型")
        if mode not in ("solo", "multi"):
            raise ValueError("不支持的游戏模式")
        if type(auto_start_when_ready) is not bool:
            raise ValueError("自动开局设置必须为布尔值")
        if type(turn_timeout_seconds) is not int or not 15 <= turn_timeout_seconds <= 300:
            raise ValueError("操作等待时长须为 15 至 300 秒的整数")
        base_stake, entry_min, loss_limit = room_settings(room_tier, base_stake, loss_limit)
        validate_game_stake(game_type, base_stake)
        user_id = str(user["user_id"])
        # 重复创建请求返回已有房间，刷新不会制造无人可找回的牌局。
        for room in self.rooms.values():
            if user_id in room.players and room.players[user_id].connected:
                if room.game_type == game_type:
                    return self._snapshot(room, user_id)
                raise ValueError("请先离开当前桌游房间")
        self._cleanup()
        if len(self.rooms) >= self.MAX_ROOMS:
            raise ValueError("房间暂时已满，请稍后再试")
        room_id = ""
        while not room_id or room_id in self.rooms:
            room_id = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        room = GameTable(room_id, game_type, user_id, mode, loss_limit,
                         room_tier=room_tier, base_stake=base_stake, entry_min=entry_min,
                         auto_start_when_ready=auto_start_when_ready, turn_timeout_seconds=turn_timeout_seconds)
        room.players[user_id] = TablePlayer(user_id, str(user["username"]), str(user["avatar_url"]))
        self.rooms[room_id] = room
        bot_count = GAME_SPECS[game_type][0] - 1 if mode == "solo" else int(include_yueyue)
        self._add_bots(room, bot_count)
        self._changed(room)
        return self._snapshot(room, user_id)

    def _cleanup(self):
        for room_id, room in list(self.rooms.items()):
            if room.settlement_status != "reserved" and self.clock() - room.updated_at > self.EMPTY_ROOM_TTL and not any(
                p.connected and not p.is_bot for p in room.players.values()
            ):
                del self.rooms[room_id]

    def join(self, room_id: str, user: dict) -> dict:
        room = self._room(room_id)
        user_id = str(user["user_id"])
        for other in self.rooms.values():
            if other is not room and user_id in other.players and other.players[user_id].connected:
                raise ValueError("请先离开当前桌游房间")
        if user_id in room.players:
            player = room.players[user_id]
            if player.is_bot:
                raise PermissionError("机器人座位不能用于登录")
            player.connected = True
            player.username = str(user["username"])
            player.avatar_url = str(user["avatar_url"])
            # 重连保留当前截止时间，刷新页面不能无限延长自己的回合。
            self._changed(room)
            return self._snapshot(room, user_id)
        if room.mode == "solo":
            raise ValueError("这是单人挑战房间")
        if room.state == "playing":
            raise ValueError("本局进行中，请等待结束后加入")
        maximum = GAME_SPECS[room.game_type][1]
        if len(room.players) >= maximum:
            # 陪玩席位可以让给真人，用户选择保留的月月不被自动移除。
            spare = next((p.user_id for p in room.players.values() if p.is_bot and p.user_id != "bot:yueyue"), None)
            if spare is None:
                raise ValueError("房间已满")
            del room.players[spare]
        room.players[user_id] = TablePlayer(user_id, str(user["username"]), str(user["avatar_url"]))
        room.removed_user_ids.discard(user_id)
        self._changed(room)
        return self._snapshot(room, user_id)

    def get(self, room_id: str, user_id: str) -> dict:
        room = self._room(room_id)
        self._member(room, user_id)
        self._advance_due_turn(room)
        return self._snapshot(room, user_id)

    def ready(self, room_id: str, user_id: str, ready: bool) -> dict:
        room = self._room(room_id)
        player = self._member(room, user_id)
        if room.state == "playing":
            raise ValueError("牌局进行中，不能修改准备状态")
        player.is_ready = ready
        self._changed(room)
        return self._snapshot(room, user_id)

    def validate_settings(self, room_id: str, user_id: str, base_stake: int | None = None,
                          loss_limit: int | None = None, auto_start_when_ready: bool | None = None,
                          turn_timeout_seconds: int | None = None) -> GameTable:
        room = self._room(room_id)
        self._member(room, user_id)
        if room.host_user_id != str(user_id):
            raise PermissionError("只有房主可以调整房间设置")
        if room.state == "playing":
            raise ValueError("牌局中不能调整设置，请本局结束后再设置")
        if auto_start_when_ready is not None and type(auto_start_when_ready) is not bool:
            raise ValueError("自动开局设置必须为布尔值")
        if turn_timeout_seconds is not None and (type(turn_timeout_seconds) is not int or not 15 <= turn_timeout_seconds <= 300):
            raise ValueError("操作等待时长须为 15 至 300 秒的整数")
        if (base_stake is None) != (loss_limit is None):
            raise ValueError("底分和单局上限必须一起设置")
        if base_stake is None and auto_start_when_ready is None and turn_timeout_seconds is None:
            raise ValueError("请提供需要修改的房间设置")
        if base_stake is not None:
            if room.room_tier != "custom":
                raise ValueError("仅自定义房间可以调整底分和单局上限")
            room_settings("custom", base_stake, loss_limit)
            validate_game_stake(room.game_type, base_stake)
        return room

    def settings(self, room_id: str, user_id: str, base_stake: int | None = None,
                 loss_limit: int | None = None, auto_start_when_ready: bool | None = None,
                 turn_timeout_seconds: int | None = None) -> dict:
        room = self.validate_settings(room_id, user_id, base_stake, loss_limit, auto_start_when_ready, turn_timeout_seconds)
        if base_stake is not None and (base_stake, loss_limit) != (room.base_stake, room.buy_in):
            room.base_stake, room.entry_min, room.buy_in = room_settings("custom", base_stake, loss_limit)
            for player in room.players.values():
                player.is_ready = player.is_bot
        if auto_start_when_ready is not None:
            room.auto_start_when_ready = auto_start_when_ready
        if turn_timeout_seconds is not None and turn_timeout_seconds != room.turn_timeout_seconds:
            room.turn_timeout_seconds = turn_timeout_seconds
            for player in room.players.values():
                player.is_ready = player.is_bot
        self._changed(room)
        return self._snapshot(room, user_id)

    def should_auto_start(self, room_id: str) -> bool:
        """只判断是否可开局；实际开局和冻结资金由接口在房间锁内完成。"""
        room = self._room(room_id)
        if not room.auto_start_when_ready or room.state == "playing" or room.settlement_status == "reserved":
            return False
        active = [player for player in room.players.values() if player.connected or player.is_bot]
        minimum, maximum, _, _ = GAME_SPECS[room.game_type]
        host = room.players.get(room.host_user_id)
        return bool(host and host.connected and minimum <= len(active) <= maximum
                    and all(player.is_ready for player in active))

    def kick(self, room_id: str, user_id: str, target_user_id: str) -> dict:
        room = self._room(room_id)
        self._member(room, user_id)
        if room.host_user_id != str(user_id):
            raise PermissionError("只有房主可以移出玩家")
        if room.state == "playing" or room.settlement_status == "reserved":
            raise ValueError("本局进行中或尚未结算，不能移出玩家")
        target = room.players.get(str(target_user_id))
        if target is None or target.is_bot or target.user_id == room.host_user_id:
            raise ValueError("只能移出房间内的其他真人玩家")
        del room.players[target.user_id]
        room.removed_user_ids.add(target.user_id)
        self._changed(room)
        return self._snapshot(room, user_id)

    @staticmethod
    def _add_bots(room: GameTable, count: int):
        for _ in range(count):
            if "bot:yueyue" not in room.players:
                uid, name = "bot:yueyue", "月月"
            else:
                index = room.next_bot_number
                room.next_bot_number += 1
                uid, name = f"bot:companion:{index}", f"陪玩{index}"
            room.players[uid] = TablePlayer(uid, name, "/character/normal.webp", True, True)

    def bots(self, room_id: str, user_id: str, operation: str,
             count: int | None = None, bot_id: str | None = None) -> dict:
        room = self._room(room_id)
        self._member(room, user_id)
        if room.host_user_id != str(user_id):
            raise PermissionError("只有房主可以调整陪玩")
        if room.state == "playing":
            raise ValueError("本局进行中，不能调整座位")
        if operation == "add":
            empty = GAME_SPECS[room.game_type][1] - len(room.players)
            if type(count) is not int or not 1 <= count <= empty or bot_id is not None:
                raise ValueError(f"添加数量须为1至{empty}之间的整数，且不能指定机器人ID")
            self._add_bots(room, count)
        elif operation == "remove":
            player = room.players.get(bot_id)
            if count is not None or player is None or not player.is_bot:
                raise ValueError("请选择房间内的机器人移除，不能移除真人")
            del room.players[bot_id]
        else:
            raise ValueError("不支持的陪玩操作")
        for player in room.players.values():
            player.is_ready = player.is_bot
        self._changed(room)
        return self._snapshot(room, user_id)

    def start(self, room_id: str, user_id: str) -> dict:
        room = self._room(room_id)
        self._member(room, user_id)
        if room.host_user_id != str(user_id):
            raise PermissionError("只有房主可以开始游戏")
        if room.state == "playing":
            raise ValueError("本局已开始")
        # 离桌托管者在下一局释放座位。
        active = {uid: p for uid, p in room.players.items() if p.connected or p.is_bot}
        minimum, maximum, module_name, class_name = GAME_SPECS[room.game_type]
        if not minimum <= len(active) <= maximum:
            raise ValueError(f"该游戏需要 {minimum} 至 {maximum} 名玩家，可添加陪玩补齐")
        if not all(p.is_ready for p in active.values()):
            raise ValueError("请等待所有玩家准备")
        engine_class = getattr(import_module(f"src.chat.features.games.blackjack-web.{module_name}"), class_name)
        # 轮换首家，避免连续多局由同一玩家固定坐庄。
        ids = list(active)
        previous_guandan = (room.engine if room.game_type == "guandan" and room.engine is not None
                            and room.engine.finished and room.engine.player_ids == ids else None)
        rotation = 0 if room.game_type == "guandan" else room.round_number % len(ids)
        ids = ids[rotation:] + ids[:rotation]
        options = {"match_state": previous_guandan.next_match_state()} if previous_guandan else {}
        engine = engine_class(ids, seed=random.SystemRandom().randrange(2**63),
                              buy_in=room.buy_in, base_stake=room.base_stake, **options)
        room.players = active
        room.engine = engine
        room.state = "playing"
        room.round_number += 1
        room.started_at = self.clock()
        room.public_action_history.clear()
        room.history_deal_count = int(getattr(engine, "deal_count", 0))
        room.history_truncated = False
        room.escrow_key = uuid.uuid4().hex
        room.settlement_status = "reserved"
        room.actual_settlement = {}
        self._changed(room)
        self._set_turn(room)
        return self._snapshot(room, user_id)

    def action(self, room_id: str, user_id: str, action: str, expected_revision: int | None = None, **payload) -> dict:
        room = self._room(room_id)
        player = self._member(room, user_id)
        if not player.connected:
            raise PermissionError("你已离桌，请重新加入")
        self._advance_due_turn(room)
        if expected_revision is not None and room.revision != expected_revision:
            raise StaleTableAction("牌局已经更新，请按当前牌面重新操作")
        if room.state != "playing":
            raise ValueError("当前没有正在进行的牌局")
        previous_turn = room.engine.current_player_id
        room.engine.act(str(user_id), action, **payload)
        self._observe_action(room, str(user_id), action, payload)
        self._changed(room)
        if room.engine.finished or room.engine.current_player_id != previous_turn or action != "look":
            self._set_turn(room)
        return self._snapshot(room, user_id)

    def leave(self, room_id: str, user_id: str):
        room = self._room(room_id)
        player = self._member(room, user_id)
        if room.state == "playing":
            player.connected = False
            if room.last_turn == str(user_id):
                self._set_turn(room)
        else:
            del room.players[str(user_id)]
        humans = [p for p in room.players.values() if not p.is_bot and p.connected]
        if not humans and room.state != "playing":
            del self.rooms[room.room_id]
            return
        if humans and room.host_user_id == str(user_id):
            room.host_user_id = humans[0].user_id
        self._changed(room)

    def settlement(self, room_id: str) -> tuple[str, dict[str, int]] | None:
        room = self._room(room_id)
        if room.state != "finished" or room.settlement_status != "reserved":
            return None
        state = room.engine.public_state(room.host_user_id)
        engine_players = {str(p["user_id"]): p for p in state["players"]}
        theoretical = {
            uid: int(result["stack"]) - room.buy_in
            if room.game_type in ("texas", "golden_flower")
            else int(result.get("score_delta", result.get("score", 0)))
            for uid, result in engine_players.items()
        }
        if sum(theoretical.values()) != 0:
            raise ValueError("牌局净额不守恒，拒绝结算")
        actual = {uid: -min(-delta, room.buy_in) if delta < 0 else 0 for uid, delta in theoretical.items()}
        available = -sum(actual.values())
        winners = {uid: delta for uid, delta in theoretical.items() if delta > 0}
        total_claims = sum(winners.values())
        if total_claims:
            for uid, claim in winners.items():
                actual[uid] = available * claim // total_claims
            remainder = -sum(actual.values())
            # 比例分配后不足一灵石的尾差依余数、座次分配，保持零和。
            order = sorted(winners, key=lambda uid: -(available * winners[uid] % total_claims))
            for uid in order[:remainder]:
                actual[uid] += 1
        room.actual_settlement = actual
        payouts = {
            uid: room.buy_in + actual[uid]
            for uid, player in room.players.items() if not player.is_bot
        }
        return str(room.escrow_key), payouts


table_service = TableService()
