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
    assert visiting["inventory"] == {"seeds": [], "produce": []}
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
