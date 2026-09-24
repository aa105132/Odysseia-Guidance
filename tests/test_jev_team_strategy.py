"""真实模型请求链路的掼蛋牌组保留与斗地主残局协作回归。"""

import asyncio
from collections import Counter
from copy import deepcopy
import importlib
import json
from types import SimpleNamespace

import httpx
import pytest


llm = importlib.import_module("src.chat.features.games.blackjack-web.game_llm")
jev = importlib.import_module("src.chat.features.games.blackjack-web.game_jev")
guandan = importlib.import_module("src.chat.features.games.blackjack-web.guandan_game")
landlord = importlib.import_module("src.chat.features.games.blackjack-web.traditional_games")
poker = importlib.import_module("src.chat.features.games.blackjack-web.poker_games")
runner_module = importlib.import_module("src.chat.features.games.blackjack-web.game_bot_runner")


def gd_cards(*ranks):
    """为指定点数生成彼此不同的实体牌。"""
    counts, result = Counter(), []
    for rank in ranks:
        index = counts[rank]
        counts[rank] += 1
        result.append(f"{rank}#{index}" if rank.startswith("Joker")
                      else f"{guandan.SUITS[index % 4]}{rank}#{index // 4}")
    return result


def gd_game(hand):
    game = guandan.GuandanGame(["a", "b", "c", "d"], seed=0)
    game.turn_index = 0
    game.hands["a"] = list(hand)
    remaining = [card for card in guandan.DECK if card not in hand]
    for index, uid in enumerate(("b", "c", "d")):
        game.hands[uid] = remaining[index * 7:(index + 1) * 7]
    return game


def teammate_lead(own_hand, remaining, lead, *, own="b", enemy=None):
    game = landlord.LandlordGame(["a", "b", "c"], seed=17)
    game.act("a", "bid", bid=3)
    teammate = "c" if own == "b" else "b"
    game.hands = {"a": list(enemy or ["Club8", "Heart9", "Spade10"]),
                  own: list(own_hand), teammate: list(lead) + list(remaining)}
    game.bottom_cards = []
    game.turn_index = game.player_ids.index(teammate)
    game.act(teammate, "play", cards=list(lead))
    if game.current_player_id == "a":
        game.act("a", "pass")
    assert game.current_player_id == own
    return game


def context_and_candidates(kind, game):
    context = llm.build_table_context(kind, game, game.current_player_id)
    return context, jev.build_action_candidates(context)


def evaluation(context, candidates):
    current = json.loads(llm.conversation_user_message(context)["content"])
    payload = jev.evaluation_payload("jev", current, context.get("_conversation", []), candidates)
    facts = {key: json.loads(value) for key, value in payload["questions"]["action"]["criteria"].items()}
    return payload, facts


def executed_pattern(game, action):
    trial = deepcopy(game)
    trial.act(game.current_player_id, **action)
    return trial.last_play


def destructive_simple(hand, cards, pattern):
    """测试按实体点数独立判定拆弹，不复用被测试的策略判定函数。"""
    if len(cards) == len(hand) or pattern.kind not in ("single", "pair", "triple"):
        return False
    counts = Counter(guandan.card_parts(card)[1] for card in hand)
    used = Counter(guandan.card_parts(card)[1] for card in cards)
    natural = any(rank < 16 and count >= 4 and 0 < used[rank] < 4 and count - used[rank] < 4
                  for rank, count in counts.items())
    four_jokers = counts[16] == counts[17] == 2 and 0 < used[16] + used[17] < 4
    return natural or four_jokers


def test_one_hundred_seeds_keep_safe_shape_representatives_through_model_and_engine():
    for seed in range(100):
        game = guandan.GuandanGame(["a", "b", "c", "d"], seed=seed)
        uid = game.current_player_id
        hand = game.hands[uid]
        complete = list(game._candidates(uid))
        expected = {(pattern.kind, pattern.size) for cards, pattern in complete
                    if not destructive_simple(hand, cards, pattern)}
        public = game.public_state(uid)
        assert {(row["kind"], row["size"]) for row in public["play_options"]} == {
            (pattern.kind, pattern.size) for _, pattern in complete}, seed
        context, candidates = context_and_candidates("guandan", game)
        assert len(context["state"]["play_options"]) <= 24
        options = {(row["kind"], row["size"]) for row in context["state"]["play_options"]}
        assert expected <= options, (seed, expected - options)
        observed = set()
        for action in candidates.values():
            actual = executed_pattern(game, action)
            observed.add((actual["kind"], actual["size"]))
        assert expected <= observed, (seed, expected - observed)


def test_context_limit_does_not_discard_safe_pairs_after_rejecting_bomb_splits():
    game = guandan.GuandanGame(["a", "b", "c", "d"], seed=93)
    context, candidates = context_and_candidates("guandan", game)
    # 大小端抽到的对子会拆四张级牌，但同一手里仍有普通10、J、Q、A对子。
    pairs = [action for action in candidates.values() if executed_pattern(game, action)["kind"] == "pair"]
    assert pairs, "保护炸弹不能因先截24个候选而把全部安全对子一并挤掉"
    for action in pairs:
        ranks = Counter(guandan.card_parts(card)[1] for card in action["cards"])
        assert 2 not in ranks
    assert len(context["state"]["play_options"]) <= 24


@pytest.mark.parametrize("extra", [("4",), ("4", "4"), ("7", "7", "7", "7")])
def test_free_lead_preserves_four_jacks_and_other_non_destructive_options(extra):
    game = gd_game(gd_cards("J", "J", "J", "J", *extra))
    _, candidates = context_and_candidates("guandan", game)
    ranks = []
    for action in candidates.values():
        played = Counter(guandan.card_parts(card)[1] for card in action["cards"])
        actual = executed_pattern(game, action)
        assert not (0 < played[11] < 4 and actual["kind"] in ("single", "pair", "triple"))
        ranks.append(actual["kind"])
    assert "bomb" in ranks
    assert ("pair" if extra == ("4", "4") else "single" if extra == ("4",) else "bomb") in ranks


def test_direct_complete_hand_stays_available_with_multiple_wildcard_interpretations():
    game = gd_game(["Club3#0", "Diamond3#0", "Heart3#0", "Heart2#0", "Heart2#1"])
    _, candidates = context_and_candidates("guandan", game)
    finishing = [action for action in candidates.values() if Counter(action["cards"]) == Counter(game.hands["a"])]
    assert finishing
    assert {executed_pattern(game, action)["kind"] for action in finishing} >= {"full_house", "bomb"}


def test_guandan_facts_explain_actual_remaining_hand_bomb_loss_and_wildcard_use():
    hand = gd_cards("J", "J", "J", "J", "4", "4") + ["Heart2#0"]
    game = gd_game(hand)
    game.hands["b"] = ["Diamond10#1"]
    game.last_pattern = guandan.classify_guandan_cards(["Spade5#1"], game.level)[0]
    game.last_play = {"user_id": "d", "cards": ["Spade5#1"], **game.last_pattern.to_dict()}
    context, candidates = context_and_candidates("guandan", game)
    context["public_action_history"] = [{"seat": "seat_4", "action": "play", "cards": ["Spade5#1"]}]
    context["_conversation"] = [{"role": "user", "content": '{"round":1}'},
                                {"role": "assistant", "content": '{"action":"pass"}'}]
    before = deepcopy(context)
    payload, facts = evaluation(context, candidates)
    assert context == before
    assert payload["state"]["conversation"] == context["_conversation"]
    assert payload["state"]["current"]["public_action_history"] == context["public_action_history"]
    focus = payload["state"]["current_decision"]
    assert focus["your_team"] == 0 and focus["teammate"] == "seat_3"
    assert focus["opponents"] == [{"seat": "seat_2", "remaining_count": 1},
                                  {"seat": "seat_4", "remaining_count": 7}]
    singles = [row for row in facts.values() if row["action"].get("cards") == ["ClubJ#0"]]
    assert singles, "对手报单时仍应允许拆J作真实阻击"
    assert singles[0]["breaks_natural_bombs"] == [{"rank": 11, "before": 4, "used": 1, "remaining": 3}]
    assert any(row.get("wild_cards_used", 0) for row in facts.values())
    for row in facts.values():
        if row["action"]["action"] != "play":
            continue
        actual = executed_pattern(game, row["action"])
        assert row["pattern"]["kind"] == actual["kind"]
        assert row["pattern"]["rank"] == actual["rank"]
        assert Counter(row["remaining_hand"]) == Counter(hand) - Counter(row["action"]["cards"])
        assert row["remaining_count"] == len(row["remaining_hand"])
    assert "_seat_ids" not in json.dumps(payload)
    assert all(not player["hand"] for player in payload["state"]["current"]["state"]["players"]
               if player["user_id"] != context["you"])


@pytest.mark.parametrize("remaining", [["Club3"], ["Club3", "Heart3"]])
def test_landlord_already_passed_only_yield_or_immediate_team_win_remain(remaining):
    game = teammate_lead(["Spade6", "SpadeA", "JokerBig"], remaining, ["Spade4"], enemy=["Spade5"])
    context, candidates = context_and_candidates("landlord", game)
    assert candidates == {"pass": {"action": "pass"}}
    payload, facts = evaluation(context, candidates)
    assert payload["state"]["current_decision"]["pass_returns_lead_to_teammate"]
    assert facts["pass"]["returns_lead_to_teammate"]
    game.hands["b"] = ["SpadeA"]
    _, finishing = context_and_candidates("landlord", game)
    assert list(finishing.values()) == [{"action": "play", "cards": ["SpadeA"]}]


def test_teammate_ace_allows_real_cover_but_not_a_reply_below_every_possible_threat():
    game = teammate_lead(["Club3", "Spade2", "JokerBig"], ["Diamond3"], ["SpadeA"],
                         own="c", enemy=["Club2"])
    context, candidates = context_and_candidates("landlord", game)
    assert any(action.get("cards") == ["JokerBig"] for action in candidates.values())
    assert jev.team_decision_facts(context)["must_block_landlord_finish"]
    game.hands["c"] = ["Club3", "Spade2", "Club2", "Heart2", "Diamond2", "JokerSmall"]
    game.hands["a"] = ["JokerBig"]
    _, candidates = context_and_candidates("landlord", game)
    assert not any(action.get("cards") == ["JokerSmall"] for action in candidates.values())
    assert any(landlord.classify_landlord_cards(action["cards"]).kind == "bomb"
               for action in candidates.values() if action["action"] == "play")


def test_both_jokers_in_own_hand_mean_teammate_two_is_already_safe():
    game = teammate_lead(["Club3", "JokerSmall", "JokerBig"], ["Diamond3"], ["Spade2"],
                         own="c", enemy=["ClubA"])
    context, candidates = context_and_candidates("landlord", game)
    assert candidates == {"pass": {"action": "pass"}}
    assert not jev.team_decision_facts(context)["must_block_landlord_finish"]


def test_short_teammate_protection_keeps_emergency_triple_cover():
    game = teammate_lead(["ClubA", "DiamondA", "HeartA", "Spade4"], ["Club3"],
                         ["Club6", "Diamond6", "Heart6"], own="c",
                         enemy=["ClubK", "DiamondK", "HeartK"])
    context, candidates = context_and_candidates("landlord", game)
    target = {"action": "play", "cards": ["ClubA", "DiamondA", "HeartA"]}
    assert any(Counter(action.get("cards", [])) == Counter(target["cards"])
               for action in candidates.values()), "地主可能三张直接走完，不能把未识别的牌型威胁当作安全"
    assert llm.GameLLMClient._validate_action(target, context) == target
    deepcopy(game).act(game.current_player_id, **target)


def test_short_teammate_protection_keeps_cover_for_opponent_last_bomb():
    game = teammate_lead(["ClubA", "DiamondA", "HeartA", "SpadeA", "Spade4"], ["Club3"],
                         ["Club6"], own="c", enemy=["ClubK", "DiamondK", "HeartK", "SpadeK"])
    context, candidates = context_and_candidates("landlord", game)
    target = {"action": "play", "cards": ["ClubA", "DiamondA", "HeartA", "SpadeA"]}
    assert any(Counter(action.get("cards", [])) == Counter(target["cards"])
               for action in candidates.values()), "地主的末手炸弹可以跨牌型压单张，应保留有效的炸弹阻击"
    assert llm.GameLLMClient._validate_action(target, context) == target
    deepcopy(game).act(game.current_player_id, **target)


@pytest.mark.parametrize("lead_ranks,own_ranks,enemy_ranks,kind", [
    (("6", "6", "6"), ("A", "A", "A", "4"), ("K", "K", "K"), "triple"),
    (("6",), ("A", "A", "A", "A", "4"), ("K", "K", "K", "K"), "bomb"),
])
def test_guandan_short_teammate_preserves_emergency_triple_and_bomb_cover(
        lead_ranks, own_ranks, enemy_ranks, kind):
    game = gd_game(gd_cards(*own_ranks))
    game.hands["b"] = gd_cards(*enemy_ranks)
    game.hands["c"] = gd_cards("3", *lead_ranks)
    game.hands["d"] = gd_cards("7", "8")
    game.turn_index = 2
    game.act("c", "play", cards=gd_cards(*lead_ranks))
    game.act("d", "pass")
    assert game.current_player_id == "a"
    context, candidates = context_and_candidates("guandan", game)
    expected = Counter(gd_cards(*("A",) * (3 if kind == "triple" else 4)))
    blockers = [action for action in candidates.values() if Counter(action.get("cards", [])) == expected]
    assert blockers, "队友快走完时仍须保留能封住下家末手的三张或炸弹"
    assert jev.team_decision_facts(context)["next_opponent_can_finish_on_this_shape"]
    for action in blockers:
        assert llm.GameLLMClient._validate_action(action, context) == action
        assert executed_pattern(game, action)["kind"] == kind


def test_guandan_finished_enemy_and_passed_enemy_return_lead_to_teammate():
    game = gd_game(gd_cards("A", "4"))
    game.hands["b"] = []
    game.finish_order = ["b"]
    game.hands["c"] = gd_cards("3", "6")
    game.hands["d"] = gd_cards("K")
    game.turn_index = 2
    game.act("c", "play", cards=gd_cards("6"))
    game.act("d", "pass")
    assert game.current_player_id == "a"
    context, candidates = context_and_candidates("guandan", game)
    facts = jev.team_decision_facts(context)
    assert facts["pass_returns_lead_to_teammate"]
    assert not facts["next_opponent_can_finish_on_this_shape"]
    assert not facts["guandan_possible_finishing_replies"]
    assert candidates == {"pass": {"action": "pass"}}
    game.act("a", "pass")
    assert game.current_player_id == "c" and game.last_play is None


@pytest.mark.parametrize("combo", ["bomb:14:4:", "single:17:1:", "", [], "x" * 81])
def test_guandan_rejects_invalid_or_mismatched_combo(combo):
    game = gd_game(gd_cards("4", "8"))
    context, _ = context_and_candidates("guandan", game)
    action = {"action": "play", "cards": gd_cards("4"), "combo": combo}
    assert llm.GameLLMClient._validate_action(action, context) is None


def test_non_guandan_action_cannot_supply_combo():
    game = teammate_lead(["SpadeA"], ["Club3"], ["Spade4"], enemy=["Spade5"])
    context, _ = context_and_candidates("landlord", game)
    action = {"action": "play", "cards": ["SpadeA"], "combo": "single:14:1:"}
    assert llm.GameLLMClient._validate_action(action, context) is None


@pytest.mark.parametrize("kind", ["guandan", "landlord"])
def test_team_summaries_never_read_other_players_hidden_cards(kind):
    class HiddenHand:
        def __init__(self, count):
            self.count = count

        def __len__(self):
            return self.count

        def __iter__(self):
            raise AssertionError("不允许读取其他座位暗牌")

    game = gd_game(gd_cards("J", "J", "J", "J", "4", "4")) if kind == "guandan" else teammate_lead(
        ["Club3", "Spade2", "JokerBig"], ["Diamond3"], ["SpadeA"], own="c", enemy=["Club2"])
    context, candidates = context_and_candidates(kind, game)
    expected = evaluation(context, candidates)
    for uid in game.hands:
        if uid != game.current_player_id:
            game.hands[uid] = HiddenHand(len(game.hands[uid]))
    changed, updated = context_and_candidates(kind, game)
    assert evaluation(changed, updated) == expected


def model_client(monkeypatch, handler, model="jev"):
    monkeypatch.setenv("GAME_LLM_ENABLED", "true")
    monkeypatch.setenv("GAME_LLM_BASE_URL", "https://bufan.test/v1")
    monkeypatch.setenv("GAME_LLM_API_KEY", "SYNTHETIC-KEY")
    monkeypatch.setenv("GAME_LLM_MODEL", model)
    original = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return llm.GameLLMClient()


def evaluation_reply(criteria, choice):
    probabilities = {name: float(name == choice) for name in criteria}
    if choice not in criteria:
        probabilities[next(iter(criteria))] = 1.0
    return {"answers": {"action": {"type": "choice", "choice": choice, "probabilities": probabilities}}}


async def actual_runner_action(model, kind, game):
    runner = runner_module.GameBotRunner(model, None)
    room = SimpleNamespace(room_id="TEAM-REGRESSION", round_number=1, revision=1, turn_deadline=0,
                           game_type=kind, state="playing", engine=game)
    try:
        assert runner.table_action(room, game.current_player_id) is None
        await asyncio.gather(*(pending.task for pending in runner.pending.values()))
        return runner.table_action(room, game.current_player_id)
    finally:
        await runner.close()


@pytest.mark.asyncio
async def test_real_jev_request_yields_to_short_teammate_and_retains_history(monkeypatch):
    game = teammate_lead(["Spade6", "SpadeA"], ["Club3"], ["Spade4"], enemy=["Spade5"])
    context, _ = context_and_candidates("landlord", game)
    context["_conversation"] = [{"role": "assistant", "content": '{"action":"pass"}'}]
    context["public_action_history"] = [{"seat": "seat_3", "action": "play", "cards": ["Spade4"]},
                                        {"seat": "seat_1", "action": "pass"}]

    def handler(request):
        payload = json.loads(request.content)
        assert request.url.path == "/v1/evaluations"
        assert payload["state"]["conversation"] == context["_conversation"]
        assert payload["state"]["current"]["public_action_history"] == context["public_action_history"]
        criteria = payload["questions"]["action"]["criteria"]
        assert list(criteria) == ["pass"]
        return httpx.Response(200, json=evaluation_reply(criteria, "pass"))

    assert await model_client(monkeypatch, handler).choose_action(context) == {"action": "pass"}


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["jev", "打牌LLM"])
@pytest.mark.parametrize("kind", ["guandan", "landlord"])
async def test_invalid_or_obviously_harmful_model_output_reaches_safe_algorithm(monkeypatch, model, kind):
    game = gd_game(gd_cards("J", "J", "J", "J", "4", "4", "8")) if kind == "guandan" else teammate_lead(
        ["Spade6", "SpadeA"], ["Club3"], ["Spade4"], enemy=["Spade5"])
    harmful = {"action": "play", "cards": ["ClubJ#0"] if kind == "guandan" else ["SpadeA"]}
    context, _ = context_and_candidates(kind, game)
    captured = []

    def handler(request):
        payload = json.loads(request.content)
        captured.append(payload)
        if model == "jev":
            criteria = payload["questions"]["action"]["criteria"]
            assert not any(json.loads(row)["action"] == harmful for row in criteria.values())
            return httpx.Response(200, json=evaluation_reply(criteria, "unknown_candidate"))
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(harmful)}}]})

    model_instance = model_client(monkeypatch, handler, model)
    assert await model_instance.choose_action(context) is None
    expected = deepcopy(game).suggest_action(game.current_player_id)
    chosen = await actual_runner_action(model_instance, kind, game)
    assert chosen == expected
    assert chosen != harmful
    deepcopy(game).act(game.current_player_id, **chosen)
    assert len(captured) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["jev", "打牌LLM"])
async def test_real_model_can_choose_guandan_steel_plate_without_changing_its_interpretation(monkeypatch, model):
    game = guandan.GuandanGame(["a", "b", "c", "d"], seed=68)
    context, candidates = context_and_candidates("guandan", game)
    targets = [action for action in candidates.values() if executed_pattern(game, action)["kind"] == "triple_straight"]
    assert targets, "候选中应有实际按钢板执行的动作，而不只是同一牌组的三连对解释"
    target = targets[0]

    def handler(request):
        payload = json.loads(request.content)
        if model == "jev":
            criteria = payload["questions"]["action"]["criteria"]
            choice = next(name for name, row in criteria.items() if json.loads(row)["action"] == target)
            return httpx.Response(200, json=evaluation_reply(criteria, choice))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(target)}}]})

    action = await model_client(monkeypatch, handler, model).choose_action(context)
    assert action == target
    assert executed_pattern(game, action)["kind"] == "triple_straight"


@pytest.mark.parametrize("kind", ["texas", "golden_flower", "landlord"])
def test_team_guard_does_not_remove_normal_opponent_actions_or_landlord_role(kind):
    if kind == "texas":
        game = poker.TexasHoldemGame(["a", "b"], seed=42)
        action = {"action": "call"}
    elif kind == "golden_flower":
        game = poker.GoldenFlowerGame(["a", "b"], seed=42)
        action = {"action": "call"}
    else:
        game = landlord.LandlordGame(["a", "b", "c"], seed=17)
        game.act("a", "bid", bid=3)
        game.hands = {"a": ["Spade6", "SpadeA"], "b": ["Club3", "Spade4"], "c": ["Heart5"]}
        game.bottom_cards = []
        game.turn_index = 1
        game.act("b", "play", cards=["Spade4"])
        game.act("c", "pass")
        action = {"action": "play", "cards": ["Spade6"]}
    context, candidates = context_and_candidates(kind, game)
    assert action in candidates.values()
    assert llm.GameLLMClient._validate_action(action, context) == action
    deepcopy(game).act(game.current_player_id, **action)
