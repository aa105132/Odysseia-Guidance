"""斗地主 Jev 当前决策：保留记牌并明确敌我、剩牌及合法动作。"""

import copy
import importlib
import json

import httpx
import pytest


llm = importlib.import_module("src.chat.features.games.blackjack-web.game_llm")
jev = importlib.import_module("src.chat.features.games.blackjack-web.game_jev")
traditional = importlib.import_module("src.chat.features.games.blackjack-web.traditional_games")
poker = importlib.import_module("src.chat.features.games.blackjack-web.poker_games")


def reply_game(hand=None, leader="a", enemy_cards=None):
    game = traditional.LandlordGame(["a", "b", "c"], seed=17)
    game.act("a", "bid", bid=3)
    game.hands = {
        "a": enemy_cards or ["Spade4", "Club9", "Heart10", "HeartJ", "HeartQ", "HeartK"],
        "b": hand or ["Diamond6", "Heart2", "Spade2"],
        "c": ["Club4", "Club5", "Club7", "Club8"],
    }
    game.bottom_cards = []
    game.turn_index = game.player_ids.index(leader)
    game.act(leader, "play", cards=[game.hands[leader][0]])
    while game.current_player_id != "b":
        game.act(game.current_player_id, "pass")
    return game


def payload_for(game, history=None):
    context = llm.build_table_context("landlord", game, game.current_player_id)
    candidates = jev.build_action_candidates(context)
    before = copy.deepcopy(context)
    payload = jev.evaluation_payload("jev", context, history or [], candidates)
    assert context == before
    for action in candidates.values():
        copy.deepcopy(game).act(game.current_player_id, **action)
    facts = {name: json.loads(value) for name, value in payload["questions"]["action"]["criteria"].items()}
    assert {name: row["action"] for name, row in facts.items()} == candidates
    return payload, facts


def test_latest_single_stays_current_with_old_rocket_history_and_preserves_pair():
    history = [
        {"role": "user", "content": json.dumps({"last_play": {"cards": ["JokerSmall", "JokerBig"]}})},
        {"role": "assistant", "content": '{"action":"pass"}'},
    ]
    saved = copy.deepcopy(history)
    game = reply_game()
    payload, facts = payload_for(game, history)
    state = payload["state"]
    assert list(state) == ["conversation", "current", "current_decision"]
    assert state["conversation"] == saved == history
    focus = state["current_decision"]
    assert focus["current_last_play"]["cards"] == ["Spade4"]
    assert focus["your_role"] == "farmer"
    assert focus["teammate"] == "seat_3"
    assert focus["opponents"] == [{"seat": "seat_1", "remaining_count": 5}]
    assert focus["last_play_relation"] == "opponent"
    six = next(row for row in facts.values() if row["action"].get("cards") == ["Diamond6"])
    assert six["remaining_hand"] == ["Heart2", "Spade2"]
    assert six["remaining_count"] == 2 and six["estimated_remaining_plays"] == 1
    assert six["splits_same_rank_groups"] == []
    two = next(row for row in facts.values() if row["action"].get("cards") == ["Heart2"])
    assert two["splits_same_rank_groups"] == [{"rank": 15, "before": 2, "used": 1}]
    assert two["strongest_same_shape_reply"] and not six["strongest_same_shape_reply"]
    assert facts["pass"]["remaining_count"] == 3


def test_own_last_single_can_finish_and_no_reply_keeps_only_pass():
    payload, facts = payload_for(reply_game(["HeartK"]))
    finish = next(row for row in facts.values() if row["action"]["action"] == "play")
    assert finish["wins_immediately"]
    assert finish["remaining_count"] == finish["estimated_remaining_plays"] == 0
    assert finish["remaining_hand"] == []
    _, facts = payload_for(reply_game(["Heart3"]))
    assert list(facts) == ["pass"]
    assert not facts["pass"]["wins_immediately"]


def test_teammate_lead_and_enemy_last_single_allow_splitting_pair_to_block():
    payload, facts = payload_for(reply_game(leader="c"))
    assert payload["state"]["current_decision"]["last_play_relation"] == "teammate"
    assert "pass" in facts
    game = reply_game(["Heart3", "HeartA", "SpadeA"], enemy_cards=["Spade4", "Club9"])
    payload, facts = payload_for(game)
    assert payload["state"]["current_decision"]["enemy_last_single"]
    ace = next(row for row in facts.values() if row["action"]["action"] == "play")
    assert ace["strongest_same_shape_reply"]
    assert ace["splits_same_rank_groups"] == [{"rank": 14, "before": 2, "used": 1}]


def test_landlord_and_bidding_have_correct_roles_without_invented_teammate(monkeypatch):
    game = traditional.LandlordGame(["a", "b", "c"], seed=18)
    with monkeypatch.context() as patch:
        def unexpected_planner(*args):
            raise AssertionError("叫分阶段无需建立出牌规划器")
        patch.setattr(traditional.LandlordGame, "_hand_planner", unexpected_planner)
        payload, facts = payload_for(game)
    focus = payload["state"]["current_decision"]
    assert focus["phase"] == "bidding" and focus["your_role"] == "unknown"
    assert focus["teammate"] is None and focus["opponents"] == []
    assert all(row["action"]["action"] == "bid" for row in facts.values())
    game.act("a", "bid", bid=3)
    payload, _ = payload_for(game)
    focus = payload["state"]["current_decision"]
    assert focus["your_role"] == "landlord" and focus["teammate"] is None
    assert focus["opponents"] == [{"seat": "seat_2", "remaining_count": 17},
                                 {"seat": "seat_3", "remaining_count": 17}]
    assert focus["last_play_relation"] == "free_lead"


def test_changing_opponent_hidden_cards_cannot_change_decision_summary():
    game = reply_game()
    original, _ = payload_for(game)
    game.hands["a"] = ["DiamondA", "DiamondK", "DiamondQ", "DiamondJ", "Diamond10"]
    game.hands["c"] = ["Spade5", "Spade7", "Spade8", "Spade9"]
    changed, _ = payload_for(game)
    assert changed == original


@pytest.mark.asyncio
async def test_real_client_keeps_history_and_executes_provider_choice_with_enriched_criteria(monkeypatch):
    game = reply_game()
    context = llm.build_table_context("landlord", game, "b")
    context["_conversation"] = [{"role": "assistant", "content": '{"action":"pass"}'}]
    context["public_action_history"] = [{"seat": "seat_1", "action": "play", "cards": ["Spade4"]}]
    original = httpx.AsyncClient
    monkeypatch.setenv("GAME_LLM_ENABLED", "true")
    monkeypatch.setenv("GAME_LLM_BASE_URL", "https://bufan.test/v1")
    monkeypatch.setenv("GAME_LLM_API_KEY", "SYNTHETIC-KEY")
    monkeypatch.setenv("GAME_LLM_MODEL", "jev")

    def handler(request):
        payload = json.loads(request.content)
        assert request.url.path == "/v1/evaluations"
        assert payload["state"]["conversation"] == context["_conversation"]
        assert payload["state"]["current"]["public_action_history"] == context["public_action_history"]
        assert "_seat_ids" not in json.dumps(payload)
        assert "max_tokens" not in payload
        facts = {name: json.loads(value) for name, value in payload["questions"]["action"]["criteria"].items()}
        choice = next(name for name, row in facts.items() if row["action"].get("cards") == ["Diamond6"])
        # 仍严格执行供应商 choice，不能擅自拿更高概率的 pass 替换。
        probabilities = {name: 0.0 for name in facts}
        probabilities.update({choice: 0.4, "pass": 0.6})
        return httpx.Response(200, json={"answers": {"action": {
            "type": "choice", "choice": choice, "probabilities": probabilities}}})

    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    action = await llm.GameLLMClient().choose_action(context)
    assert action == {"action": "play", "cards": ["Diamond6"]}
    game.act("b", **action)
    assert game.hands["b"] == ["Heart2", "Spade2"]


def test_texas_payload_still_uses_original_action_criteria():
    game = poker.TexasHoldemGame(["a", "b"], seed=42)
    current = llm.build_table_context("texas", game, game.current_player_id)
    candidates = jev.build_action_candidates(current)
    payload = jev.evaluation_payload("jev", current, [], candidates)
    assert list(payload["state"]) == ["current", "conversation"]
    assert {name: json.loads(value) for name, value in payload["questions"]["action"]["criteria"].items()} == candidates
