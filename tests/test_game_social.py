"""固定快捷消息的权限、限频、房间实例隔离与事件游标。"""

import importlib
import pytest


module = importlib.import_module("src.chat.features.games.blackjack-web.game_social")
MEMBERS = [{"user_id": "1", "username": "甲", "is_bot": False},
           {"user_id": "2", "username": "乙", "is_bot": False},
           {"user_id": "bot:yueyue", "username": "月月", "is_bot": True}]


@pytest.fixture
def setup():
    now = [100.0]
    return module.GameSocialService(clock=lambda: now[0], wall_clock=lambda: 1700000000 + now[0]), now


def test_catalog_fixed_and_returned_data_not_mutable(setup):
    service, _ = setup
    catalog = service.catalog()
    assert len(catalog["chat"]) == 11
    assert {"id": "mm_or_gg", "text": "你是MM还是GG？"} in catalog["chat"]
    assert {item["id"] for item in catalog["interaction"]} == {"tea", "flower", "incense"}
    catalog["chat"][0]["text"] = "篡改"
    assert service.catalog()["chat"][0]["text"] != "篡改"


@pytest.mark.parametrize("user_id", ["outsider", "bot:yueyue"])
def test_only_human_room_members_can_send_or_read(setup, user_id):
    service, _ = setup
    with pytest.raises(PermissionError):
        service.send("room", user_id, MEMBERS, "chat", "hello")
    with pytest.raises(PermissionError):
        service.recent("room", user_id, MEMBERS)
    assert not service._rooms


@pytest.mark.parametrize("kind,item,target", [
    ("chat", "任意文本", None), ("chat", "hello", "2"), ("interaction", "tea", "1"),
    ("interaction", "tea", "other"), ("interaction", "unknown", "2"),
    ("interaction", "tea", None), ("other", "hello", None), ([], "hello", None),
])
def test_invalid_operations_do_not_create_events_or_charge_limits(setup, kind, item, target):
    service, _ = setup
    with pytest.raises(ValueError):
        service.send("r", "1", MEMBERS, kind, item, target)
    assert service.send("r", "1", MEMBERS, "chat", "hello")["event_id"] == 1


def test_join_cursor_does_not_replay_old_audio_and_incremental_is_copied(setup):
    service, now = setup
    service.send("r", "1", MEMBERS, "chat", "hello")
    initial = service.recent("r", "2", MEMBERS)
    assert initial == {"events": [], "cursor": 1}
    now[0] += 2
    event = service.send("r", "1", MEMBERS, "interaction", "tea", "bot:yueyue")
    assert event["target_username"] == "月月" and event["username"] == "甲"
    event["text"] = "篡改"
    delta = service.recent("r", "2", MEMBERS, initial["cursor"])
    assert delta["events"][0]["text"] == "倒茶"
    delta["events"][0]["text"] = "篡改2"
    assert service.recent("r", "2", MEMBERS, 1)["events"][0]["text"] == "倒茶"
    assert service.recent("r", "2", MEMBERS, delta["cursor"])["events"] == []


def test_per_user_two_seconds_and_twelve_per_minute(setup):
    service, now = setup
    service.send("r", "1", MEMBERS, "chat", "hello")
    now[0] += 1
    with pytest.raises(module.SocialRateLimitError) as error:
        service.send("r", "1", MEMBERS, "chat", "hello")
    assert error.value.retry_after == 1
    service.send("r", "2", MEMBERS, "chat", "hello")
    now[0] += 1
    for _ in range(11):
        service.send("r", "1", MEMBERS, "chat", "hello")
        now[0] += 2
    with pytest.raises(module.SocialRateLimitError) as error:
        service.send("r", "1", MEMBERS, "chat", "hello")
    assert error.value.retry_after == 36
    now[0] = 160
    service.send("r", "1", MEMBERS, "chat", "again")


def test_room_instances_and_cursors_are_isolated(setup):
    service, _ = setup
    first, second = ("ROOM", 1), ("ROOM", 2)
    event1 = service.send(first, "1", MEMBERS, "chat", "hello")
    assert service.recent(second, "1", MEMBERS, 0)["events"] == []
    event2 = service.send(second, "1", MEMBERS, "chat", "again")
    assert event2["event_id"] > event1["event_id"]
    assert service.recent(first, "1", MEMBERS, 0)["events"] == [event1]


def test_retention_trims_events_and_expires_idle_rooms(setup):
    service, now = setup
    for _ in range(105):
        service.send("r", "1", MEMBERS, "chat", "hello")
        now[0] += 6
    events = service.recent("r", "1", MEMBERS, 0)["events"]
    assert len(events) == 100 and events[0]["event_id"] == 6
    now[0] += service.IDLE_TTL_SECONDS
    service.cleanup()
    assert not service._rooms
    assert service.recent("r", "1", MEMBERS, 0)["events"] == []


def test_memory_has_scope_limit_and_retains_recently_used_scope(setup):
    service, now = setup
    service.MAX_SCOPES = 2
    service.recent("a", "1", MEMBERS)
    now[0] += 1
    service.recent("b", "1", MEMBERS)
    now[0] += 1
    service.recent("a", "1", MEMBERS)
    now[0] += 1
    service.recent("c", "1", MEMBERS)
    assert set(service._rooms) == {"a", "c"}


@pytest.mark.parametrize("cursor", [-1, True, 1.5, "1"])
def test_cursor_is_strict_nonnegative_integer(setup, cursor):
    service, _ = setup
    with pytest.raises(ValueError):
        service.recent("r", "1", MEMBERS, cursor)
