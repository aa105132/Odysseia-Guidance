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
        assert public["inventory"] == {"seeds": [], "produce": []}
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
