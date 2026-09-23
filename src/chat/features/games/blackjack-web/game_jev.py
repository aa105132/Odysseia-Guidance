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


def _landlord_decision(current: dict, candidates: dict) -> tuple[dict, dict]:
    """把当前敌我关系与自己的拆牌结果显式化，不读取任何其他座位暗牌。"""
    state, you = current["state"], current["you"]
    players = state["players"]
    own = next(player for player in players if player["user_id"] == you)
    hand = own["hand"]
    landlord = state.get("landlord_id")
    teammate = next((p["user_id"] for p in players if p["user_id"] not in (you, landlord)), None) if landlord and you != landlord else None
    opponents = [p for p in players if p["user_id"] != you and (you == landlord or p["user_id"] == landlord)]
    previous = state.get("last_play")
    leader = previous.get("user_id") if previous else None
    relation = "teammate" if leader == teammate and teammate else "opponent" if leader else "free_lead"
    own_index = next(index for index, player in enumerate(players) if player["user_id"] == you)
    next_seat = players[(own_index + 1) % len(players)]["user_id"]
    patterns = {name: traditional.classify_landlord_cards(action["cards"])
                for name, action in candidates.items() if action["action"] == "play"}
    strongest = {}
    for pattern in patterns.values():
        strongest[pattern.kind] = max(strongest.get(pattern.kind, 0), pattern.rank)
    if any(action["action"] in ("play", "pass") for action in candidates.values()):
        original, remainder, estimate = traditional.LandlordGame._hand_planner(
            hand, list(traditional.LandlordGame._hand_candidates(hand)))
    counts = Counter(traditional.poker_value(card) for card in hand)
    descriptions = {}
    for name, action in candidates.items():
        facts = {"action": action}
        if action["action"] == "play":
            cards = action["cards"]
            pattern = patterns[name]
            used = Counter(traditional.poker_value(card) for card in cards)
            rest = list(hand)
            for card in cards:
                rest.remove(card)
            remaining = remainder(original, tuple((rank - 3, count) for rank, count in used.items()))
            facts.update(pattern=pattern.to_dict(), remaining_hand=rest, remaining_count=len(rest),
                         wins_immediately=not rest, estimated_remaining_plays=estimate(remaining, budget=32),
                         splits_same_rank_groups=[{"rank": rank, "before": counts[rank], "used": count}
                                                  for rank, count in used.items() if count < counts[rank]])
            if pattern.kind in ("single", "pair"):
                facts["strongest_same_shape_reply"] = pattern.rank == strongest[pattern.kind]
        elif action["action"] == "pass":
            facts.update(remaining_hand=list(hand), remaining_count=len(hand), wins_immediately=False,
                         estimated_remaining_plays=estimate(original, budget=32),
                         meaning="本回合不出任何牌，把压制机会交给下家；不会减少自己的手牌，也不能直接取得出牌权。")
        descriptions[name] = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    focus = {
        "phase": state["phase"], "you": you, "your_role": own.get("role"), "teammate": teammate,
        "opponents": [{"seat": p["user_id"], "remaining_count": p["hand_count"]} for p in opponents],
        "next_seat": next_seat, "current_last_play": previous, "last_play_relation": relation,
        "enemy_last_single": any(p["hand_count"] == 1 for p in opponents),
        "enemy_last_two_cards": any(p["hand_count"] == 2 for p in opponents),
        "your_current_hand": list(hand),
        "rank_order": "3<4<5<6<7<8<9<10<J<Q<K<A<2<小王<大王，花色不影响大小",
        "planning_note": "estimated_remaining_plays仅是自己剩余牌的有界拆分估算，不保证能取得对应次数的出牌权，不是假定对手手牌。",
    }
    return focus, descriptions


def evaluation_payload(model: str, current: dict, history: list, candidates: dict) -> dict:
    instructions = "你是当前座位的牌局策略助手。阅读current中的规则、自己的可见牌、公开行动历史及这个座位的conversation。比较以下具体合法动作，以长期收益和当前游戏队伍目标选择一个最佳动作。候选是可选方案，不是算法推荐；不把未知牌当强牌，不因全下就机械弃牌，也不为激进而无条件跟注。不要猜测隐藏的具体牌，按局面选择合理的价值下注、诈唬或防守。返回所选choice及全部候选的概率。"
    state = {"current": current, "conversation": history}
    criteria = {name: json.dumps(action, ensure_ascii=False, separators=(",", ":"))
                for name, action in candidates.items()}
    if current["game_type"] == "landlord":
        focus, criteria = _landlord_decision(current, candidates)
        # 历史保留完整且独立，但最新事实置后，避免把旧王炸/旧pass当作本回合。
        state = {"conversation": history, "current": current, "current_decision": focus}
        instructions = (
            "你正在打斗地主，唯一目标是让自己所在阵营先出完牌。只决定current_decision这个最新时刻的动作。"
            "conversation是按时间从旧到新的已结束回合，供记牌与分析；旧手牌、旧last_play和旧回答不能覆盖current。"
            "尤其过去对王炸不出，不代表现在对小单牌也不出。完整公开出牌记录在current.public_action_history。"
            "当前要压的是current_decision.current_last_play，null表示自由领出。单牌可以用任何更大的单张接，允许拆对子。"
            "候选附有准确牌型、实际剩余手牌、是否立即走完和拆牌估算。先看能否合法直接走完，再比较本方先走的路径。"
            "上手是opponent时，用低拆牌成本的牌压住小牌、减少自己的散张并争取出牌权；"
            "如果接掉散张后只剩一组完整对子，不要为保留较小散张而无故pass或拆掉该对子。"
            "对手报单/报双时优先防止其下次直接走完，结合出牌顺序考虑用大单/大对拦截，而非机械放行。"
            "enemy_last_single为真且当前跟单时，优先考虑strongest_same_shape_reply的大单封堵；只有明确有更好的本方走完路线时才放小。"
            "上手是teammate且没有紧急拦截需要时通常pass，让队友继续走，不浪费大牌压自己人；"
            "自己能直接走完、队友需要接力或下家地主即将走完时可以压队友。"
            "确实接不了或接牌会严重破坏炸弹、连牌且无紧迫威胁时可以pass。不要一律有牌必接，也不要一律遇单就pass。"
            "不猜测对手暗牌；出牌权并不保证，综合敌我剩余张数、公开历史和候选效果选最佳动作。"
            "bidding阶段身份尚未确定，只根据自己高牌控制力、炸弹和成型牌组选bid分数，不把任何座位提前认作队友。"
            "返回所选choice及全部候选的概率。"
        )
    return {
        "model": model,
        "state": state,
        "questions": {
            "action": {
                "type": "choice",
                "instructions": instructions,
                "criteria": criteria,
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
