"""异步申请陪玩动作：网络请求不持有房间锁，规则引擎仍负责最终校验。"""

import asyncio
import copy
import json
import logging
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any


llm_module = import_module("src.chat.features.games.blackjack-web.game_llm")
log = logging.getLogger(__name__)


@dataclass
class PendingDecision:
    token: tuple
    task: asyncio.Task
    context: dict
    session_key: tuple


@dataclass
class BotConversation:
    messages: list[dict] = field(default_factory=list)


class GameBotRunner:
    """每个房间最多一个模型请求；轮询只入队或领取已完成结果。"""

    MAX_PENDING = 64

    def __init__(self, client, blackjack_service):
        self.client = client
        self.blackjack_service = blackjack_service
        self.pending: dict[tuple[str, str], PendingDecision] = {}
        self.conversations: dict[tuple, BotConversation] = {}
        self.public_actions: dict[tuple, list[dict]] = {}
        self.closed = False

    async def _choose(self, context: dict) -> dict | None:
        try:
            # 即使替换了客户端实现，调度层仍保证等待有明确上限。
            return await asyncio.wait_for(
                self.client.choose_action(context), timeout=self.client.timeout_seconds + 0.5,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("陪玩模型调用回退算法：%s", type(exc).__name__)
            return None

    def _decision(self, key: tuple[str, str], token: tuple, session_key: tuple, context_factory):
        """返回 (是否完成, 动作)，None 动作表示使用原算法。"""
        if self.closed or not self.client.enabled:
            return True, None, None
        pending = self.pending.get(key)
        if pending is not None and pending.token != token:
            pending.task.cancel()
            del self.pending[key]
            pending = None
        if pending is not None:
            if not pending.task.done():
                return False, None, None
            del self.pending[key]
            if pending.task.cancelled():
                return True, None, pending.context
            return True, pending.task.result(), pending.context
        if len(self.pending) >= self.MAX_PENDING:
            return True, None, None
        try:
            context = context_factory()
            conversation = self.conversations.setdefault(session_key, BotConversation())
            context["_conversation"] = copy.deepcopy(conversation.messages)
            if key[0] == "table":
                context["public_action_history"] = copy.deepcopy(self.public_actions.get(session_key[:-1], []))
        except Exception as exc:
            log.warning("陪玩局面构建回退算法：%s", type(exc).__name__)
            return True, None, None
        self.pending[key] = PendingDecision(token, asyncio.create_task(self._choose(context)), context, session_key)
        return False, None, None

    def _remember(self, session_key: tuple, context: dict | None, action: dict):
        if context is None:
            return
        conversation = self.conversations.setdefault(session_key, BotConversation())
        # 当前请求总是带完整公开出牌记录；旧轮次消息不重复携带同一份记录。
        previous = {key: value for key, value in context.items() if key != "public_action_history"}
        try:
            messages = [llm_module.conversation_user_message(previous),
                        llm_module.conversation_assistant_message(action, context)]
        except (ValueError, TypeError, KeyError):
            log.warning("陪玩会话更新失败，保留公开动作记忆并继续牌局")
            return
        conversation.messages.extend(messages)
        while len(conversation.messages) > 2 and (
            len(conversation.messages) > 12 or
            len(json.dumps(conversation.messages, ensure_ascii=False)) > 20000
        ):
            del conversation.messages[:2]

    @staticmethod
    def _round_key(room) -> tuple:
        return ("table", room.room_id, id(room), room.round_number, getattr(room.engine, "deal_count", 0))

    def observe_table_action(self, room, user_id: str, action: str, payload: dict):
        """只记公共动作，不记录看牌内容、暗杠牌面或未公开的定缺选择。"""
        state = room.engine.public_state(str(user_id))
        if room.game_type == "landlord" and action == "bid" and not state.get("bids"):
            # 全员不叫后的重新发牌属于新牌副，这个旧叫分不能进入新牌副记忆。
            return
        seats = {str(player["user_id"]): f"seat_{index + 1}"
                 for index, player in enumerate(state["players"])}
        event = {"seat": seats[str(user_id)], "action": action}
        if action in {"raise", "bid", "play", "discard", "chow"}:
            for key in ("amount", "bid", "cards", "tile", "tiles"):
                if key in payload:
                    event[key] = copy.deepcopy(payload[key])
        if action == "compare" and str(payload.get("target_id")) in seats:
            event["target_seat"] = seats[str(payload["target_id"])]
        if room.game_type == "guandan" and action == "play":
            played = state.get("last_play") or {}
            event.update({key: played[key] for key in ("combo", "kind", "name") if key in played})
        if room.game_type in {"texas", "golden_flower"}:
            actor = next(player for player in state["players"] if str(player["user_id"]) == str(user_id))
            event.update({key: state[key] for key in ("phase", "pot") if key in state})
            event.update({key: actor[key] for key in ("round_bet", "total_bet", "stack") if key in actor})
        # 完整公开记录按局保存，和对话截断无关，后续轮次仍能记住早期出的牌。
        self.public_actions.setdefault(self._round_key(room), []).append(event)

    def table_action(self, room: Any, user_id: str) -> dict | None:
        token = (id(room), room.round_number, room.revision, user_id, room.turn_deadline,
                 getattr(room.engine, "deal_count", 0))
        session_key = (*self._round_key(room), user_id)
        ready, action, context = self._decision(
            ("table", room.room_id), token, session_key,
            lambda: llm_module.build_table_context(room.game_type, room.engine, user_id),
        )
        if not ready:
            return None
        if action is not None:
            try:
                # 有些非法动作在报错前会修改局部状态，先用副本验证，保护真实牌局。
                trial = copy.deepcopy(room.engine)
                payload = dict(action)
                trial.act(user_id, payload.pop("action"), **payload)
            except Exception as exc:
                log.warning("陪玩模型动作非法，回退算法：%s %s", room.game_type, type(exc).__name__)
            else:
                log.info("陪玩 LLM 动作已通过规则校验：%s %s", room.game_type, action["action"])
                self._remember(session_key, context, action)
                return action
        action = dict(room.engine.suggest_action(user_id))
        self._remember(session_key, context, action)
        return action

    def blackjack_action(self, room: Any, user_id: int) -> dict | None:
        player = room.players[user_id]
        token = (id(room), room.round_key, room.current_turn_index, user_id,
                 tuple(player.hand), player.status, room.turn_deadline)
        session_key = ("blackjack", room.room_id, id(room), room.round_key, str(user_id))
        ready, action, context = self._decision(
            ("blackjack", room.room_id), token, session_key,
            lambda: llm_module.build_blackjack_context(
                self.blackjack_service.llm_public_state(room), str(user_id),
            ),
        )
        if not ready:
            return None
        if action is not None:
            try:
                self.blackjack_service.apply_bot_action(copy.deepcopy(room), action)
            except Exception as exc:
                log.warning("陪玩模型动作非法，回退算法：blackjack %s", type(exc).__name__)
            else:
                log.info("陪玩 LLM 动作已通过规则校验：blackjack %s", action["action"])
                self._remember(session_key, context, action)
                return action
        module = import_module(self.blackjack_service.__class__.__module__)
        hit = module._companion_should_hit(player.hand, room.dealer_hand[0])
        action = {"action": "hit" if hit else "stand"}
        self._remember(session_key, context, action)
        return action

    def prune(self, table_rooms: dict, blackjack_rooms: dict):
        """房间结束或被移除时取消过期请求，晚到的模型答案不得执行。"""
        for key, pending in list(self.pending.items()):
            rooms = table_rooms if key[0] == "table" else blackjack_rooms
            room = rooms.get(key[1])
            if room is None or room.state != "playing" or id(room) != pending.token[0]:
                pending.task.cancel()
                del self.pending[key]
        for store in (self.conversations, self.public_actions):
            for key in list(store):
                rooms = table_rooms if key[0] == "table" else blackjack_rooms
                room = rooms.get(key[1])
                if (room is None or room.state != "playing" or id(room) != key[2]
                        or (room.round_number if key[0] == "table" else room.round_key) != key[3]
                        or (key[0] == "table" and getattr(room.engine, "deal_count", 0) != key[4])):
                    del store[key]

    async def close(self):
        self.closed = True
        tasks = [pending.task for pending in self.pending.values()]
        self.pending.clear()
        self.conversations.clear()
        self.public_actions.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
