"""房间内固定快捷聊天与免费互动，不接触钱包或 Discord 消息。"""

from collections import OrderedDict, deque
from copy import deepcopy
from dataclasses import dataclass, field
import math
import time


CHAT_CATALOG = {
    "hurry": "快点吧，我等到花儿都谢了", "nice": "你的牌打得也太好了",
    "hello": "很高兴和你一起玩", "thanks": "谢谢你", "well_played": "这局打得漂亮",
    "mm_or_gg": "你是MM还是GG？",
    "partner": "队友，我们配合一下", "let_me": "这轮让我来", "thinking": "别急，我想想怎么打",
    "good_luck": "祝大家好运", "again": "再来一局吧",
}
INTERACTION_CATALOG = {"tea": "倒茶", "flower": "送花", "incense": "上香"}


class SocialRateLimitError(ValueError):
    def __init__(self, retry_after: float):
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(f"发送太频繁，请 {self.retry_after} 秒后再试")


@dataclass
class _RoomEvents:
    events: deque = field(default_factory=lambda: deque(maxlen=100))
    send_times: dict[str, deque] = field(default_factory=dict)
    touched_at: float = 0


class GameSocialService:
    MAX_SCOPES = 1000
    IDLE_TTL_SECONDS = 3600
    INTERVAL_SECONDS = 2
    MINUTE_LIMIT = 12

    def __init__(self, clock=time.monotonic, wall_clock=time.time):
        self.clock = clock
        self.wall_clock = wall_clock
        self._rooms: OrderedDict[object, _RoomEvents] = OrderedDict()
        self._event_id = 0

    @staticmethod
    def catalog():
        return {"chat": [{"id": key, "text": value} for key, value in CHAT_CATALOG.items()],
                "interaction": [{"id": key, "text": value} for key, value in INTERACTION_CATALOG.items()]}

    @staticmethod
    def _members(user_id, members):
        indexed = {str(member["user_id"]): member for member in members}
        member = indexed.get(str(user_id))
        if member is None or member.get("is_bot"):
            raise PermissionError("只有房间内的玩家可以使用快捷聊天和互动")
        return indexed, member

    def cleanup(self):
        """只清理空闲实例；房号相同的不同实例必须由调用者传入不同 scope。"""
        now = self.clock()
        while self._rooms:
            scope, room = next(iter(self._rooms.items()))
            if now - room.touched_at < self.IDLE_TTL_SECONDS:
                break
            self._rooms.pop(scope)

    def _room(self, scope, member_ids):
        self.cleanup()
        room = self._rooms.get(scope)
        if room is None:
            while len(self._rooms) >= self.MAX_SCOPES:
                self._rooms.popitem(last=False)
            room = _RoomEvents()
            self._rooms[scope] = room
        room.touched_at = self.clock()
        self._rooms.move_to_end(scope)
        # 历史离房账号的限频桶在一分钟后释放，避免频繁换人累积内存。
        room.send_times = {uid: times for uid, times in room.send_times.items()
                           if uid in member_ids or times and room.touched_at - times[-1] < 60}
        return room

    def send(self, scope, user_id, members, kind, item_id, target_id=None):
        indexed, member = self._members(user_id, members)
        user_id = str(user_id)
        if not isinstance(kind, str) or not isinstance(item_id, str):
            raise ValueError("请选择固定的快捷聊天或互动")
        target = None
        if kind == "chat":
            if item_id not in CHAT_CATALOG or target_id is not None:
                raise ValueError("无效的快捷聊天")
            text = CHAT_CATALOG[item_id]
        elif kind == "interaction":
            target_id = str(target_id) if target_id is not None else None
            target = indexed.get(target_id)
            if item_id not in INTERACTION_CATALOG or target is None or target_id == user_id:
                raise ValueError("请选择同房间的其他玩家互动")
            text = INTERACTION_CATALOG[item_id]
        else:
            raise ValueError("不支持的社交操作")
        room = self._room(scope, indexed)
        now = self.clock()
        times = room.send_times.setdefault(user_id, deque())
        while times and now - times[0] >= 60:
            times.popleft()
        if times and now - times[-1] < self.INTERVAL_SECONDS:
            raise SocialRateLimitError(self.INTERVAL_SECONDS - (now - times[-1]))
        if len(times) >= self.MINUTE_LIMIT:
            raise SocialRateLimitError(60 - (now - times[0]))
        times.append(now)
        self._event_id += 1
        event = {
            "event_id": self._event_id, "kind": kind, "item_id": item_id, "text": text,
            "user_id": user_id, "username": str(member.get("username") or user_id),
            "target_id": target_id, "target_username": str(target.get("username") or target_id) if target else None,
            "timestamp": self.wall_clock(),
        }
        room.events.append(event)
        return deepcopy(event)

    def recent(self, scope, user_id, members, after=None):
        indexed, _ = self._members(user_id, members)
        if after is not None and (type(after) is not int or after < 0):
            raise ValueError("事件游标必须是非负整数")
        room = self._room(scope, indexed)
        # 首次打开不重放语音/动画；全局单调游标避免清理后同实例出现旧游标碰撞。
        events = [] if after is None else [deepcopy(event) for event in room.events if event["event_id"] > after]
        return {"events": events, "cursor": self._event_id}
