# -*- coding: utf-8 -*-

import importlib
from unittest.mock import patch

import pytest


multiplayer = importlib.import_module(
    "src.chat.features.games.blackjack-web.multiplayer_service"
)


@pytest.fixture
def service():
    return multiplayer.MultiplayerBlackjackService()


def make_room(service, user_ids=(10, 20, 30), bet=100):
    room_id = service.create_room(user_ids[0], "玩家一", "")["room_id"]
    for user_id in user_ids[1:]:
        service.join_room(room_id, user_id, f"玩家{user_id}", "")
    for user_id in user_ids:
        service.set_bet(room_id, user_id, bet)
        service.set_ready(room_id, user_id, True)
    return room_id


def start_with_cards(service, room_id, cards, host=10):
    # 固定抽牌顺序，覆盖可重现的多人完整回合。
    with patch.object(multiplayer, "_create_deck", return_value=list(reversed(cards))):
        with patch.object(multiplayer.random, "shuffle"):
            return service.start_round(room_id, host)


def start_three_player_round(service, room_id):
    return start_with_cards(
        service,
        room_id,
        [
            "Club10", "Diamond7",  # 庄家 17 点
            "Heart10", "Spade8",  # 玩家一 18 点
            "Diamond10", "Club8",  # 玩家二 18 点
            "Spade10", "Heart8",  # 玩家三 18 点
        ],
    )


def player_state(state, user_id):
    return next(player for player in state["players"] if int(player["user_id"]) == user_id)


def commit_settlement(service, room_id):
    settlement = service.settle_if_finished(room_id)
    for user_id in settlement["payouts"]:
        service.mark_payout_committed(room_id, user_id)
    service.mark_round_committed(room_id)


def test_leaving_an_earlier_seat_keeps_current_player_turn(service):
    room_id = make_room(service)
    start_three_player_round(service, room_id)
    service.stand(room_id, 10)

    state = service.leave_room(room_id, 10)

    assert int(state["current_turn_user_id"]) == 20
    assert int(state["host_user_id"]) == 20
    state = service.stand(room_id, 20)
    assert int(state["current_turn_user_id"]) == 30
    state = service.stand(room_id, 30)
    assert state["state"] == "finished"


def test_current_player_leaving_advances_to_next_unfinished_player(service):
    room_id = make_room(service)
    start_three_player_round(service, room_id)
    service.stand(room_id, 10)

    state = service.leave_room(room_id, 20)

    assert int(state["current_turn_user_id"]) == 30
    state = service.stand(room_id, 30)
    assert state["state"] == "finished"
    assert player_state(state, 10)["result"] == "win"


def test_last_active_player_leaving_settles_remaining_players(service):
    room_id = make_room(service, user_ids=(10, 20))
    start_with_cards(
        service, room_id,
        ["Club10", "Diamond7", "Heart10", "Spade8", "Diamond10", "Club8"],
    )
    service.stand(room_id, 10)

    state = service.leave_room(room_id, 20)

    assert state["state"] == "finished"
    assert state["current_turn_user_id"] is None
    assert player_state(state, 10)["payout_amount"] == 200
    settlement = service.settle_if_finished(room_id)
    assert settlement["bet_total"] == 200
    assert settlement["payout_total"] == 200
    assert settlement["payouts"] == {10: 200}
    assert service.get_round_bet_total(room_id) == 200


def test_future_player_leaving_keeps_current_turn(service):
    room_id = make_room(service)
    start_three_player_round(service, room_id)

    state = service.leave_room(room_id, 30)

    assert int(state["current_turn_user_id"]) == 10
    state = service.stand(room_id, 10)
    assert int(state["current_turn_user_id"]) == 20
    assert service.stand(room_id, 20)["state"] == "finished"


def test_dealer_blackjack_ends_round_and_pushes_player_blackjack(service):
    room_id = make_room(service, user_ids=(10, 20))

    state = start_with_cards(
        service, room_id,
        ["ClubA", "DiamondK", "HeartA", "SpadeQ", "Diamond10", "Club8"],
    )

    assert state["state"] == "finished"
    assert state["current_turn_user_id"] is None
    assert player_state(state, 10)["result"] == "push"
    assert player_state(state, 10)["payout_amount"] == 100
    assert player_state(state, 20)["result"] == "loss"
    assert player_state(state, 20)["payout_amount"] == 0
    assert state["dealer"]["hand"] == ["ClubA", "DiamondK"]


def test_player_blackjack_beats_dealer_three_card_twenty_one(service):
    room_id = make_room(service, user_ids=(10,), bet=101)

    state = start_with_cards(
        service, room_id,
        ["Club10", "Diamond6", "HeartA", "SpadeQ", "Heart5"],
    )

    assert state["state"] == "finished"
    assert state["dealer"]["score"] == 21
    assert player_state(state, 10)["result"] == "blackjack"
    assert player_state(state, 10)["payout_amount"] == 252


def test_bust_advances_turn_and_cannot_receive_a_payout(service):
    room_id = make_room(service, user_ids=(10, 20))
    start_with_cards(
        service, room_id,
        ["Club10", "Diamond7", "Heart10", "Spade8", "Diamond10", "Club8", "HeartK"],
    )

    state = service.hit(room_id, 10)
    assert player_state(state, 10)["status"] == "bust"
    assert int(state["current_turn_user_id"]) == 20
    with pytest.raises(ValueError, match="还没轮到"):
        service.hit(room_id, 10)
    state = service.stand(room_id, 20)
    assert player_state(state, 10)["payout_amount"] == 0
    assert player_state(state, 20)["payout_amount"] == 200


def test_finished_round_cannot_reuse_settled_bet_for_ready(service):
    room_id = make_room(service, user_ids=(10,))
    state = start_with_cards(
        service, room_id, ["ClubA", "DiamondK", "HeartA", "SpadeQ"],
    )

    with pytest.raises(ValueError, match="先下注"):
        service.set_ready(room_id, 10, True)
    assert service.get_room_state(room_id) == state


def test_failed_start_after_round_does_not_erase_results(service):
    room_id = make_room(service, user_ids=(10,))
    state = start_with_cards(
        service, room_id, ["ClubA", "DiamondK", "HeartA", "SpadeQ"],
    )

    with pytest.raises(ValueError, match="新一局下注"):
        service.start_round(room_id, 10)
    assert service.get_room_state(room_id) == state


def test_continue_without_previous_bet_does_not_erase_others_results(service):
    room_id = make_room(service, user_ids=(10,))
    start_with_cards(
        service, room_id, ["ClubA", "DiamondK", "HeartA", "SpadeQ"],
    )
    state = service.join_room(room_id, 20, "新玩家", "")

    with pytest.raises(ValueError, match="上一局没有有效下注"):
        service.continue_ready(room_id, 20)
    assert service.get_room_state(room_id) == state


def test_next_round_resets_all_bets_and_requires_each_player_ready(service):
    room_id = make_room(service, user_ids=(10, 20))
    start_with_cards(
        service, room_id,
        ["ClubA", "DiamondK", "HeartA", "SpadeQ", "Diamond10", "Club8"],
    )
    commit_settlement(service, room_id)

    state = service.continue_ready(room_id, 10)

    assert state["state"] == "waiting"
    assert player_state(state, 10)["bet_amount"] == 100
    assert player_state(state, 10)["is_ready"] is True
    assert player_state(state, 20)["bet_amount"] == 0
    assert player_state(state, 20)["last_bet_amount"] == 100
    assert player_state(state, 20)["is_ready"] is False
    with pytest.raises(ValueError, match="尚未完成下注并准备"):
        service.start_round(room_id, 10)
    service.continue_ready(room_id, 20)
    state = start_with_cards(
        service, room_id,
        ["Club10", "Diamond7", "Heart10", "Spade8", "Diamond10", "Club8"],
    )
    assert state["state"] == "playing"


def test_finished_settlement_remains_pending_until_payments_are_confirmed(service):
    room_id = make_room(service, user_ids=(10,))
    start_with_cards(
        service, room_id, ["ClubA", "DiamondK", "HeartA", "SpadeQ"],
    )

    settlement = service.settle_if_finished(room_id)
    assert settlement["committed"] is True
    assert settlement["payouts"] == {10: 100}
    assert service.settle_if_finished(room_id)["payouts"] == {10: 100}
    service.mark_payout_committed(room_id, 10)
    assert service.settle_if_finished(room_id)["payouts"] == {}
    service.mark_round_committed(room_id)
    second_settlement = service.settle_if_finished(room_id)
    assert second_settlement["committed"] is False
    assert second_settlement["payouts"] == {}


def test_returned_hands_are_snapshots(service):
    room_id = make_room(service, user_ids=(10,))
    state = start_with_cards(
        service, room_id, ["ClubA", "DiamondK", "HeartA", "SpadeQ"],
    )
    state["players"][0]["hand"].clear()
    state["dealer"]["hand"].clear()

    state = service.get_room_state(room_id)

    assert player_state(state, 10)["hand"] == ["HeartA", "SpadeQ"]
    assert state["dealer"]["hand"] == ["ClubA", "DiamondK"]


def test_last_player_leaving_closes_room(service):
    room_id = make_room(service, user_ids=(10,))

    state = service.leave_room(room_id, 10)

    assert state == {"room_closed": True, "room_id": room_id}
    with pytest.raises(ValueError, match="不存在或已关闭"):
        service.get_room_state(room_id)


def test_partial_settlement_only_returns_unpaid_players(service):
    room_id = make_room(service, user_ids=(10, 20))
    start_with_cards(
        service, room_id,
        ["Club10", "Diamond7", "Heart10", "Spade8", "Diamond10", "Club8"],
    )
    service.stand(room_id, 10)
    service.stand(room_id, 20)

    service.mark_payout_committed(room_id, 10)

    assert service.settle_if_finished(room_id)["payouts"] == {20: 200}
    with pytest.raises(ValueError, match="尚有玩家派彩未完成"):
        service.mark_round_committed(room_id)
    with pytest.raises(ValueError, match="上一局仍在结算"):
        service.set_bet(room_id, 10, 50)
    with pytest.raises(ValueError, match="上一局仍在结算"):
        service.continue_ready(room_id, 10)
    service.mark_payout_committed(room_id, 20)
    service.mark_round_committed(room_id)
    state = service.set_bet(room_id, 10, 50)
    assert state["state"] == "waiting"


def test_large_discord_user_ids_are_returned_as_exact_strings(service):
    user_id = 123456789012345679
    room_id = make_room(service, user_ids=(user_id,))

    state = start_with_cards(
        service, room_id,
        ["Club10", "Diamond7", "Heart10", "Spade8"], host=user_id,
    )

    assert state["host_user_id"] == "123456789012345679"
    assert state["current_turn_user_id"] == "123456789012345679"
    assert state["players"][0]["user_id"] == "123456789012345679"


def test_expired_turn_automatically_stands_and_gives_next_player_full_time(service):
    room_id = make_room(service)
    with patch.object(multiplayer.time, "time", return_value=1000):
        state = start_three_player_round(service, room_id)
    assert state["turn_deadline"] == 1060
    assert state["turn_timeout_seconds"] == 60

    with patch.object(multiplayer.time, "time", return_value=1061):
        state = service.get_room_state(room_id)

    assert player_state(state, 10)["status"] == "stood"
    assert state["current_turn_user_id"] == "20"
    assert state["turn_deadline"] == 1121
    with patch.object(multiplayer.time, "time", return_value=1062):
        with pytest.raises(ValueError, match="还没轮到"):
            service.hit(room_id, 10)


def test_last_player_timeout_finishes_the_round(service):
    room_id = make_room(service, user_ids=(10,))
    with patch.object(multiplayer.time, "time", return_value=1000):
        start_with_cards(
            service, room_id,
            ["Club10", "Diamond7", "Heart10", "Spade8"],
        )
    with patch.object(multiplayer.time, "time", return_value=1060):
        state = service.get_room_state(room_id)

    assert state["state"] == "finished"
    assert state["turn_deadline"] is None
    assert player_state(state, 10)["payout_amount"] == 200


def test_unrelated_player_leaving_does_not_extend_current_turn(service):
    room_id = make_room(service)
    with patch.object(multiplayer.time, "time", return_value=1000):
        start_three_player_round(service, room_id)
    with patch.object(multiplayer.time, "time", return_value=1015):
        state = service.leave_room(room_id, 30)

    assert state["current_turn_user_id"] == "10"
    assert state["turn_deadline"] == 1060


def bot_room(service, include_guest=False):
    room_id = service.create_room(10, "房主", "")["room_id"]
    service.configure_bot(room_id, 10, True)
    if include_guest:
        service.join_room(room_id, 20, "另一玩家", "")
        service.set_bet(room_id, 20, 80)
        service.set_ready(room_id, 20, True)
    service.set_bet(room_id, 10, 100)
    service.set_ready(room_id, 10, True)
    return room_id


def test_configure_bot_requires_host_and_available_seat(service):
    room_id = service.create_room(10, "房主", "")["room_id"]
    service.join_room(room_id, 20, "另一玩家", "")
    with pytest.raises(ValueError, match="只有房主"):
        service.configure_bot(room_id, 20, True)
    with pytest.raises(ValueError, match="布尔值"):
        service.configure_bot(room_id, 10, 1)
    state = service.configure_bot(room_id, 10, True)
    assert state["include_yueyue"] is True
    bot = player_state(state, -1)
    assert bot["is_bot"] is True
    assert bot["is_ready"] is True
    assert bot["username"] == "月月（陪玩）"
    assert bot["avatar_url"] == "/character/normal.webp"
    assert player_state(state, 10)["is_bot"] is False
    assert len(service.configure_bot(room_id, 10, True)["players"]) == 3
    with pytest.raises(ValueError, match="人数已满"):
        service.join_room(room_id, 30, "第三真人", "")
    state = service.configure_bot(room_id, 10, False)
    assert state["include_yueyue"] is False
    service.join_room(room_id, 30, "第三真人", "")
    with pytest.raises(ValueError, match="人数已满"):
        service.configure_bot(room_id, 10, True)


def test_reserved_bot_identity_cannot_create_or_join_as_human(service):
    with pytest.raises(ValueError, match="保留"):
        service.create_room(-1, "冒充月月", "")
    room_id = service.create_room(10, "房主", "")["room_id"]
    with pytest.raises(ValueError, match="保留"):
        service.join_room(room_id, -1, "冒充月月", "")


def test_bot_bet_tracks_host_and_is_automatically_ready(service):
    room_id = bot_room(service)
    state = service.set_bet(room_id, 10, 125)
    assert player_state(state, -1)["bet_amount"] == 125
    assert player_state(state, -1)["last_bet_amount"] == 125
    assert player_state(state, -1)["is_ready"] is True
    assert state["all_players_ready"] is False
    state = service.set_ready(room_id, 10, True)
    assert state["all_players_ready"] is True
    with pytest.raises(ValueError, match="自动"):
        service.set_bet(room_id, -1, 300)
    with pytest.raises(ValueError, match="自动"):
        service.set_ready(room_id, -1, False)


def test_bot_hits_to_seventeen_and_hands_turn_back_to_human(service):
    room_id = bot_room(service, include_guest=True)
    state = start_with_cards(service, room_id, [
        "Club10", "Diamond7",  # 荷官 17
        "Heart10", "Spade8",  # 房主 18
        "Club5", "Diamond6",  # 月月 11
        "Heart9", "Spade9",  # 真人 18
        "Club6",              # 月月补到 17
    ])
    assert state["current_turn_user_id"] == "10"
    state = service.stand(room_id, 10)
    assert state["current_turn_user_id"] == "20"
    assert player_state(state, -1)["hand"] == ["Club5", "Diamond6", "Club6"]
    assert player_state(state, -1)["status"] == "stood"
    assert service.stand(room_id, 20)["state"] == "finished"


def test_bot_runs_immediately_when_human_has_natural_blackjack(service):
    room_id = bot_room(service)
    state = start_with_cards(service, room_id, [
        "Club10", "Diamond7", "HeartA", "SpadeK", "Club5", "Diamond6", "Club6",
    ])
    assert state["state"] == "finished"
    assert player_state(state, -1)["score"] == 17
    assert player_state(state, 10)["payout_amount"] == 250


def test_bot_win_is_excluded_from_payouts_and_house_totals(service):
    room_id = bot_room(service)
    start_with_cards(service, room_id, [
        "Club10", "Diamond7", "Heart10", "Spade8", "ClubK", "Diamond9",
    ])
    state = service.stand(room_id, 10)
    assert player_state(state, -1)["payout_amount"] == 200
    settlement = service.settle_if_finished(room_id)
    assert settlement["payouts"] == {10: 200}
    assert settlement["bet_total"] == 100
    assert settlement["payout_total"] == 200
    assert service.get_round_bet_total(room_id) == 100
    assert service.get_round_payout_total(room_id) == 200
    with pytest.raises(ValueError, match="没有待确认"):
        service.mark_payout_committed(room_id, -1)
    service.mark_payout_committed(room_id, 10)
    service.mark_round_committed(room_id)
    state = service.continue_ready(room_id, 10)
    assert player_state(state, -1)["bet_amount"] == 100
    assert state["all_players_ready"] is True


def test_configure_bot_rejects_playing_or_unsettled_round(service):
    room_id = bot_room(service)
    start_with_cards(service, room_id, [
        "Club10", "Diamond7", "Heart10", "Spade8", "ClubK", "Diamond9",
    ])
    with pytest.raises(ValueError, match="进行中"):
        service.configure_bot(room_id, 10, False)
    service.stand(room_id, 10)
    with pytest.raises(ValueError, match="仍在结算"):
        service.configure_bot(room_id, 10, False)
    commit_settlement(service, room_id)
    state = service.configure_bot(room_id, 10, False)
    assert state["include_yueyue"] is False


def test_host_transfer_skips_bot_and_waiting_bet_tracks_new_host(service):
    room_id = bot_room(service, include_guest=True)
    state = service.leave_room(room_id, 10)
    assert state["host_user_id"] == "20"
    assert player_state(state, -1)["bet_amount"] == 80
    assert player_state(state, -1)["is_ready"] is True


@pytest.mark.parametrize("playing", [False, True])
def test_last_human_leaving_closes_room_with_bot(service, playing):
    room_id = bot_room(service)
    if playing:
        start_with_cards(service, room_id, [
            "Club10", "Diamond7", "Heart10", "Spade8", "ClubK", "Diamond9",
        ])
    assert service.leave_room(room_id, 10) == {"room_closed": True, "room_id": room_id}
    with pytest.raises(ValueError, match="不存在或已关闭"):
        service.get_room_state(room_id)


def test_leaving_current_human_immediately_drives_bot_before_next_human(service):
    room_id = bot_room(service, include_guest=True)
    start_with_cards(service, room_id, [
        "Club10", "Diamond7", "Heart10", "Spade8", "Club5", "Diamond6",
        "Heart9", "Spade9", "Club6",
    ])
    state = service.leave_room(room_id, 10)
    assert state["host_user_id"] == "20"
    assert state["current_turn_user_id"] == "20"
    assert player_state(state, -1)["status"] == "stood"
    assert service.stand(room_id, 20)["state"] == "finished"
    assert service.settle_if_finished(room_id)["bet_total"] == 180


def test_human_bust_immediately_drives_bot_to_completion(service):
    room_id = bot_room(service)
    start_with_cards(service, room_id, [
        "Club10", "Diamond7", "Heart10", "Spade8", "Club5", "Diamond6", "ClubK", "Club6",
    ])
    state = service.hit(room_id, 10)
    assert state["state"] == "finished"
    assert player_state(state, 10)["result"] == "loss"
    assert player_state(state, -1)["score"] == 17


def test_human_timeout_poll_drives_bot_without_another_timeout(service):
    room_id = bot_room(service)
    with patch.object(multiplayer.time, "time", return_value=1000):
        start_with_cards(service, room_id, [
            "Club10", "Diamond7", "Heart10", "Spade8", "Club5", "Diamond6", "Club6",
        ])
    with patch.object(multiplayer.time, "time", return_value=1060):
        state = service.get_room_state(room_id)
    assert state["state"] == "finished"
    assert player_state(state, -1)["score"] == 17
