"""灵圃 API 使用真实路由与 SQLite，验证身份边界和结构化输入。"""

import importlib
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
import httpx
import pytest


api_module = importlib.import_module("src.chat.features.games.blackjack-web.farm_api")


def database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE user_coins (user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL);
            CREATE TABLE coin_transactions (id INTEGER PRIMARY KEY, user_id INTEGER, amount INTEGER, reason TEXT);
            INSERT INTO user_coins VALUES (1, 100), (2, 500);
        """)


@pytest.fixture
def api(tmp_path):
    path = tmp_path / "farm-api.sqlite"
    database(path)
    current_path = [path]
    application = FastAPI()

    async def identity(request: Request):
        user_id = request.headers.get("X-Test-User")
        if user_id not in {"1", "2"}:
            raise HTTPException(status_code=401, detail="需要登录")
        return {"user_id": user_id, "username": f"道友{user_id}", "avatar_url": ""}

    application.include_router(api_module.create_farm_router(identity, lambda: current_path[0]))
    return SimpleNamespace(app=application, path=path, current_path=current_path, tmp_path=tmp_path)


def client(api, uid=None):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://farm.test",
                             headers={"X-Test-User": uid} if uid is not None else {})


@pytest.mark.asyncio
async def test_all_routes_require_authenticated_profile_and_do_not_create_anonymous_accounts(api):
    async with client(api) as browser:
        assert (await browser.get("/api/farm")).status_code == 401
        assert (await browser.get("/api/farm/visits")).status_code == 401
        assert (await browser.post("/api/farm/action", json={"action": "expand", "request_id": uuid4().hex})).status_code == 401
    with sqlite3.connect(api.path) as connection:
        assert connection.execute("SELECT count(*) FROM user_coins").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM coin_transactions").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_purchase_and_visit_keep_balances_and_inventories_isolated(api):
    async with client(api, "1") as owner, client(api, "2") as visitor:
        response = await owner.post("/api/farm/action", json={"action": "buy_seed", "request_id": uuid4().hex, "crop_id": "huangjing", "quantity": 2})
        assert response.status_code == 200
        assert response.json()["balance"] == 52
        assert (await visitor.get("/api/farm")).json()["balance"] == 500
        public = (await visitor.get("/api/farm?owner_id=1")).json()
        assert public["owner"]["user_id"] == "1"
        assert public["balance"] is None
        assert public["inventory"] == {"seeds": [], "produce": [], "items": []}
        visits = (await visitor.get("/api/farm/visits")).json()["entries"]
        assert [entry["user_id"] for entry in visits] == ["1"]
        assert "balance" not in visits[0]
    with sqlite3.connect(api.path) as connection:
        assert connection.execute("SELECT user_id,amount FROM coin_transactions").fetchall() == [(1, -48)]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"quantity": True}, {"quantity": "2"}, {"quantity": -1}, {"quantity": 100},
    {"balance": 999999}, {"price": 0}, {"user_id": 2}, {"target_user_id": "2"},
    {"crop_id": None}, {"request_id": "x"}, {"action": "free_money"}, {"plot_id": 1},
])
async def test_strict_purchase_fields_reject_coercion_account_spoofing_and_extra_prices(api, change):
    body = {"action": "buy_seed", "request_id": uuid4().hex, "crop_id": "huangjing", **change}
    async with client(api, "1") as browser:
        response = await browser.post("/api/farm/action", json=body)
        assert response.status_code == 422
        assert (await browser.get("/api/farm")).json()["balance"] == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"action": "plant", "crop_id": "huangjing"},
    {"action": "plant", "crop_id": "huangjing", "plot_id": "1"},
    {"action": "water", "plot_id": True},
    {"action": "harvest", "plot_id": 0},
    {"action": "sell", "crop_id": "huangjing", "quality": "legendary"},
    {"action": "steal", "plot_id": 1},
    {"action": "steal", "plot_id": 1, "target_user_id": 1.2},
    {"action": "steal", "plot_id": 1, "target_user_id": -1},
])
async def test_other_action_schemas_require_exact_fields(api, body):
    async with client(api, "1") as browser:
        response = await browser.post("/api/farm/action", json={"request_id": uuid4().hex, **body})
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_business_errors_keep_status_and_idempotency_conflicts(api):
    async with client(api, "1") as browser:
        assert (await browser.get("/api/farm?owner_id=2")).status_code == 404
        invalid = await browser.get("/api/farm?owner_id=abc")
        assert invalid.status_code == 422
        insufficient = await browser.post("/api/farm/action", json={"action": "buy_seed", "request_id": uuid4().hex, "crop_id": "huangjing", "quantity": 5})
        assert insufficient.status_code == 400
        assert "灵石不足" in insufficient.json()["detail"]
        body = {"action": "buy_seed", "request_id": uuid4().hex, "crop_id": "huangjing"}
        assert (await browser.post("/api/farm/action", json=body)).status_code == 200
        assert (await browser.post("/api/farm/action", json=body)).json()["result"]["replayed"] is True
        conflict = await browser.post("/api/farm/action", json={**body, "quantity": 2})
        assert conflict.status_code == 409
        assert (await browser.get("/api/farm")).json()["balance"] == 76


@pytest.mark.asyncio
async def test_database_provider_is_resolved_again_after_path_changes(api):
    async with client(api, "1") as browser:
        bought = await browser.post("/api/farm/action", json={"action": "buy_seed", "request_id": uuid4().hex, "crop_id": "huangjing"})
        assert bought.json()["balance"] == 76
        replacement = api.tmp_path / "replacement.sqlite"
        database(replacement)
        api.current_path[0] = replacement
        fresh = (await browser.get("/api/farm")).json()
        assert fresh["balance"] == 100
        assert fresh["inventory"]["seeds"] == [{"crop_id": "huangjing", "quantity": 6}]

@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"action": "buy_pet"},
    {"action": "buy_pet", "pet_id": "qingling_fox", "price": 0},
    {"action": "buy_pet", "pet_id": "qingling_fox", "quantity": 2},
    {"action": "buy_pet", "pet_id": "qingling_fox", "target_user_id": "2"},
    {"action": "equip_pet", "pet_id": None},
    {"action": "equip_pet", "pet_id": "qingling_fox", "guard_until": 999999999999},
    {"action": "buy_item", "item_id": "pet_food", "quantity": True},
    {"action": "buy_item", "item_id": "pet_food", "quantity": "1"},
    {"action": "buy_item", "item_id": "pet_food", "quantity": 100},
    {"action": "buy_item", "item_id": "pet_food", "quantity": 0},
    {"action": "use_item", "item_id": "pet_food", "quantity": 1},
    {"action": "use_item", "item_id": "pet_food", "plot_id": 1},
    {"action": "use_item", "item_id": "spirit_dew"},
    {"action": "use_item", "item_id": "ward_talisman", "plot_id": 1, "target_user_id": "2"},
    {"action": "use_item", "item_id": "ward_talisman", "plot_id": 13},
    {"action": "water", "plot_id": 1, "pet_id": "dew_crane"},
])
async def test_companion_action_schema_rejects_missing_and_untrusted_fields(api, body):
    async with client(api, "1") as browser:
        response = await browser.post("/api/farm/action", json={"request_id": uuid4().hex, **body})
        assert response.status_code == 422
        state = (await browser.get("/api/farm")).json()
        assert state["balance"] == 100
        assert state["pets"] == [] and state["inventory"]["items"] == []
    with sqlite3.connect(api.path) as connection:
        assert connection.execute("SELECT count(*) FROM coin_transactions").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_companion_routes_require_login_and_reject_unknown_catalog_ids(api):
    async with client(api) as anonymous:
        for body in [{"action": "buy_pet", "pet_id": "qingling_fox"},
                     {"action": "buy_item", "item_id": "pet_food"},
                     {"action": "use_item", "item_id": "pet_food"}]:
            assert (await anonymous.post("/api/farm/action", json={"request_id": uuid4().hex, **body})).status_code == 401
    async with client(api, "1") as browser:
        for body in [{"action": "buy_pet", "pet_id": "fake_fox"},
                     {"action": "buy_item", "item_id": "free_coins"}]:
            response = await browser.post("/api/farm/action", json={"request_id": uuid4().hex, **body})
            assert response.status_code == 400
        insufficient = await browser.post("/api/farm/action", json={"action": "buy_pet", "pet_id": "qingling_fox", "request_id": uuid4().hex})
        assert insufficient.status_code == 400
        assert "灵石不足" in insufficient.json()["detail"]


@pytest.mark.asyncio
async def test_companion_api_full_workflow_catalog_privacy_and_idempotent_requests(api):
    async with client(api, "2") as owner, client(api, "1") as visitor:
        state = (await owner.get("/api/farm")).json()
        assert [pet["price"] for pet in state["catalog"]["pets"]] == [300, 650, 500]
        assert [item["price"] for item in state["catalog"]["items"]] == [40, 20, 35]
        purchase = {"action": "buy_pet", "pet_id": "qingling_fox", "request_id": uuid4().hex}
        pet = await owner.post("/api/farm/action", json=purchase)
        assert pet.status_code == 200
        assert pet.json()["balance"] == 200
        assert pet.json()["guardian"]["active"] is True
        repeat = (await owner.post("/api/farm/action", json=purchase)).json()
        assert repeat["balance"] == 200 and repeat["result"]["replayed"] is True
        for item_id in ["pet_food", "spirit_dew", "ward_talisman"]:
            bought = await owner.post("/api/farm/action", json={"action": "buy_item", "item_id": item_id, "request_id": uuid4().hex})
            assert bought.status_code == 200
        fed = await owner.post("/api/farm/action", json={"action": "use_item", "item_id": "pet_food", "request_id": uuid4().hex})
        assert fed.status_code == 200
        assert fed.json()["guardian"]["remaining_seconds"] > 47 * 3600
        planted = await owner.post("/api/farm/action", json={"action": "plant", "crop_id": "huangjing", "plot_id": 1, "request_id": uuid4().hex})
        assert planted.status_code == 200
        for item_id in ["spirit_dew", "ward_talisman"]:
            used = await owner.post("/api/farm/action", json={"action": "use_item", "item_id": item_id, "plot_id": 1, "request_id": uuid4().hex})
            assert used.status_code == 200
        assert used.json()["plots"][0]["ward_active"] is True
        assert used.json()["plots"][0]["dew_used"] is True
        assert used.json()["balance"] == 105
        private = used.json()["activity"]
        assert any(event["action"] == "buy_pet" for event in private)
        public = (await visitor.get("/api/farm?owner_id=2")).json()
        assert public["guardian"]["pet_id"] == "qingling_fox"
        assert public["balance"] is None and public["pets"] == []
        assert public["inventory"] == {"seeds": [], "produce": [], "items": []}
        assert public["plots"][0]["can_steal"] is False
        assert [event["action"] for event in public["activity"]] == ["plant"]
        assert (await visitor.get("/api/farm")).json()["balance"] == 100
    with sqlite3.connect(api.path) as connection:
        assert connection.execute("SELECT sum(amount) FROM coin_transactions WHERE user_id=2").fetchone()[0] == -395
