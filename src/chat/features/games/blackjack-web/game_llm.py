"""游戏模型建议：仅传公开牌局信息，异常时交还本地算法处理。"""

import asyncio
import json
import logging
import math
import os
from urllib.parse import urlsplit

import httpx


log = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 32768
MAX_CONTENT_BYTES = 4096
RULES = {
    "texas": "无限注德州扑克。用自己的两张底牌和五张公共牌组成最佳五张牌；结合多人胜率、底池赔率和筹码决策。raise.amount 是本轮加注到的总额，遵守 min_raise_to/max_raise_to；河牌仍有最后一轮下注。",
    "golden_flower": "炸金花。未看牌不能获知自己的手牌；look 看牌，call 跟注，raise.amount 为基础注总额，看牌者实际付双倍；compare.target_id 必须来自 compare_targets。考虑轮次、成本和适度诈唬。",
    "landlord": "斗地主，地主对两名农民。bid 为 0 至 3 的叫分，play.cards 是要出的手牌；同类牌型比大小，炸弹和王炸例外。农民应配合队友，考虑剩余张数、保留炸弹和拆牌成本。",
    "guandan": "掼蛋，四人对家组队，双副108张。level是全桌当前级牌，红桃级牌可配非王；单对三、三带二、五张顺子、三连对、两连三、同点炸弹、同花顺和四王。同花顺大于五炸小于六炸。出完后若无人压，队友接风。优先从play_options选合法cards，保留#0/#1区分两副，必要时手动组合；配合队友，不压队友无必要的小牌，注意对手剩余张数。只输出action和cards，规则引擎会选最弱可压的通配解释。",
    "mahjong": "四人麻将。discard.tile 打牌，chow.tiles 选择 chow_options 中的顺子，kong.tile 来自 kong_options，pung 碰，win 胡，pass 跳过响应。只依据自身手牌、公开副露和弃牌。",
    "sichuan_mahjong": "四川血战麻将。dingque.suit 定缺，有缺门先打缺门；不能吃，允许碰杠胡，胡后其他玩家继续至三家胡牌。优先使用 missing_suit_options/kong_options 和合法动作。",
    "blackjack": "21点。仅 hit 要牌或 stand 停牌，超过21爆牌；A可算1或11，荷官按固定17点规则行动。只知道荷官明牌，结合自己点数作决策。",
}
ACTION_FIELDS = {
    "fold": (), "check": (), "call": (), "all_in": (), "look": (),
    "raise": ("amount",), "compare": ("target_id",), "bid": ("bid",),
    "play": ("cards",), "pass": (), "discard": ("tile",),
    "chow": ("tiles",), "kong": ("tile",), "pung": (), "win": (),
    "dingque": ("suit",), "hit": (), "stand": (),
}
GAME_ACTIONS = {
    "texas": {"fold", "check", "call", "raise", "all_in"},
    "golden_flower": {"fold", "look", "call", "raise", "compare"},
    "landlord": {"bid", "play", "pass"},
    "guandan": {"play", "pass"},
    "mahjong": {"discard", "chow", "kong", "pung", "win", "pass"},
    "sichuan_mahjong": {"dingque", "discard", "kong", "pung", "win", "pass"},
    "blackjack": {"hit", "stand"},
}
# 字段白名单阻止用户名、头像、房号、聊天内容及未来新增内部字段进入模型。
PUBLIC_FIELDS = set("""phase state finished current_player_id current_turn_user_id
players user_id hand hand_count stack round_bet total_bet folded all_in payout score_delta
is_dealer is_small_blind is_big_blind hand_name buy_in base_stake small_blind big_blind
legal_actions community_cards pot current_bet call_amount min_raise min_raise_to max_raise_to
side_pots amount winners settlement ante seen base_bet compare_cost action_count max_actions
compare_targets score role bid landlord_id highest_bid bids bid_options bottom_cards last_play
cards kind rank size chain seat_actions action multiplier deal_count score_unit effective_stake
melds tiles type concealed tile_count discards seat_wind wall_count last_discard tile chow_options
kong_options drawn_tile mahjong_variant missing_suit has_won win_order win_fan win_label
winning_tile max_fan missing_suit_options reaction_kind after_kong source_id from_user_id bet_amount status
is_current_turn dealer level team_levels team finished_rank finish_order tribute_events
match_finished match_winner_team level_gain play_options combo to_user_id target_id
user_ids card automatic fallback""".split())


def _context(game_type: str, public_state: dict, user_id: str) -> dict:
    if game_type not in RULES or not isinstance(public_state, dict):
        raise ValueError("不支持的游戏公开状态")
    identities = [str(player["user_id"]) for player in public_state.get("players", [])]
    if str(user_id) not in identities:
        raise ValueError("公开状态中没有当前玩家")
    seats = {uid: f"seat_{index + 1}" for index, uid in enumerate(identities)}

    identity_fields = {"user_id", "current_player_id", "current_turn_user_id", "landlord_id",
                       "source_id", "from_user_id", "to_user_id", "target_id", "user_ids", "winners", "compare_targets", "finish_order"}

    def clean(value, field=""):
        if isinstance(value, dict):
            return {seats.get(str(key), str(key)): clean(item, str(key))
                    for key, item in value.items() if str(key) in PUBLIC_FIELDS or str(key) in seats}
        if isinstance(value, list):
            return [clean(item, field) for item in value]
        if field in identity_fields and isinstance(value, (str, int)) and str(value) in seats:
            return seats[str(value)]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise ValueError("公开状态包含无法编码的数据")

    state = clean(public_state)
    if game_type == "guandan":
        # 完整手牌和公开记牌保持原样；候选按牌型分组，避免重复组合拖长推理。
        groups = {}
        seen = set()
        for option in state.get("play_options", []):
            signature = (option.get("combo"), option.get("kind"), option.get("rank"), option.get("size"))
            if signature in seen:
                continue
            seen.add(signature)
            groups.setdefault((option.get("kind"), option.get("size")), []).append(option)
        options = []
        take_high = False
        while len(options) < 12 and any(groups.values()):
            for group in groups.values():
                if group and len(options) < 12:
                    options.append(group.pop(-1 if take_high else 0))
            take_high = not take_high
        state["play_options"] = options
    if game_type == "golden_flower":
        for player in state.get("players", []):
            if not player.get("hand") and player.get("hand_count", 0):
                player["hand"] = ["Hidden"] * player["hand_count"]
    if game_type == "blackjack":
        state["legal_actions"] = ["hit", "stand"]
        # 即使调用者不慎传入完整荷官数组，行动阶段也只保留第一张明牌。
        if state.get("state", state.get("phase")) not in ("dealer_turn", "finished"):
            dealer = state.get("dealer", {})
            hand = dealer.get("hand", [])
            dealer["hand"] = hand[:1] + (["Hidden"] if len(hand) > 1 else [])
            dealer.pop("score", None)
    return {
        "game_type": game_type, "rules": RULES[game_type], "you": seats[str(user_id)],
        "state": state, "action_fields": {action: list(ACTION_FIELDS[action]) for action in GAME_ACTIONS[game_type]},
        "_seat_ids": {seat: uid for uid, seat in seats.items()},
    }


def build_table_context(game_type: str, engine, user_id: str) -> dict:
    """只读取引擎公开接口，不访问底牌、牌堆或发牌随机数。"""
    return _context(game_type, engine.public_state(str(user_id)), str(user_id))


def build_blackjack_context(public_state: dict, user_id: str) -> dict:
    return _context("blackjack", public_state, str(user_id))


def conversation_user_message(context: dict) -> dict:
    """内部映射和独立会话不进入本轮牌局正文；公开行动记录可以用于记牌。"""
    def public(value):
        if isinstance(value, dict):
            return {key: public(item) for key, item in value.items() if not str(key).startswith("_")}
        if isinstance(value, list):
            return [public(item) for item in value]
        return value
    public_context = public(context)
    content = json.dumps(public_context, ensure_ascii=False, allow_nan=False)
    if len(content.encode("utf-8")) > 65536:
        raise ValueError("公开牌局过大")
    return {"role": "user", "content": content}


def conversation_assistant_message(action: dict, context: dict) -> dict:
    """会话历史保存匿名座次，避免比牌动作把真实账号带入下一轮请求。"""
    action = dict(action)
    if "target_id" in action:
        identities = {uid: seat for seat, uid in context.get("_seat_ids", {}).items()}
        action["target_id"] = identities[str(action["target_id"])]
    return {"role": "assistant", "content": json.dumps(action, ensure_ascii=False, allow_nan=False)}


def _bounded_setting(name: str, default: float, minimum: float, maximum: float, integer=False):
    raw = os.getenv(name, str(default))
    value = int(raw) if integer else float(raw)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("游戏模型配置超出范围")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("模型返回重复字段")
        result[key] = value
    return result


class GameLLMClient:
    def __init__(self):
        self.enabled = False
        self.timeout_seconds = 12.0
        self.model = os.getenv("GAME_LLM_MODEL", "打牌LLM").strip()
        self._api_key = os.getenv("GAME_LLM_API_KEY", "").strip()
        self._url = ""
        self._semaphore = asyncio.Semaphore(4)
        try:
            self.timeout_seconds = _bounded_setting("GAME_LLM_TIMEOUT_SECONDS", 12, 2, 30)
            concurrency = _bounded_setting("GAME_LLM_MAX_CONCURRENCY", 4, 1, 32, integer=True)
            self._semaphore = asyncio.Semaphore(concurrency)
            base = os.getenv("GAME_LLM_BASE_URL", "").strip().rstrip("/")
            parsed = urlsplit(base)
            valid_url = parsed.scheme in ("http", "https") and bool(parsed.hostname) and not (
                parsed.username or parsed.password or parsed.query or parsed.fragment)
            self._url = base + "/chat/completions"
            self.enabled = os.getenv("GAME_LLM_ENABLED", "false").lower().strip() == "true" and bool(
                valid_url and self._api_key and self.model and
                not any(ord(char) < 32 or ord(char) == 127 for char in self._api_key))
        except (TypeError, ValueError, OverflowError):
            log.warning("游戏模型已禁用：配置无效")

    @staticmethod
    def _validate_action(result: object, context: dict) -> dict | None:
        if not isinstance(result, dict) or not isinstance(result.get("action"), str):
            return None
        action = result["action"]
        if action not in GAME_ACTIONS.get(context.get("game_type"), set()):
            return None
        if action not in context.get("state", {}).get("legal_actions", []):
            return None
        expected = {"action", *ACTION_FIELDS[action]}
        if set(result) != expected:
            return None
        output = dict(result)
        if "amount" in result and (type(result["amount"]) is not int or not 0 < result["amount"] < 2**53):
            return None
        if "bid" in result and (type(result["bid"]) is not int or not 0 <= result["bid"] <= 3):
            return None
        for field, maximum in (("cards", 20), ("tiles", 3)):
            if field in result and (not isinstance(result[field], list) or not 1 <= len(result[field]) <= maximum
                    or any(not isinstance(card, str) or not 1 <= len(card) <= 16 for card in result[field])):
                return None
        for field in ("tile", "suit"):
            if field in result and (not isinstance(result[field], str) or not 1 <= len(result[field]) <= 16):
                return None
        if "target_id" in result:
            target = result["target_id"]
            if not isinstance(target, str) or target not in context.get("_seat_ids", {}):
                return None
            output["target_id"] = context["_seat_ids"][target]
        return output

    async def choose_action(self, context: dict) -> dict | None:
        if not self.enabled:
            return None
        try:
            # 超时包含排队、网络和解析，防止并发牌桌造成无限等待。
            async with asyncio.timeout(self.timeout_seconds):
                async with self._semaphore:
                    current_message = conversation_user_message(context)
                    history = context.get("_conversation", [])
                    if not isinstance(history, list):
                        raise ValueError("独立会话格式不合法")
                    for message in history:
                        if not isinstance(message, dict) or set(message) != {"role", "content"} or (
                            message["role"] not in ("user", "assistant") or not isinstance(message["content"], str)
                        ):
                            raise ValueError("独立会话消息不合法")
                    if sum(len(item["content"].encode("utf-8")) for item in history) > 262144:
                        raise ValueError("独立会话过大")
                    payload = {
                        "model": self.model, "stream": False,
                        "messages": [
                            {"role": "system", "content": "你是牌局策略助手。仅根据给出的可见信息和规则，选择legal_actions中的一个动作。输出一个JSON对象，包含action及action_fields列出的必需字段；不能包含解释、代码块或其他字段。不得猜测已隐藏的具体牌。"},
                            *history, current_message,
                        ],
                    }
                    if context.get("game_type") == "guandan":
                        # 掼蛋组合由规则引擎校验，降低模型穷举耗时，不限制输出令牌数。
                        payload["reasoning_effort"] = "low"
                    async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
                        async with client.stream("POST", self._url, json=payload,
                                                 headers={"Authorization": f"Bearer {self._api_key}"}) as response:
                            response.raise_for_status()
                            body = bytearray()
                            async for chunk in response.aiter_bytes():
                                body.extend(chunk)
                                if len(body) > MAX_RESPONSE_BYTES:
                                    raise ValueError("模型响应过大")
                    data = json.loads(body, object_pairs_hook=_unique_object)
                    message = data["choices"][0]["message"]
                    if "tool_calls" in message or "function_call" in message:
                        raise ValueError("不接受工具调用")
                    answer = message["content"]
                    if not isinstance(answer, str) or len(answer.encode("utf-8")) > MAX_CONTENT_BYTES:
                        raise ValueError("模型动作内容不合法")
                    action = self._validate_action(json.loads(answer, object_pairs_hook=_unique_object), context)
                    if action is None:
                        log.info("游戏模型回退：动作格式无效")
                    return action
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
            # 不输出异常文本，避免第三方响应、网址凭据和完整牌局出现在日志。
            log.info("游戏模型回退：请求或响应无效")
            return None
