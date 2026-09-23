"""给模型的扑克参考数据：只使用自身已知牌和公开局面，不替模型付费行动。"""

import hashlib
import json
import math
import random
from collections import Counter
from functools import lru_cache
from importlib import import_module


poker = import_module("src.chat.features.games.blackjack-web.poker_games")
EQUITY_SAMPLES = 256
DECK = tuple(poker.create_deck())
DECK_PARTS = {card: poker.card_parts(card) for card in DECK}


def _seed(values) -> int:
    encoded = json.dumps(values, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big")


def _known_cards(hand, board, hand_size):
    cards = list(hand) + list(board)
    if len(hand) != hand_size or len(set(cards)) != len(cards) or any(card not in DECK_PARTS for card in cards):
        return None
    return tuple(sorted(hand)), tuple(board)


@lru_cache(maxsize=512)
def _texas_equity(hand: tuple, board: tuple, opponents: int, pressure: bool) -> tuple:
    """等概率范围是基线；更强范围只作敏感性对照，不能当作对手的真实底牌。"""
    rng = random.Random(_seed(("texas", hand, board, opponents, pressure)))
    known = set(hand + board)
    remaining = [card for card in DECK if card not in known]
    own = [DECK_PARTS[card] for card in hand]
    visible_board = [DECK_PARTS[card] for card in board]
    heads_up = all_active = stronger = 0.0
    runout_count = 5 - len(board)
    for _ in range(EQUITY_SAMPLES):
        draw = rng.sample(remaining, opponents * 2 + runout_count)
        runout = visible_board + [DECK_PARTS[card] for card in draw[opponents * 2:]]
        own_rank = poker._sampled_texas_rank(own + runout)
        other_ranks = [poker._sampled_texas_rank([DECK_PARTS[card] for card in draw[index:index + 2]] + runout)
                       for index in range(0, opponents * 2, 2)]
        heads_up += 1 if own_rank > other_ranks[0] else (0.5 if own_rank == other_ranks[0] else 0)
        best = max(other_ranks)
        all_active += 1 if own_rank > best else (1 / (1 + other_ranks.count(own_rank)) if own_rank == best else 0)
        if pressure:
            # 候选仅按当前已发的牌筛选，绝不能先看未来公共牌再挑对手底牌。
            candidates = [rng.sample(remaining, 2) for _ in range(3)]
            if board:
                selected = max(candidates, key=lambda cards: poker._sampled_texas_rank(
                    [DECK_PARTS[card] for card in cards] + visible_board))
            else:
                selected = max(candidates, key=lambda cards: poker._starting_hand_score([DECK_PARTS[card] for card in cards]))
            future = rng.sample([card for card in remaining if card not in selected], runout_count)
            runout = visible_board + [DECK_PARTS[card] for card in future]
            own_rank = poker._sampled_texas_rank(own + runout)
            other_rank = poker._sampled_texas_rank([DECK_PARTS[card] for card in selected] + runout)
            stronger += 1 if own_rank > other_rank else (0.5 if own_rank == other_rank else 0)
    return (heads_up / EQUITY_SAMPLES, all_active / EQUITY_SAMPLES,
            stronger / EQUITY_SAMPLES if pressure else None)


def texas_hand_analysis(state: dict, you: str) -> dict:
    own = next(player for player in state["players"] if player["user_id"] == you)
    opponents = [player for player in state["players"] if player["user_id"] != you and not player.get("folded")]
    board = state.get("community_cards", [])
    known = _known_cards(own.get("hand", []), board, 2)
    if known is None or len(board) not in (0, 3, 4, 5) or not 1 <= len(opponents) <= 7:
        return {"available": False}
    hand, board = known
    own_parts = [DECK_PARTS[card] for card in hand]
    board_parts = [DECK_PARTS[card] for card in board]
    ranks = sorted((rank for _, rank in own_parts), reverse=True)
    all_in_seats = [player["user_id"] for player in opponents if player.get("all_in")]
    call_cost = state.get("call_amount", 0)
    pressure = bool(call_cost and (all_in_seats or state.get("current_bet", 0) >=
                    max(state.get("big_blind", 1) * 4, state.get("pot", 0) * 0.35)))
    heads_up, all_active, stronger = _texas_equity(hand, board, len(opponents), pressure)
    equity = {
        "method": "deterministic_monte_carlo", "samples": EQUITY_SAMPLES,
        "max_sampling_standard_error": round(0.5 / math.sqrt(EQUITY_SAMPLES), 4),
        "heads_up_random_share": round(heads_up, 4),
        "all_active_random_share": round(all_active, 4),
        "all_active_opponents": len(opponents),
        "assumptions": "仅排除自身底牌与公共牌，其余牌等概率抽样；双方都摊牌，平局平分。不是对手真实范围，不含弃牌收益或未来下注。",
        "preflop_caution": "多人未加注底池中尚未行动者可能弃牌；全桌同时摊牌胜率不能直接当成入池或加注后的胜率。",
    }
    if stronger is not None:
        equity["heads_up_stronger_range_share"] = round(stronger, 4)
        equity["stronger_range_assumption"] = "单挑敏感性对照：从三组随机候选中选当前牌力较强的一组；翻前用起手牌启发式排序。仅是假设，不表示大注或全下者必有强牌。"
    analysis = {
        "available": True,
        "starting_hand": {
            "ranks": ranks, "pocket_pair": ranks[0] == ranks[1],
            "suited": own_parts[0][0] == own_parts[1][0],
            "gap": max(0, ranks[0] - ranks[1] - 1),
            "high_cards_10_plus": sum(rank >= 10 for rank in ranks),
            "contains_ace": 14 in ranks,
        },
        "equity_reference": equity, "opponents_all_in": all_in_seats,
        "all_in_is_not_a_hand_strength": bool(all_in_seats),
    }
    if board:
        rank = poker._sampled_texas_rank(own_parts + board_parts)
        suits = Counter(suit for suit, _ in own_parts + board_parts)
        board_ranks = {value for _, value in board_parts}
        # 只标记至少使用一张自己独有点数/花色底牌的听牌，避免把公共听牌当私有优势。
        unique_own_ranks = set(ranks) - board_ranks
        available_ranks = set(ranks) | board_ranks
        straight_ranks = set()
        if len(board) < 5 and rank[0] < 4:
            for high in range(5, 15):
                sequence = {14, 2, 3, 4, 5} if high == 5 else set(range(high - 4, high + 1))
                missing = sequence - available_ranks
                if len(missing) == 1 and sequence & unique_own_ranks:
                    straight_ranks.update(missing)
        flush_draws = []
        if len(board) < 5 and rank[0] < 5:
            for suit, count in suits.items():
                own_suit_ranks = {value for own_suit, value in own_parts if own_suit == suit}
                if count == 4 and own_suit_ranks:
                    board_suit_ranks = {value for board_suit, value in board_parts if board_suit == suit}
                    best_hole_rank = max(set(range(2, 15)) - board_suit_ranks)
                    flush_draws.append({"suit": suit, "nut_flush_draw": best_hole_rank in own_suit_ranks})
        analysis.update({
            "made_hand": poker.TEXAS_HAND_NAMES[rank[0]],
            "flush_draws": flush_draws,
            "straight_completing_ranks": sorted(straight_ranks),
            "draw_caution": "听牌命中不保证获胜；留意同花、成对牌面及反向隐含赔率。河牌没有未来听牌。",
        })
    return analysis


@lru_cache(maxsize=512)
def _golden_equity(hand: tuple, opponents: int) -> tuple:
    rng = random.Random(_seed(("golden_flower", hand, opponents)))
    remaining = [card for card in DECK if card not in hand]
    own_rank = poker.evaluate_golden_hand(list(hand))
    heads_up_wins = heads_up_ties = all_active = 0.0
    for _ in range(EQUITY_SAMPLES):
        draw = rng.sample(remaining, opponents * 3)
        others = [poker.evaluate_golden_hand(draw[index:index + 3]) for index in range(0, opponents * 3, 3)]
        heads_up_wins += own_rank > others[0]
        heads_up_ties += own_rank == others[0]
        best = max(others)
        all_active += 1 if own_rank > best else (1 / (1 + others.count(own_rank)) if own_rank == best else 0)
    return tuple(value / EQUITY_SAMPLES for value in (heads_up_wins, heads_up_ties, all_active))


def golden_flower_strategy(state: dict, you: str) -> dict:
    own = next(player for player in state["players"] if player["user_id"] == you)
    opponents = [player for player in state["players"] if player["user_id"] != you and not player.get("folded")]
    stack = own.get("stack", 0)
    pot = state.get("pot", 0)
    call_cost, compare_cost = state.get("call_amount", 0), state.get("compare_cost", 0)
    samples = []
    if "raise" in state.get("legal_actions", []):
        for target in (state["min_raise_to"], min(state["max_raise_to"], state["base_bet"] * 3)):
            if target < state["min_raise_to"] or any(row["raise_to"] == target for row in samples):
                continue
            cost = target * (2 if own.get("seen") else 1)
            samples.append({
                "raise_to": target, "additional_cost": cost,
                "stack_fraction": round(cost / max(1, stack), 4),
                "zero_equity_bluff_break_even_all_fold_rate": round(cost / max(1, pot + cost), 4),
                "opponents_next_call_cost": [{"seat": player["user_id"], "cost": target * (2 if player.get("seen") else 1)}
                                             for player in opponents],
            })
    result = {
        "active_opponents": len(opponents),
        "unseen_opponents": [player["user_id"] for player in opponents if not player.get("seen")],
        "seen_opponents": [player["user_id"] for player in opponents if player.get("seen")],
        "own_hand_seen": bool(own.get("seen")),
        "look_cost_now": 0, "look_ends_turn": False,
        "future_cost_multiplier_after_look": 2,
        "call_cost": call_cost, "call_stack_fraction": round(call_cost / max(1, stack), 4),
        "compare_cost": compare_cost, "compare_stack_fraction": round(compare_cost / max(1, stack), 4),
        "heads_up_compare_break_even_win_rate": round(compare_cost / max(1, pot + compare_cost), 4) if len(opponents) == 1 else None,
        "compare_caution": "仅单挑比牌胜者立即拿走底池；多人比牌只淘汰一人，不等于立即赢池。同牌力时发起方负。",
        "raise_to_examples": samples,
        "mix_percentile": _seed((you, state)) % 100,
        "unseen_range_note": "未看牌对手不知道自己的牌，其盲跟或盲加注本身不能证明手牌强；看牌也不等于有强牌，要结合看牌后的公开行动。",
    }
    known = _known_cards(own.get("hand", []), [], 3) if own.get("seen") else None
    if known is None or not 1 <= len(opponents) <= 4:
        result["equity_reference"] = {"available": False, "reason": "未看牌时不读取或估计自己的具体牌力。"}
        return result
    hand, _ = known
    wins, ties, all_active = _golden_equity(hand, len(opponents))
    result["made_hand"] = poker.GOLDEN_HAND_NAMES[poker.evaluate_golden_hand(list(hand))[0]]
    result["equity_reference"] = {
        "available": True, "method": "deterministic_monte_carlo", "samples": EQUITY_SAMPLES,
        "max_sampling_standard_error": round(0.5 / math.sqrt(EQUITY_SAMPLES), 4),
        "heads_up_random_strict_win_rate": round(wins, 4),
        "heads_up_random_tie_rate": round(ties, 4),
        "all_active_random_showdown_share": round(all_active, 4),
        "assumptions": "只排除自己的三张已看牌，其余牌等概率抽样；对手仍是未知范围。未看牌者盲打不按强牌收紧，已看牌者的跟加注历史需自行调整；不含诈唬弃牌收益。",
    }
    return result
