"""Jev 评估协议：从可见局面构造多样候选，模型只选择明确的合法动作。"""

import json
import math
from collections import Counter
from importlib import import_module


traditional = import_module("src.chat.features.games.blackjack-web.traditional_games")
MAX_PLAY_CANDIDATES = 24
MAX_CANDIDATES = 40
SIMPLE_ACTIONS = ("check", "call", "fold", "all_in", "look", "pass", "pung", "win", "hit", "stand")


def _landlord_plays(state: dict, own: dict) -> list[dict]:
    """纯函数只接收自己的牌及公开上手，按牌型与张数组选取大小两端。"""
    hand = own.get("hand", [])
    if not isinstance(hand, list) or not 1 <= len(hand) <= 20 or len(set(hand)) != len(hand):
        raise ValueError("斗地主可见手牌不合法")
    previous = state.get("last_play")
    previous_pattern = traditional.classify_landlord_cards(previous["cards"]) if previous else None
    groups = {}
    finishing = []
    for cards, pattern in traditional.LandlordGame._hand_candidates(hand):
        if previous_pattern and not traditional.landlord_beats(pattern, previous_pattern):
            continue
        item = {"action": "play", "cards": list(cards)}
        if len(cards) == len(hand):
            finishing.append(item)
        groups.setdefault((pattern.kind, pattern.size, pattern.chain), []).append((pattern.rank, item))
    # 先保留可以直接出完的牌，再交替取各牌型的小牌/大牌，不把模型限制为单个算法答案。
    selected = list(finishing)
    for rows in groups.values():
        rows.sort(key=lambda item: (item[0], item[1]["cards"]))
    take_high = False
    while len(selected) < MAX_PLAY_CANDIDATES and any(groups.values()):
        for rows in groups.values():
            if rows and len(selected) < MAX_PLAY_CANDIDATES:
                _, item = rows.pop(-1 if take_high else 0)
                if item not in selected:
                    selected.append(item)
        take_high = not take_high
    return selected[:MAX_PLAY_CANDIDATES]


def build_action_candidates(context: dict) -> dict[str, dict]:
    """候选完全来自匿名公开状态，最终仍由 runner 的引擎副本逐项校验所选动作。"""
    state = context["state"]
    kind = context["game_type"]
    legal = set(state.get("legal_actions", []))
    own = next((player for player in state.get("players", []) if player["user_id"] == context["you"]), None)
    if own is None:
        raise ValueError("没有当前玩家的可见状态")
    candidates = {}

    def add(action: dict):
        if action["action"] not in legal or action in candidates.values():
            return
        if len(candidates) >= MAX_CANDIDATES:
            raise ValueError("候选动作过多")
        label = action["action"]
        for key in ("amount", "bid", "target_id", "tile", "suit"):
            if key in action:
                label += "_" + str(action[key])
        if "cards" in action or "tiles" in action:
            label += "_" + str(len(candidates) + 1)
        candidates[label] = action

    for action in SIMPLE_ACTIONS:
        # 免费过牌占优，避免把无成本弃牌作为独立候选重复分散概率。
        if action == "fold" and kind == "texas" and "check" in legal:
            continue
        add({"action": action})

    if "raise" in legal and kind in {"texas", "golden_flower"}:
        minimum, maximum = state["min_raise_to"], state["max_raise_to"]
        if type(minimum) is not int or type(maximum) is not int or not 0 < minimum <= maximum < 2**53:
            raise ValueError("加注范围不合法")
        amounts = [minimum]
        if kind == "texas":
            current = state.get("current_bet", 0)
            pot_after_call = state.get("pot", 0) + state.get("call_amount", 0)
            amounts.extend(current + round(pot_after_call * fraction) for fraction in (0.33, 0.67, 1.0, 1.5))
            if state.get("phase") == "preflop":
                amounts.extend(state.get("big_blind", 0) * multiplier for multiplier in (2, 3, 4))
            # 全下有独立候选；仅在不足完整加注、min=max时保留 raise 的等效选项。
            amounts = [amount for amount in amounts if amount != maximum or minimum == maximum]
        else:
            amounts.extend(state["base_bet"] * multiplier for multiplier in (3, 4))
        for amount in sorted(set(max(minimum, min(maximum, amount)) for amount in amounts)):
            if kind == "texas" and amount == maximum and minimum != maximum and "all_in" in legal:
                continue
            add({"action": "raise", "amount": amount})

    if "compare" in legal:
        for target in state.get("compare_targets", []):
            if target != context["you"] and target in context.get("_seat_ids", {}):
                add({"action": "compare", "target_id": target})
    if "bid" in legal:
        for bid in state.get("bid_options", []):
            if type(bid) is int and 0 <= bid <= 3:
                add({"action": "bid", "bid": bid})
    if "play" in legal:
        if kind == "landlord":
            for action in _landlord_plays(state, own):
                add(action)
        elif kind == "guandan":
            hand = Counter(own.get("hand", []))
            for option in state.get("play_options", [])[:MAX_PLAY_CANDIDATES]:
                cards = option.get("cards", [])
                if isinstance(cards, list) and 1 <= len(cards) <= 20 and not (Counter(cards) - hand):
                    add({"action": "play", "cards": list(cards)})
    if "discard" in legal:
        hand = list(dict.fromkeys(own.get("hand", [])))
        missing = own.get("missing_suit") if kind == "sichuan_mahjong" else None
        must_discard = [tile for tile in hand if isinstance(tile, str) and tile.startswith(missing)] if missing else []
        for tile in must_discard or hand:
            add({"action": "discard", "tile": tile})
    if "chow" in legal:
        for tiles in state.get("chow_options", []):
            add({"action": "chow", "tiles": list(tiles)})
    if "kong" in legal:
        for tile in state.get("kong_options", []):
            add({"action": "kong", "tile": tile})
    if "dingque" in legal:
        for suit in state.get("missing_suit_options", []):
            add({"action": "dingque", "suit": suit})
    if not candidates:
        raise ValueError("没有可供 Jev 评估的动作")
    return candidates


def evaluation_payload(model: str, current: dict, history: list, candidates: dict) -> dict:
    return {
        "model": model,
        "state": {"current": current, "conversation": history},
        "questions": {
            "action": {
                "type": "choice",
                "instructions": "你是当前座位的牌局策略助手。阅读current中的规则、自己的可见牌、公开行动历史及这个座位的conversation。比较以下具体合法动作，以长期收益和当前游戏队伍目标选择一个最佳动作。候选是可选方案，不是算法推荐；不把未知牌当强牌，不因全下就机械弃牌，也不为激进而无条件跟注。不要猜测隐藏的具体牌，按局面选择合理的价值下注、诈唬或防守。返回所选choice及全部候选的概率。",
                "criteria": {name: json.dumps(action, ensure_ascii=False, separators=(",", ":"))
                             for name, action in candidates.items()},
            },
        },
    }


def selected_action(response: object, candidates: dict) -> dict:
    """严格匹配本次候选；接受概率两位小数独立舍入误差，不额外随机采样。"""
    if not isinstance(response, dict) or not isinstance(response.get("answers"), dict) or set(response["answers"]) != {"action"}:
        raise ValueError("Jev 缺少动作回答")
    answer = response["answers"]["action"]
    if not isinstance(answer, dict) or set(answer) != {"type", "choice", "probabilities"} or answer["type"] != "choice":
        raise ValueError("Jev 动作回答类型不合法")
    choice, probabilities = answer["choice"], answer["probabilities"]
    if not isinstance(choice, str) or choice not in candidates or not isinstance(probabilities, dict) or set(probabilities) != set(candidates):
        raise ValueError("Jev 回答了未知候选")
    if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities.values()):
        raise ValueError("Jev 概率不合法")
    tolerance = len(candidates) * 0.005 + 1e-9
    if probabilities[choice] <= 0 or abs(sum(probabilities.values()) - 1) > tolerance:
        raise ValueError("Jev 概率总和不合法")
    return dict(candidates[choice])
