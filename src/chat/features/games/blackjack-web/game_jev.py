"""Jev 评估协议：从可见局面构造多样候选，模型只选择明确的合法动作。"""

import json
import math
from collections import Counter
from importlib import import_module


traditional = import_module("src.chat.features.games.blackjack-web.traditional_games")
guandan = import_module("src.chat.features.games.blackjack-web.guandan_game")
MAX_PLAY_CANDIDATES = 24
MAX_CANDIDATES = 40
SIMPLE_ACTIONS = ("check", "call", "fold", "all_in", "look", "pass", "pung", "win", "hit", "stand")


def team_decision_facts(context: dict) -> dict:
    """只根据座次、身份和公开剩牌数判断出牌权，不把旧过牌当本轮过牌。"""
    state, you = context["state"], context["you"]
    players = state["players"]
    own_index = next(i for i, player in enumerate(players) if player["user_id"] == you)
    own = players[own_index]
    if context["game_type"] == "landlord":
        landlord = state.get("landlord_id")
        teammate = next((p for p in players if p["user_id"] not in (you, landlord)), None) if landlord and you != landlord else None
        opponents = [p for p in players if p["user_id"] != you and (you == landlord or p["user_id"] == landlord)]
    else:
        teammate = next((p for p in players if p["user_id"] != you and p["team"] == own["team"]), None)
        opponents = [p for p in players if p["team"] != own["team"]]
    following = [players[(own_index + offset) % len(players)] for offset in range(1, len(players))]
    next_player = next((p for p in following if p.get("hand_count", 0) > 0), None)
    previous = state.get("last_play")
    leader = previous.get("user_id") if previous else None
    teammate_leads = bool(teammate and leader == teammate["user_id"])
    next_id = next_player["user_id"] if next_player else None
    # 斗地主当前轮到自己而下家就是最后出牌的队友，说明中间地主已过牌。
    returns_lead = bool(context["game_type"] == "landlord" and teammate_leads and next_id == leader)
    danger = bool(previous and next_player in opponents and previous.get("kind") in ("single", "pair")
                  and previous.get("size") == next_player["hand_count"]
                  and previous.get("rank", 0) < (17 if context["game_type"] == "guandan" or previous.get("kind") == "single" else 15))
    teammate_count = teammate.get("hand_count") if teammate else None
    facts = {
        "teammate": teammate["user_id"] if teammate else None,
        "teammate_remaining_count": teammate_count,
        "teammate_close_to_finish": teammate_count in (1, 2),
        "opponents": [{"seat": p["user_id"], "remaining_count": p["hand_count"]} for p in opponents],
        "next_seat": next_id,
        "current_last_play": previous,
        "last_play_relation": "teammate" if teammate_leads else "opponent" if leader else "free_lead",
        "pass_returns_lead_to_teammate": returns_lead,
        "next_opponent_can_finish_on_this_shape": danger,
        "protect_teammate_finish": bool(teammate_leads and teammate_count in (1, 2) and not danger),
    }
    if context["game_type"] == "landlord":
        cooperation = traditional.landlord_team_context(state, you)
        facts.update(cooperation)
        if teammate_leads:
            facts["next_opponent_can_finish_on_this_shape"] = cooperation["must_block_landlord_finish"]
            facts["protect_teammate_finish"] = cooperation["teammate_close_to_finish"] and not cooperation["must_block_landlord_finish"]
    elif teammate_leads:
        # 最后一次出牌到当前座位之间只能是过牌；旧 seat_actions 不能证明本轮已过。
        leader_index = next(i for i, player in enumerate(players) if player["user_id"] == leader)
        passed = set()
        index = (leader_index + 1) % len(players)
        while index != own_index:
            passed.add(players[index]["user_id"])
            index = (index + 1) % len(players)
        waiting = {p["user_id"] for p in players if p["hand_count"] > 0 and p["user_id"] != leader}
        facts["pass_returns_lead_to_teammate"] = teammate_count > 0 and waiting <= passed | {you}
        threats = []
        if next_player in opponents and next_id not in passed:
            visible = set(own["hand"]) | set(previous.get("cards", []))
            for event in state.get("seat_actions", {}).values():
                visible.update(event.get("cards", []))
            for event in context.get("public_action_history", []):
                visible.update(event.get("cards", []))
            before = guandan.GuandanPattern(previous["kind"], previous["rank"], previous["size"])
            threats = guandan.guandan_finishing_threats(visible, state["level"], before, next_player["hand_count"])
        facts["guandan_possible_finishing_replies"] = [pattern.to_dict() for pattern in threats]
        facts["next_opponent_can_finish_on_this_shape"] = bool(threats)
        facts["protect_teammate_finish"] = teammate_count in (1, 2) and not threats
    return facts


def _guandan_play_facts(hand: list, cards: list, level: str, previous: dict | None = None, combo: str | None = None) -> dict:
    """解释引擎实际采用的通配牌型及自己剩牌；不假设其他人的暗牌。"""
    patterns = guandan.classify_guandan_cards(cards, level)
    if previous:
        before = guandan.GuandanPattern(previous["kind"], previous["rank"], previous["size"])
        patterns = [pattern for pattern in patterns if guandan.guandan_beats(pattern, before)]
    if combo is not None:
        patterns = [pattern for pattern in patterns if pattern.combo == combo]
    if not patterns:
        raise ValueError("掼蛋候选无法压过上手")
    pattern = patterns[0]
    analysis = guandan.guandan_hand_analysis(hand, level, cards)
    remaining = analysis["remaining_hand"]
    return {
        "pattern": pattern.to_dict(), "remaining_hand": remaining, "remaining_count": len(remaining),
        "finishes_own_hand": not remaining, "splits_same_rank_groups": analysis["splits_same_rank_groups"],
        "breaks_natural_bombs": analysis["breaks_natural_bombs"], "breaks_four_jokers": analysis["splits_four_jokers"],
        "wild_cards_used": analysis["wildcards_used"],
        "remaining_rank_groups": [{"rank": rank, "count": count} for rank, count in sorted(Counter(guandan.card_parts(card)[1] for card in remaining).items())],
    }


def strategic_action_allowed(context: dict, action: dict, hand_plan=None) -> bool:
    """只约束明确的低价值拆弹/堵队友残局，普通局面仍由模型比较。"""
    kind, state = context.get("game_type"), context["state"]
    if kind not in ("landlord", "guandan") or action.get("action") != "play":
        return True
    own = next(p for p in state["players"] if p["user_id"] == context["you"])
    cards, hand = action.get("cards", []), own["hand"]
    if Counter(cards) == Counter(hand):
        return True
    facts = team_decision_facts(context)
    if facts["protect_teammate_finish"] and "pass" in state.get("legal_actions", []):
        return False
    if kind == "landlord" and facts.get("teammate_leads") and facts["teammate_close_to_finish"]:
        pattern = traditional.classify_landlord_cards(cards)
        if not traditional.landlord_blocks_finishing_reply(pattern, facts):
            return False
    if kind == "guandan" and facts["last_play_relation"] == "teammate" and facts["teammate_close_to_finish"]:
        details = _guandan_play_facts(hand, cards, state["level"], state.get("last_play"), action.get("combo"))
        actual = details["pattern"]
        pattern = guandan.GuandanPattern(actual["kind"], actual["rank"], actual["size"])
        threats = [guandan.GuandanPattern(row["kind"], row["rank"], row["size"])
                   for row in facts.get("guandan_possible_finishing_replies", [])]
        if not any(not guandan.guandan_beats(threat, pattern) for threat in threats):
            return False
    if kind == "guandan" and state.get("last_play") is None:
        details = _guandan_play_facts(hand, cards, state["level"], combo=action.get("combo"))
        if details["pattern"]["kind"] in ("single", "pair", "triple"):
            if details["breaks_natural_bombs"] or details["breaks_four_jokers"]:
                # 自由领出始终可以完整打炸弹，不需要拆成若干单张才取得出牌权。
                return False
            if len(hand) <= 10:
                plan = (hand_plan or guandan.guandan_hand_plan(hand, state["level"]))(cards)
                if plan["sequence_split_worsens_plan"] and plan["estimated_extra_plays"] >= 2:
                    # 仅去掉短残局中把完整连牌拆散、平添至少两手的明显劣势领牌。
                    return False
    return True


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
    hand_plan = guandan.guandan_hand_plan(own["hand"], state["level"]) if kind == "guandan" else None

    def add(action: dict):
        if action["action"] not in legal or action in candidates.values():
            return
        if not strategic_action_allowed(context, action, hand_plan):
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
                    action = {"action": "play", "cards": list(cards)}
                    if option.get("combo"):
                        action["combo"] = option["combo"]
                    add(action)
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
    if kind in ("landlord", "guandan"):
        finishing = {name: action for name, action in candidates.items()
                     if action["action"] == "play" and Counter(action["cards"]) == Counter(own["hand"])}
        if finishing:
            # 斗地主任一农民清手就是本队胜利，不能为让队友而放弃已经能赢的动作。
            return finishing
    return candidates


def _landlord_decision(current: dict, candidates: dict) -> tuple[dict, dict]:
    """把当前敌我关系与自己的拆牌结果显式化，不读取任何其他座位暗牌。"""
    state, you = current["state"], current["you"]
    players = state["players"]
    own = next(player for player in players if player["user_id"] == you)
    hand = own["hand"]
    team_facts = team_decision_facts(current)
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
                         overtakes_teammate=team_facts["last_play_relation"] == "teammate",
                         splits_same_rank_groups=[{"rank": rank, "before": counts[rank], "used": count}
                                                  for rank, count in used.items() if count < counts[rank]])
            if pattern.kind in ("single", "pair"):
                facts["strongest_same_shape_reply"] = pattern.rank == strongest[pattern.kind]
        elif action["action"] == "pass":
            facts.update(remaining_hand=list(hand), remaining_count=len(hand), wins_immediately=False,
                         estimated_remaining_plays=estimate(original, budget=32),
                         returns_lead_to_teammate=team_facts["pass_returns_lead_to_teammate"],
                         meaning="地主已经让过当前队友牌，本次过牌立即把自由领出权还给队友。" if team_facts["pass_returns_lead_to_teammate"]
                         else "本回合不出牌，由下家继续；不会减少自己的手牌。")
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
    focus.update(team_facts)
    return focus, descriptions


def _guandan_decision(current: dict, candidates: dict) -> tuple[dict, dict]:
    state, you = current["state"], current["you"]
    own = next(p for p in state["players"] if p["user_id"] == you)
    hand_plan = guandan.guandan_hand_plan(own["hand"], state["level"])
    focus = {**team_decision_facts(current), "you": you, "your_team": own["team"],
             "phase": state["phase"], "level": state["level"], "your_current_hand": own["hand"],
             "finish_order": state.get("finish_order", []),
             "estimated_current_plays": hand_plan([])["estimated_current_plays"],
             "planning_note": "剩手数是只使用自己手牌的有界估计，不保证牌权，也不等于胜率。remaining_rank_groups只是点数组；broken_sequence_groups配合estimated_extra_plays说明拆连牌代价。"}
    descriptions = {}
    for name, action in candidates.items():
        facts = {"action": action}
        if action["action"] == "play":
            facts.update(_guandan_play_facts(own["hand"], action["cards"], state["level"], state.get("last_play"), action.get("combo")))
            facts.update(hand_plan(action["cards"]))
            facts["overtakes_teammate"] = focus["last_play_relation"] == "teammate"
        else:
            facts.update(remaining_count=len(own["hand"]), finishes_own_hand=False,
                         estimated_remaining_plays=focus["estimated_current_plays"],
                         meaning="让当前上手继续保有牌权，所有未走玩家均过后由上手领出；上手已出完则其队友接风。")
        descriptions[name] = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
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
            "enemy_last_single只说明对手张数，不代表轮到地主或地主还能再次接当前这手。"
            "当前上手为opponent，或next_opponent_can_finish_on_this_shape为真时，才优先考虑大单/大对封堵。"
            "上手为teammate时先看teammate_remaining_count和pass_returns_lead_to_teammate；"
            "若地主已过牌，你pass能把自由领出直接还给只剩一两张的队友，就让队友走，不能用A、2或炸弹堵住他。"
            "只有自己这手立即清空，或下家地主存在末手接走的可能且候选有实际封堵效果，才压临近走完的队友；不能把有大牌当作需要接力。"
            "landlord_possible_finishing_patterns仅列公开信息未排除的末手牌型，不是地主实际手牌；注意末手炸弹可跨牌型接走。"
            "确实接不了或接牌会严重破坏炸弹、连牌且无紧迫威胁时可以pass。不要一律有牌必接，也不要一律遇单就pass。"
            "不猜测对手暗牌；出牌权并不保证，综合敌我剩余张数、公开历史和候选效果选最佳动作。"
            "bidding阶段身份尚未确定，只根据自己高牌控制力、炸弹和成型牌组选bid分数，不把任何座位提前认作队友。"
            "返回所选choice及全部候选的概率。"
        )
    elif current["game_type"] == "guandan":
        focus, criteria = _guandan_decision(current, candidates)
        state = {"conversation": history, "current": current, "current_decision": focus}
        instructions = (
            "你正在打四人掼蛋，与对家同队，以本方争取头游和更好的队伍名次为目标。只决定current_decision这个最新时刻。"
            "conversation是已结束回合，公开出牌历史在current.public_action_history，不能把旧上手当当前。"
            "候选标明引擎实际牌型、剩余手牌、拆点数组、拆炸弹和红桃级牌消耗；先看能否一手出完，再规划完整牌组出牌和接风。"
            "自由领出不是跟单：优先比较完整顺子、三连对、钢板、三带二、对子等组合，不能习惯性只从最小单张开始。"
            "34567是一手顺子，不能打成五手单牌；比较estimated_total_plays_after_action与estimated_current_plays，避免拆连牌增加手数。"
            "345678的端点3打掉还保有45678，和拆散34567不同；只有真实改善路线或紧急阻击才付拆组成本。"
            "有四张J就有自然炸弹，不要拆成J、J、J、J或三张J再一张；可以先走其他完整组或真正散张，保留炸弹夺回牌权。"
            "breaks_natural_bombs或breaks_four_jokers代表损失控制牌；只有能明显改善整手收尾路线才考虑拆组，不能只看当前点数小。"
            "也不能一律先炸或永远保炸：自己最后一手可出完就走，对手即将走完时可用炸弹及时拦截。"
            "对手报单时不要无理由送小单；跟单可为真正阻击拆对子，但不等于自由领出也要拆弹。"
            "当前上手是对手的小单，而你有不拆组、不耗通配且不增加总手数的普通散单可压，应优先接牌走掉散张，不能像让队友那样一律不出。"
            "上手为teammate且快走完时应让牌，除自己立即走完或下家对手可能末手接走且有有效阻击。"
            "guandan_possible_finishing_replies是公开信息未排除的可能牌型，不是敌手实际手牌；末手炸弹可以跨牌型接走。"
            "队友已出完且当前领牌属于队友时尽量让其接风规则发挥作用。不能查看或猜定其他人的具体手牌。"
            "rank_groups不等于出完手数，必须连同实际remaining_hand检查连牌，比较多个不同牌型的完整路线。返回choice及全部候选概率。"
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
