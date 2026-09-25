"""用真实 SQLite 验证灵圃成长、拜访权限、交易幂等及跨业务并发扣款。"""

import asyncio
import importlib
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest


module = importlib.import_module("src.chat.features.games.blackjack-web.farm_service")
catalog = importlib.import_module("src.chat.features.games.blackjack-web.farm_catalog")
wallet_module = importlib.import_module("src.chat.features.games.blackjack-web.table_wallet")
OWNER = {"user_id": "1", "username": "种植者", "avatar_url": "/owner.webp"}
VISITOR = {"user_id": "2", "username": "访客", "avatar_url": "/visitor.webp"}


class Clock:
    value = 1_000_000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FixedRandom:
    def __init__(self, *values):
        self.values = iter(values)

    def random(self):
        return next(self.values, 0.9)


@pytest.fixture
def farm(tmp_path):
    path = tmp_path / "farm.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE user_coins (user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL);
            CREATE TABLE coin_transactions (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, reason TEXT NOT NULL);
            INSERT INTO user_coins VALUES (1, 1000), (2, 1000), (3, 1000);
        """)
    clock = Clock()
    service = module.FarmService(path, clock=clock, rng=FixedRandom())
    return SimpleNamespace(service=service, clock=clock, path=path)


async def act(farm, action, profile=OWNER, **kwargs):
    return await farm.service.act(profile, action, request_id=kwargs.pop("request_id", uuid4().hex), **kwargs)


def ledger(path, uid=1):
    with sqlite3.connect(path) as connection:
        return (connection.execute("SELECT balance FROM user_coins WHERE user_id=?", (uid,)).fetchone()[0],
                connection.execute("SELECT coalesce(sum(amount),0) FROM coin_transactions WHERE user_id=?", (uid,)).fetchone()[0])


def edit_state(farm, edit, uid=1):
    with sqlite3.connect(farm.path) as connection:
        state = json.loads(connection.execute("SELECT state FROM farm_profiles WHERE user_id=?", (uid,)).fetchone()[0])
        edit(state)
        connection.execute("UPDATE farm_profiles SET state=? WHERE user_id=?", (json.dumps(state), uid))


@pytest.mark.asyncio
async def test_new_farm_starter_gift_is_once_and_wallet_is_not_funded(farm):
    first = await farm.service.get_farm(OWNER)
    second = await farm.service.get_farm(OWNER)
    assert first["plots"] == second["plots"]
    assert second["farm"]["level"] == 1
    assert second["inventory"]["seeds"] == [{"crop_id": "huangjing", "quantity": 6}]
    assert second["balance"] == 1000
    stranger = {"user_id": "99", "username": "新玩家"}
    assert (await farm.service.get_farm(stranger))["balance"] == 0
    assert ledger(farm.path) == (1000, 0)


@pytest.mark.asyncio
async def test_full_plant_water_harvest_sell_loop_and_offline_persistence(farm):
    planted = await act(farm, "plant", plot_id=1, crop_id="huangjing")
    assert planted["plots"][0]["quality"] is None
    assert planted["plots"][0]["status"] == "growing"
    original_due = planted["plots"][0]["mature_at"]
    watered = await act(farm, "water", plot_id=1)
    assert watered["plots"][0]["mature_at"] == original_due - 180
    with pytest.raises(module.FarmError, match="已.*浇"):
        await act(farm, "water", plot_id=1)
    with pytest.raises(module.FarmError, match="尚未成熟"):
        await act(farm, "harvest", plot_id=1)
    farm.clock.advance(1620)
    farm.service = module.FarmService(farm.path, clock=farm.clock, rng=FixedRandom())
    ready = await farm.service.get_farm(OWNER)
    assert ready["plots"][0]["status"] == "mature"
    harvested = await act(farm, "harvest", plot_id=1)
    assert harvested["plots"][0]["status"] == "empty"
    assert harvested["farm"]["xp"] == 12
    assert harvested["inventory"]["produce"] == [{"crop_id": "huangjing", "quality": "normal", "quantity": 4, "unit_price": 10}]
    sold = await act(farm, "sell", crop_id="huangjing", quantity=4)
    assert sold["balance"] == 1040
    assert sold["inventory"]["produce"] == []
    assert ledger(farm.path) == (1040, 40)
    with pytest.raises(module.FarmError, match="数量不足"):
        await act(farm, "sell", crop_id="huangjing", quantity=4)


@pytest.mark.asyncio
@pytest.mark.parametrize("clear,expected", [(False, 3), (True, 4)])
async def test_pests_appear_later_and_care_restores_yield(farm, clear, expected):
    farm.service.rng = FixedRandom(0.9, 0.1)
    planted = await act(farm, "plant", plot_id=1, crop_id="huangjing")
    assert planted["plots"][0]["has_pest"] is False
    with pytest.raises(module.FarmError, match="没有虫害"):
        await act(farm, "pest", plot_id=1)
    farm.clock.advance(640)
    assert (await farm.service.get_farm(OWNER))["plots"][0]["has_pest"] is True
    if clear:
        cleaned = await act(farm, "pest", plot_id=1)
        assert cleaned["plots"][0]["has_pest"] is False
    farm.clock.advance(1200)
    assert (await act(farm, "harvest", plot_id=1))["result"]["quantity"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("roll,quality,multiple", [(0.002, "celestial", 5), (0.025, "spirit", 2), (0.8, "normal", 1)])
async def test_mutation_hidden_until_mature_and_sale_uses_server_price(farm, roll, quality, multiple):
    farm.service.rng = FixedRandom(roll, 0.9)
    planted = await act(farm, "plant", plot_id=1, crop_id="huangjing")
    assert planted["plots"][0]["quality"] is None
    assert "harvest_id" not in json.dumps(planted)
    farm.clock.advance(1800)
    mature = await farm.service.get_farm(OWNER)
    assert mature["plots"][0]["quality"] == quality
    await act(farm, "harvest", plot_id=1)
    result = await act(farm, "sell", crop_id="huangjing", quality=quality, quantity=4)
    assert result["result"]["coins"] == 40 * multiple


@pytest.mark.asyncio
async def test_exact_retries_do_not_repeat_purchases_and_payload_reuse_fails(farm):
    first = await act(farm, "buy_seed", crop_id="qixing", quantity=2, request_id="request-1")
    repeat = await act(farm, "buy_seed", crop_id="qixing", quantity=2, request_id="request-1")
    assert first["balance"] == repeat["balance"] == 976
    assert repeat["result"]["replayed"] is True
    assert ledger(farm.path) == (976, -24)
    with pytest.raises(module.FarmError) as error:
        await act(farm, "buy_seed", crop_id="qixing", quantity=3, request_id="request-1")
    assert error.value.status == 409


@pytest.mark.asyncio
async def test_concurrent_retries_harvest_only_once(farm):
    await act(farm, "plant", plot_id=1, crop_id="huangjing")
    farm.clock.advance(1800)
    results = await asyncio.gather(*(act(farm, "harvest", plot_id=1, request_id="same-harvest") for _ in range(8)))
    assert sum(bool(result["result"].get("replayed")) for result in results) == 7
    result = await farm.service.get_farm(OWNER)
    assert result["farm"]["xp"] == 12
    assert result["inventory"]["produce"][0]["quantity"] == 4


@pytest.mark.asyncio
async def test_concurrent_wallet_spending_cannot_overdraw(farm):
    with sqlite3.connect(farm.path) as connection:
        connection.execute("UPDATE user_coins SET balance=24 WHERE user_id=1")
    wallet = wallet_module.TableWallet(str(farm.path))
    results = await asyncio.gather(act(farm, "buy_seed", crop_id="huangjing"), wallet.reserve("farm-competition", ["1"], 24), return_exceptions=True)
    assert sum(isinstance(result, Exception) for result in results) == 1
    assert ledger(farm.path) == (0, -24)


@pytest.mark.asyncio
async def test_ledger_failure_rolls_back_state_money_and_receipt(farm):
    before = await farm.service.get_farm(OWNER)
    with sqlite3.connect(farm.path) as connection:
        connection.execute("CREATE TRIGGER fail_coin BEFORE INSERT ON coin_transactions BEGIN SELECT RAISE(ABORT,'ledger failed'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await act(farm, "buy_seed", crop_id="huangjing", request_id="rollback-purchase")
    after = await farm.service.get_farm(OWNER)
    assert before["inventory"] == after["inventory"]
    assert ledger(farm.path) == (1000, 0)
    with sqlite3.connect(farm.path) as connection:
        assert connection.execute("SELECT count(*) FROM farm_actions").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_visitor_hides_wallet_inventory_and_cannot_change_owner(farm):
    await act(farm, "plant", plot_id=1, crop_id="huangjing")
    visiting = await farm.service.get_farm(VISITOR, 1)
    assert visiting["is_owner"] is False
    assert visiting["balance"] is None
    assert visiting["inventory"] == {"seeds": [], "produce": [], "items": []}
    assert visiting["farm"]["stats"] is None
    with pytest.raises(module.FarmError, match="自己"):
        await act(farm, "harvest", profile=VISITOR, target_user_id=1, plot_id=1)
    with pytest.raises(module.FarmError) as error:
        await farm.service.get_farm(VISITOR, 9)
    assert error.value.status == 404
    entries = (await farm.service.list_farms(VISITOR))["entries"]
    assert entries[0]["user_id"] == "1"
    assert "balance" not in entries[0]


@pytest.mark.asyncio
async def test_theft_protection_owner_guarantee_and_next_harvest(farm):
    await act(farm, "plant", plot_id=1, crop_id="huangjing")
    farm.clock.advance(1800)
    with pytest.raises(module.FarmError, match="保护"):
        await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1)
    farm.clock.advance(86400)
    stolen = await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1, request_id="theft-one")
    assert stolen["plots"][0]["yield_remaining"] == 3
    assert stolen["is_owner"] is False
    await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1, request_id="theft-one")
    with pytest.raises(module.FarmError):
        await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1)
    with pytest.raises(module.FarmError):
        await act(farm, "steal", profile={"user_id": "3"}, target_user_id=1, plot_id=1)
    assert (await act(farm, "harvest", plot_id=1))["result"]["quantity"] == 3
    assert (await farm.service.get_farm(VISITOR))["inventory"]["produce"][0]["quantity"] == 1
    await act(farm, "plant", plot_id=1, crop_id="huangjing")
    farm.clock.advance(1800)
    assert (await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1))["result"]["quantity"] == 1


@pytest.mark.asyncio
async def test_theft_daily_limit_resets_in_shanghai_day(farm):
    await act(farm, "plant", plot_id=1, crop_id="huangjing")
    farm.clock.advance(90000)
    with sqlite3.connect(farm.path) as connection:
        for _ in range(10):
            connection.execute("INSERT INTO farm_events(owner_id,actor_id,action,message,created_at) VALUES (1,2,'steal','历史偷取',?)", (farm.clock(),))
    with pytest.raises(module.FarmError, match="今日"):
        await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1)
    farm.clock.advance(86400)
    assert (await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1))["result"]["quantity"] == 1


@pytest.mark.asyncio
async def test_simultaneous_owner_harvest_and_theft_conserve_crop(farm):
    await act(farm, "plant", plot_id=1, crop_id="huangjing")
    farm.clock.advance(90000)
    outcomes = await asyncio.gather(
        act(farm, "harvest", plot_id=1),
        act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1),
        return_exceptions=True,
    )
    assert not isinstance(outcomes[0], Exception)
    owner = await farm.service.get_farm(OWNER)
    visitor = await farm.service.get_farm(VISITOR)
    assert sum(item["quantity"] for item in owner["inventory"]["produce"] + visitor["inventory"]["produce"]) == 4


@pytest.mark.asyncio
async def test_failed_sale_preserves_produce_and_retry_reads_fresh_balance(farm):
    await act(farm, "plant", plot_id=1, crop_id="huangjing")
    farm.clock.advance(1800)
    await act(farm, "harvest", plot_id=1)
    with sqlite3.connect(farm.path) as connection:
        connection.execute("CREATE TRIGGER fail_sale BEFORE INSERT ON coin_transactions BEGIN SELECT RAISE(ABORT,'ledger failed'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await act(farm, "sell", crop_id="huangjing", quantity=4, request_id="resumable-sale")
    assert (await farm.service.get_farm(OWNER))["inventory"]["produce"][0]["quantity"] == 4
    with sqlite3.connect(farm.path) as connection:
        connection.execute("DROP TRIGGER fail_sale")
    assert (await act(farm, "sell", crop_id="huangjing", quantity=4, request_id="resumable-sale"))["balance"] == 1040
    await act(farm, "buy_seed", crop_id="huangjing")
    replay = await act(farm, "sell", crop_id="huangjing", quantity=4, request_id="resumable-sale")
    assert replay["result"]["replayed"] is True
    assert replay["balance"] == replay["result"]["balance"] == 1016
    assert ledger(farm.path) == (1016, 16)


@pytest.mark.asyncio
async def test_upgrade_requirements_costs_and_remaining_growth(farm):
    await farm.service.get_farm(OWNER)
    with pytest.raises(module.FarmError, match="达到"):
        await act(farm, "expand")
    with pytest.raises(module.FarmError, match="达到"):
        await act(farm, "buy_seed", crop_id="jinlei")
    edit_state(farm, lambda state: state.update(xp=80))
    expanded = await act(farm, "expand")
    assert len(expanded["plots"]) == 4
    assert expanded["balance"] == 820
    planted = await act(farm, "plant", plot_id=4, crop_id="huangjing")
    farm.clock.advance(300)
    upgraded = await act(farm, "upgrade_aura")
    assert upgraded["balance"] == 520
    assert upgraded["farm"]["growth_multiplier"] == 1.1
    assert upgraded["plots"][3]["mature_at"] == pytest.approx(farm.clock() + 1500 / 1.1)
    assert upgraded["plots"][3]["mutation_chance"] == 0.04
    new = await act(farm, "plant", plot_id=1, crop_id="huangjing")
    assert new["plots"][0]["mutation_chance"] == 0.05
    assert new["plots"][0]["mature_at"] == pytest.approx(farm.clock() + 1800 / 1.1)


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"quantity": -1}, {"quantity": True}, {"quantity": 1.2}, {"quantity": 100},
    {"crop_id": "missing"}, {"request_id": "short"},
])
async def test_invalid_purchases_never_change_money(farm, kwargs):
    params = {"crop_id": "huangjing", **kwargs}
    with pytest.raises(module.FarmError):
        await act(farm, "buy_seed", **params)
    assert ledger(farm.path) == (1000, 0)


def test_economy_progression_has_positive_regular_yield_and_source_attribution():
    assert len(catalog.CROPS) == 8
    assert all(crop["sale_price"] * crop["base_yield"] > crop["seed_price"] for crop in catalog.CROPS)
    assert [crop["grow_seconds"] for crop in catalog.CROPS] == sorted(crop["grow_seconds"] for crop in catalog.CROPS)
    assert [crop["unlock_level"] for crop in catalog.CROPS] == sorted(crop["unlock_level"] for crop in catalog.CROPS)
    assert catalog.LAND_LEVELS[-1]["plot_count"] == 12
    assert catalog.AURA_LEVELS[-1]["growth_multiplier"] == 1.5
    assert "游戏改编" in catalog.RULES["pricing_note"]
    assert "原著无种子" in catalog.CROP_BY_ID["yusui"]["description"]

class CountingRandom:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def random(self):
        self.calls += 1
        return self.value


async def mature_public_plot(farm, plot_id=1):
    await act(farm, "plant", plot_id=plot_id, crop_id="huangjing")
    edit_state(farm, lambda state: state.update(created_at=farm.clock() - 90000))
    farm.clock.advance(1800)


@pytest.mark.asyncio
async def test_old_json_state_and_old_receipt_need_no_migration(farm):
    await act(farm, "plant", plot_id=1, crop_id="huangjing", request_id="pre-v2-plant")
    def remove_v2(state):
        for key in ("pets", "items", "equipped_pet"):
            state.pop(key)
        for key in ("guarded", "caught"):
            state["stats"].pop(key)
        for key in ("theft_attempted_by", "dew_used", "ward_used"):
            state["plots"][0].pop(key)
    edit_state(farm, remove_v2)
    replay = await act(farm, "plant", plot_id=1, crop_id="huangjing", request_id="pre-v2-plant")
    assert replay["result"]["replayed"] is True
    assert replay["guardian"] is None and replay["pets"] == []
    assert replay["inventory"]["items"] == []
    assert replay["plots"][0]["dew_used"] is False
    assert replay["plots"][0]["ward_active"] is False
    assert replay["farm"]["stats"]["guarded"] == 0
    assert replay["inventory"]["seeds"][0]["quantity"] == 5


@pytest.mark.asyncio
async def test_pet_purchase_levels_equipment_and_independent_feeding_clock(farm):
    await farm.service.get_farm(OWNER)
    with pytest.raises(module.FarmError, match="达到 2"):
        await act(farm, "buy_pet", pet_id="mountain_hound")
    with pytest.raises(module.FarmError, match="尚未拥有"):
        await act(farm, "equip_pet", pet_id="qingling_fox")
    fox = await act(farm, "buy_pet", pet_id="qingling_fox")
    assert fox["balance"] == 700
    assert fox["guardian"]["pet_id"] == "qingling_fox"
    fox_until = fox["guardian"]["guard_until"]
    assert fox_until == farm.clock() + 86400
    with pytest.raises(module.FarmError, match="已经拥有"):
        await act(farm, "buy_pet", pet_id="qingling_fox")
    edit_state(farm, lambda state: state.update(xp=80))
    farm.clock.advance(300)
    hound = await act(farm, "buy_pet", pet_id="mountain_hound")
    assert hound["balance"] == 50
    assert hound["guardian"]["pet_id"] == "qingling_fox"
    equipped = await act(farm, "equip_pet", pet_id="mountain_hound")
    assert sum(pet["equipped"] for pet in equipped["pets"]) == 1
    assert equipped["guardian"]["guard_chance"] == 0.4
    assert equipped["guardian"]["guard_until"] == farm.clock() + 86400
    restored = await act(farm, "equip_pet", pet_id="qingling_fox")
    assert restored["guardian"]["guard_until"] == fox_until
    assert ledger(farm.path) == (50, -950)


@pytest.mark.asyncio
@pytest.mark.parametrize("action,params", [
    ("buy_pet", {"pet_id": "qingling_fox"}),
    ("buy_item", {"item_id": "pet_food", "quantity": 3}),
])
async def test_companion_purchase_failure_rolls_back_money_inventory_and_receipt(farm, action, params):
    before = await farm.service.get_farm(OWNER)
    with sqlite3.connect(farm.path) as connection:
        connection.execute("CREATE TRIGGER fail_coin BEFORE INSERT ON coin_transactions BEGIN SELECT RAISE(ABORT,'ledger failed'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await act(farm, action, request_id="v2-rollback", **params)
    after = await farm.service.get_farm(OWNER)
    assert after["pets"] == before["pets"]
    assert after["inventory"] == before["inventory"]
    assert after["guardian"] == before["guardian"]
    assert ledger(farm.path) == (1000, 0)
    with sqlite3.connect(farm.path) as connection:
        assert connection.execute("SELECT count(*) FROM farm_actions").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_pet_concurrent_purchase_retry_only_charges_once(farm):
    responses = await asyncio.gather(*(act(farm, "buy_pet", pet_id="qingling_fox", request_id="buy-fox-once") for _ in range(6)))
    assert sum(bool(response["result"].get("replayed")) for response in responses) == 5
    assert all(response["balance"] == 700 for response in responses)
    assert ledger(farm.path) == (700, -300)
    with pytest.raises(module.FarmError) as error:
        await act(farm, "buy_pet", pet_id="mountain_hound", request_id="buy-fox-once")
    assert error.value.status == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("action,params", [
    ("buy_pet", {"pet_id": "qingling_fox", "quantity": 2}),
    ("buy_pet", {"pet_id": "missing"}),
    ("buy_pet", {"pet_id": []}),
    ("buy_item", {"item_id": "pet_food", "quantity": True}),
    ("buy_item", {"item_id": "pet_food", "quantity": 100}),
    ("buy_item", {"item_id": "pet_food", "quantity": 0}),
    ("buy_item", {"item_id": "missing"}),
    ("use_item", {"item_id": "pet_food", "quantity": 2}),
])
async def test_invalid_companion_requests_do_not_change_money(farm, action, params):
    with pytest.raises(module.FarmError):
        await act(farm, action, **params)
    assert ledger(farm.path) == (1000, 0)


@pytest.mark.asyncio
async def test_food_expiry_and_seventy_two_hour_cap_preserve_unused_food(farm):
    await act(farm, "buy_item", item_id="pet_food", quantity=4)
    with pytest.raises(module.FarmError, match="先结缘"):
        await act(farm, "use_item", item_id="pet_food")
    await act(farm, "buy_pet", pet_id="qingling_fox")
    await act(farm, "use_item", item_id="pet_food", request_id="first-food")
    full = await act(farm, "use_item", item_id="pet_food")
    assert full["guardian"]["remaining_seconds"] == 72 * 3600
    assert full["inventory"]["items"] == [{"item_id": "pet_food", "quantity": 2}]
    replay = await act(farm, "use_item", item_id="pet_food", request_id="first-food")
    assert replay["guardian"]["remaining_seconds"] == 72 * 3600
    with pytest.raises(module.FarmError, match="72小时"):
        await act(farm, "use_item", item_id="pet_food")
    farm.clock.advance(72 * 3600)
    expired = await farm.service.get_farm(OWNER)
    assert expired["guardian"]["active"] is False
    assert expired["guardian"]["remaining_seconds"] == 0
    assert expired["inventory"]["items"][0]["quantity"] == 2
    fed = await act(farm, "use_item", item_id="pet_food")
    assert fed["guardian"]["active"] is True
    assert fed["guardian"]["remaining_seconds"] == 86400
    assert ledger(farm.path) == (540, -460)


@pytest.mark.asyncio
@pytest.mark.parametrize("pet_id,chance", [("qingling_fox", 0.25), ("mountain_hound", 0.4), ("dew_crane", 0.15)])
@pytest.mark.parametrize("difference,caught", [(-0.00001, True), (0, False)])
async def test_guard_probability_boundary_for_each_pet(farm, pet_id, chance, difference, caught):
    await farm.service.get_farm(OWNER)
    edit_state(farm, lambda state: state.update(xp=240))
    await act(farm, "buy_pet", pet_id=pet_id)
    await mature_public_plot(farm)
    rng = CountingRandom(chance + difference)
    farm.service.rng = rng
    result = await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1)
    assert result["result"]["caught"] is caught
    assert result["result"]["quantity"] == (0 if caught else 1)
    assert result["plots"][0]["yield_remaining"] == (4 if caught else 3)
    assert result["plots"][0]["theft_attempted"] is True
    assert rng.calls == 1


@pytest.mark.asyncio
async def test_expired_guard_does_not_roll_or_catch(farm):
    await act(farm, "buy_pet", pet_id="qingling_fox")
    await mature_public_plot(farm)
    farm.clock.advance(86400)
    rng = CountingRandom(0)
    farm.service.rng = rng
    result = await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1)
    assert result["guardian"]["active"] is False
    assert result["result"]["caught"] is False
    assert result["result"]["quantity"] == 1
    assert rng.calls == 0


@pytest.mark.asyncio
async def test_caught_attempt_is_atomic_idempotent_and_records_both_sides_without_produce(farm):
    await act(farm, "buy_pet", pet_id="qingling_fox")
    await mature_public_plot(farm)
    rng = CountingRandom(0)
    farm.service.rng = rng
    attempts = await asyncio.gather(*(act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1, request_id="catch-once") for _ in range(5)))
    assert all(attempt["result"]["caught"] for attempt in attempts)
    assert sum(bool(attempt["result"].get("replayed")) for attempt in attempts) == 4
    assert rng.calls == 1
    with pytest.raises(module.FarmError, match="本茬已尝试"):
        await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1)
    assert rng.calls == 1
    owner = await farm.service.get_farm(OWNER)
    visitor = await farm.service.get_farm(VISITOR)
    assert owner["farm"]["stats"]["guarded"] == 1
    assert visitor["farm"]["stats"]["caught"] == 1
    assert visitor["farm"]["stats"]["stolen"] == 0
    assert visitor["inventory"]["produce"] == []
    assert owner["plots"][0]["stolen_count"] == 0
    assert owner["activity"][0]["action"] == "steal_caught"
    assert visitor["activity"][0]["action"] == "caught"
    with sqlite3.connect(farm.path) as connection:
        assert connection.execute("SELECT action,count(*) FROM farm_events WHERE action IN ('steal_caught','caught') GROUP BY action").fetchall() == [("caught", 1), ("steal_caught", 1)]
    # 被抓不占掉产物份额，其他人仍能在本茬尝试。
    farm.service.rng = CountingRandom(0.9)
    other = await act(farm, "steal", profile={"user_id": "3"}, target_user_id=1, plot_id=1)
    assert other["result"]["quantity"] == 1
    assert (await act(farm, "harvest", plot_id=1))["result"]["quantity"] == 3
    await act(farm, "plant", plot_id=1, crop_id="huangjing")
    farm.clock.advance(1800)
    assert (await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1))["result"]["quantity"] == 1


@pytest.mark.asyncio
async def test_caught_attempt_uses_daily_limit_once_and_rejected_attempt_does_not_roll(farm):
    await act(farm, "buy_pet", pet_id="qingling_fox")
    await mature_public_plot(farm)
    await act(farm, "plant", plot_id=2, crop_id="huangjing")
    farm.clock.advance(1800)
    with sqlite3.connect(farm.path) as connection:
        for _ in range(9):
            connection.execute("INSERT INTO farm_events(owner_id,actor_id,action,message,created_at) VALUES (1,2,'steal','历史偷取',?)", (farm.clock(),))
    rng = CountingRandom(0)
    farm.service.rng = rng
    await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1, request_id="daily-catch")
    await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1, request_id="daily-catch")
    with pytest.raises(module.FarmError, match="今日已经偷取 10 次"):
        await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=2)
    assert rng.calls == 1
    farm.clock.advance(86400)
    assert (await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=2))["result"]["quantity"] == 1


@pytest.mark.asyncio
async def test_failed_catch_event_rolls_back_attempt_and_statistics(farm):
    await act(farm, "buy_pet", pet_id="qingling_fox")
    await mature_public_plot(farm)
    farm.service.rng = CountingRandom(0)
    with sqlite3.connect(farm.path) as connection:
        connection.execute("CREATE TRIGGER fail_event BEFORE INSERT ON farm_events WHEN NEW.action='caught' BEGIN SELECT RAISE(ABORT,'event failed'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1, request_id="catch-fails")
    owner = await farm.service.get_farm(OWNER)
    public = await farm.service.get_farm(VISITOR, 1)
    assert owner["farm"]["stats"]["guarded"] == 0
    assert public["plots"][0]["can_steal"] is True
    assert public["plots"][0]["theft_attempted"] is False
    with sqlite3.connect(farm.path) as connection:
        assert connection.execute("SELECT count(*) FROM farm_events WHERE action='steal_caught'").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM farm_actions WHERE request_id='catch-fails'").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False])
async def test_crane_water_bonus_only_while_fed_and_dew_only_once(farm, active):
    await farm.service.get_farm(OWNER)
    edit_state(farm, lambda state: state.update(xp=240))
    await act(farm, "buy_pet", pet_id="dew_crane")
    await act(farm, "buy_item", item_id="spirit_dew", quantity=2)
    if not active:
        farm.clock.advance(86400)
    planted = await act(farm, "plant", plot_id=1, crop_id="huangjing")
    initial = planted["plots"][0]["mature_at"]
    watered = await act(farm, "water", plot_id=1)
    assert watered["plots"][0]["mature_at"] == initial - (270 if active else 180)
    dewed = await act(farm, "use_item", item_id="spirit_dew", plot_id=1, request_id="dew-once")
    assert dewed["plots"][0]["mature_at"] == initial - (450 if active else 360)
    assert dewed["plots"][0]["dew_used"] is True
    retry = await act(farm, "use_item", item_id="spirit_dew", plot_id=1, request_id="dew-once")
    assert retry["plots"][0]["mature_at"] == dewed["plots"][0]["mature_at"]
    with pytest.raises(module.FarmError, match="已经使用过灵露"):
        await act(farm, "use_item", item_id="spirit_dew", plot_id=1)
    assert (await farm.service.get_farm(OWNER))["inventory"]["items"][0]["quantity"] == 1
    farm.clock.advance(1800)
    await act(farm, "harvest", plot_id=1)
    again = await act(farm, "plant", plot_id=1, crop_id="huangjing")
    assert again["plots"][0]["dew_used"] is False


@pytest.mark.asyncio
async def test_dew_never_moves_maturity_before_now_and_cannot_use_on_empty_or_mature_plot(farm):
    await act(farm, "buy_item", item_id="spirit_dew", quantity=3)
    with pytest.raises(module.FarmError, match="还没有种植"):
        await act(farm, "use_item", item_id="spirit_dew", plot_id=1)
    await act(farm, "plant", plot_id=1, crop_id="huangjing")
    farm.clock.advance(1799)
    dewed = await act(farm, "use_item", item_id="spirit_dew", plot_id=1)
    assert dewed["plots"][0]["mature_at"] == farm.clock()
    assert dewed["plots"][0]["status"] == "mature"
    with pytest.raises(module.FarmError, match="已经成熟"):
        await act(farm, "use_item", item_id="spirit_dew", plot_id=1)
    assert (await farm.service.get_farm(OWNER))["inventory"]["items"][0]["quantity"] == 2


@pytest.mark.asyncio
async def test_talisman_blocks_attempts_then_expires_without_repeat_use(farm):
    await mature_public_plot(farm)
    await act(farm, "buy_item", item_id="ward_talisman", quantity=2)
    ward = await act(farm, "use_item", item_id="ward_talisman", plot_id=1, request_id="ward-once")
    assert ward["plots"][0]["ward_active"] is True
    assert ward["plots"][0]["ward_until"] == farm.clock() + 3600
    farm.clock.advance(10)
    retry = await act(farm, "use_item", item_id="ward_talisman", plot_id=1, request_id="ward-once")
    assert retry["plots"][0]["ward_until"] == ward["plots"][0]["ward_until"]
    with pytest.raises(module.FarmError, match="处于保护"):
        await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1)
    with sqlite3.connect(farm.path) as connection:
        assert connection.execute("SELECT count(*) FROM farm_events WHERE actor_id=2").fetchone()[0] == 0
    farm.clock.advance(3590)
    public = await farm.service.get_farm(VISITOR, 1)
    assert public["plots"][0]["ward_active"] is False
    assert public["plots"][0]["can_steal"] is True
    with pytest.raises(module.FarmError, match="已经使用过护田符"):
        await act(farm, "use_item", item_id="ward_talisman", plot_id=1)
    assert (await act(farm, "steal", profile=VISITOR, target_user_id=1, plot_id=1))["result"]["quantity"] == 1
    assert (await farm.service.get_farm(OWNER))["inventory"]["items"][0]["quantity"] == 1


@pytest.mark.asyncio
async def test_companions_items_and_purchase_events_are_private_and_target_actions_rejected(farm):
    await farm.service.get_farm(OWNER)
    edit_state(farm, lambda state: state.update(xp=240))
    await act(farm, "buy_pet", pet_id="qingling_fox")
    await act(farm, "buy_pet", pet_id="dew_crane")
    await act(farm, "buy_item", item_id="pet_food", quantity=2)
    await act(farm, "use_item", item_id="pet_food")
    public = await farm.service.get_farm(VISITOR, 1)
    assert public["pets"] == []
    assert public["inventory"] == {"seeds": [], "produce": [], "items": []}
    assert public["balance"] is None and public["farm"]["stats"] is None
    assert public["guardian"]["pet_id"] == "qingling_fox"
    assert public["guardian"]["remaining_seconds"] == 48 * 3600
    assert public["activity"] == []
    for action, kwargs in [("buy_pet", {"pet_id": "qingling_fox"}),
                           ("equip_pet", {"pet_id": "dew_crane"}),
                           ("buy_item", {"item_id": "pet_food"}),
                           ("use_item", {"item_id": "ward_talisman", "plot_id": 1})]:
        with pytest.raises(module.FarmError, match="自己的"):
            await act(farm, action, profile=VISITOR, target_user_id=1, **kwargs)
    assert ledger(farm.path, 2) == (1000, 0)
    visits = (await farm.service.list_farms(VISITOR))["entries"]
    assert all("pets" not in visit and "items" not in visit and "balance" not in visit for visit in visits)

@pytest.mark.asyncio
async def test_item_event_failure_rolls_back_consumption_effect_and_receipt(farm):
    await act(farm, "buy_item", item_id="spirit_dew")
    before = await act(farm, "plant", crop_id="huangjing", plot_id=1)
    with sqlite3.connect(farm.path) as connection:
        connection.execute("CREATE TRIGGER fail_item_event BEFORE INSERT ON farm_events WHEN NEW.action='use_item' BEGIN SELECT RAISE(ABORT,'event failed'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await act(farm, "use_item", item_id="spirit_dew", plot_id=1, request_id="rollback-dew")
    after = await farm.service.get_farm(OWNER)
    assert after["plots"][0]["mature_at"] == before["plots"][0]["mature_at"]
    assert after["plots"][0]["dew_used"] is False
    assert after["inventory"]["items"] == [{"item_id": "spirit_dew", "quantity": 1}]
    with sqlite3.connect(farm.path) as connection:
        assert connection.execute("SELECT count(*) FROM farm_actions WHERE request_id='rollback-dew'").fetchone()[0] == 0
    assert ledger(farm.path) == (980, -20)
