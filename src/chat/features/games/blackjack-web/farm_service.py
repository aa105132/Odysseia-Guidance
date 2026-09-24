"""灵圃持久化：作物、背包、灵石与幂等回执在同一写事务内提交。"""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from importlib import import_module
import json
from pathlib import Path
import random
import sqlite3
import time
from uuid import uuid4


catalog = import_module("src.chat.features.games.blackjack-web.farm_catalog")


class FarmError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class FarmService:
    def __init__(self, db_path, *, clock=time.time, rng=None):
        self.db_path = str(db_path)
        self.clock = clock
        self.rng = rng if rng is not None else random.SystemRandom()

    @contextmanager
    def _transaction(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=15000")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE IF NOT EXISTS farm_profiles (
                user_id INTEGER PRIMARY KEY, username TEXT NOT NULL, avatar_url TEXT NOT NULL,
                state TEXT NOT NULL, updated_at REAL NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS farm_actions (
                user_id INTEGER NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                result TEXT NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY(user_id, request_id))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS farm_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL,
                actor_id INTEGER NOT NULL, action TEXT NOT NULL, message TEXT NOT NULL,
                created_at REAL NOT NULL)""")
            connection.execute("CREATE INDEX IF NOT EXISTS farm_events_owner ON farm_events(owner_id, event_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS farm_theft_daily ON farm_events(actor_id, action, created_at)")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _user_id(value):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise FarmError("玩家编号不正确")
        try:
            uid = int(value)
        except (TypeError, ValueError):
            raise FarmError("玩家编号不正确") from None
        if uid <= 0 or uid >= 2**63:
            raise FarmError("玩家编号不正确")
        return uid

    @staticmethod
    def _integer(value, minimum, maximum, label):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise FarmError(f"{label}应为 {minimum}—{maximum} 的整数")
        return value

    @staticmethod
    def _dump(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def get_catalog():
        return catalog.get_catalog()

    def _load(self, connection, uid, now, profile=None):
        row = connection.execute("SELECT * FROM farm_profiles WHERE user_id = ?", (uid,)).fetchone()
        if row is None:
            if profile is None:
                raise FarmError("这位道友还没有开辟灵圃", 404)
            state = {"xp": 0, "aura_level": 0, "unlocked_plots": 3, "created_at": now,
                     "seeds": {"huangjing": 6}, "produce": {},
                     "plots": [{"plot_id": index} for index in range(1, 4)],
                     "stats": {"harvests": 0, "stolen": 0, "earned": 0, "spent": 0, "mutations": 0}}
            farm = {"user_id": uid, "username": str(profile.get("username") or uid)[:100],
                    "avatar_url": str(profile.get("avatar_url") or "")[:1000], "state": state}
            # 新账号也可领入门灵种，初始化钱包不会赠送灵石。
            connection.execute("INSERT OR IGNORE INTO user_coins (user_id, balance) VALUES (?, 0)", (uid,))
            self._save(connection, farm, now)
            return farm
        farm = dict(row)
        farm["state"] = json.loads(farm["state"])
        if profile is not None:
            farm["username"] = str(profile.get("username") or uid)[:100]
            farm["avatar_url"] = str(profile.get("avatar_url") or "")[:1000]
        return farm

    def _save(self, connection, farm, now):
        connection.execute("""INSERT INTO farm_profiles (user_id, username, avatar_url, state, updated_at)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
            avatar_url=excluded.avatar_url, state=excluded.state, updated_at=excluded.updated_at""",
            (farm["user_id"], farm["username"], farm["avatar_url"], self._dump(farm["state"]), now))

    @staticmethod
    def _balance(connection, uid):
        row = connection.execute("SELECT balance FROM user_coins WHERE user_id = ?", (uid,)).fetchone()
        return row[0] if row else 0

    def _coins(self, connection, farm, amount, reason):
        uid = farm["user_id"]
        changed = connection.execute(
            "UPDATE user_coins SET balance = balance + ? WHERE user_id = ? AND balance >= ?",
            (amount, uid, max(0, -amount)),
        ).rowcount
        if changed != 1:
            raise FarmError(f"灵石不足，需要 {-amount} 灵石")
        connection.execute("INSERT INTO coin_transactions (user_id, amount, reason) VALUES (?, ?, ?)",
                           (uid, amount, "灵圃·" + reason))
        farm["state"]["stats"]["earned" if amount > 0 else "spent"] += abs(amount)

    @staticmethod
    def _add_inventory(inventory, key, amount):
        remaining = inventory.get(key, 0) + amount
        if remaining < 0:
            raise FarmError("背包数量不足")
        if remaining:
            inventory[key] = remaining
        else:
            inventory.pop(key, None)

    @staticmethod
    def _crop(crop_id):
        crop = catalog.CROP_BY_ID.get(crop_id)
        if crop is None:
            raise FarmError("没有这种灵植")
        return crop

    @staticmethod
    def _plot(state, plot_id):
        if isinstance(plot_id, bool) or not isinstance(plot_id, int) or not 1 <= plot_id <= state["unlocked_plots"]:
            raise FarmError("这块灵田尚未开辟")
        return state["plots"][plot_id - 1]

    @staticmethod
    def _yield(plot, now):
        crop = catalog.CROP_BY_ID[plot["crop_id"]]
        pest_active = plot["has_pest"] and not plot["pest_cleared"] and now >= plot["pest_at"]
        return crop["base_yield"] - int(pest_active)

    @staticmethod
    def _steal_limit(plot):
        return catalog.RULES["steal_per_harvest_limit"]

    def _can_steal(self, farm, plot, viewer_id, now):
        return bool(
            viewer_id != farm["user_id"] and plot.get("crop_id")
            and now >= farm["state"]["created_at"] + catalog.RULES["newcomer_protection_seconds"]
            and now >= plot["mature_at"] and str(viewer_id) not in plot["stolen_by"]
            and plot["stolen_count"] < self._steal_limit(plot)
            and self._yield(plot, now) - plot["stolen_count"] > 1
        )

    def _render(self, connection, farm, viewer_id, now):
        state = farm["state"]
        own = farm["user_id"] == viewer_id
        level = catalog.farm_level(state["xp"])
        aura = catalog.AURA_LEVELS[state["aura_level"]]
        plots = []
        for plot in state["plots"]:
            if not plot.get("crop_id"):
                plots.append({"plot_id": plot["plot_id"], "status": "empty", "crop_id": None,
                              "crop_name": "空灵田", "can_steal": False, "planted_at": None,
                              "mature_at": None, "progress": 0, "watered": False,
                              "has_pest": False, "pest_cleared": False, "quality": None,
                              "yield_total": 0, "yield_remaining": 0, "stolen_count": 0,
                              "mutation_chance": catalog.RULES["base_mutation_chance"] + aura["mutation_bonus"]})
                continue
            crop = self._crop(plot["crop_id"])
            mature = now >= plot["mature_at"]
            total = self._yield(plot, now)
            plots.append({
                "plot_id": plot["plot_id"], "status": "mature" if mature else "growing",
                "crop_id": crop["id"], "crop_name": crop["name"],
                "planted_at": plot["planted_at"], "mature_at": plot["mature_at"],
                "progress": min(1.0, max(0.0, (now - plot["planted_at"]) / (plot["mature_at"] - plot["planted_at"]))),
                "watered": plot["watered"],
                "has_pest": plot["has_pest"] and not plot["pest_cleared"] and now >= plot["pest_at"],
                "pest_cleared": plot["pest_cleared"], "quality": plot["quality"] if mature else None,
                "yield_total": total, "yield_remaining": total - plot["stolen_count"],
                "stolen_count": plot["stolen_count"], "can_steal": self._can_steal(farm, plot, viewer_id, now),
                "mutation_chance": plot["mutation_chance"],
            })
        inventory = {"seeds": [], "produce": []}
        if own:
            inventory["seeds"] = [{"crop_id": cid, "quantity": quantity} for cid, quantity in state["seeds"].items()]
            for key, quantity in state["produce"].items():
                cid, quality = key.split(":")
                inventory["produce"].append({"crop_id": cid, "quality": quality, "quantity": quantity,
                                              "unit_price": self._crop(cid)["sale_price"] * catalog.QUALITY_MULTIPLIERS[quality]})
        activity = [dict(row) for row in connection.execute(
            "SELECT event_id, action, message, created_at FROM farm_events WHERE owner_id = ? ORDER BY event_id DESC LIMIT 20",
            (farm["user_id"],)).fetchall()]
        return {
            "server_time": now, "is_owner": own,
            "owner": {"user_id": str(farm["user_id"]), "username": farm["username"], "avatar_url": farm["avatar_url"]},
            "farm": {"level": level, "xp": state["xp"],
                     "next_level_xp": catalog.LEVEL_XP[level] if level < len(catalog.LEVEL_XP) else None,
                     "unlocked_plots": state["unlocked_plots"], "aura_level": state["aura_level"],
                     "growth_multiplier": aura["growth_multiplier"], "mutation_bonus": aura["mutation_bonus"],
                     "created_at": state["created_at"],
                     "protected_until": state["created_at"] + catalog.RULES["newcomer_protection_seconds"],
                     "stats": dict(state["stats"]) if own else None},
            "balance": self._balance(connection, farm["user_id"]) if own else None,
            "plots": plots, "inventory": inventory, "catalog": self.get_catalog(), "activity": activity,
        }

    def _get_farm(self, profile, owner_id):
        uid = self._user_id(profile["user_id"])
        owner_id = uid if owner_id is None else self._user_id(owner_id)
        now = self.clock()
        with self._transaction() as connection:
            farm = self._load(connection, owner_id, now, profile if owner_id == uid else None)
            if owner_id == uid:
                self._save(connection, farm, now)
            return self._render(connection, farm, uid, now)

    async def get_farm(self, profile, owner_id=None):
        return await asyncio.to_thread(self._get_farm, profile, owner_id)

    def _list_farms(self, profile, limit):
        uid = self._user_id(profile["user_id"])
        self._integer(limit, 1, 100, "拜访数量")
        now = self.clock()
        with self._transaction() as connection:
            rows = connection.execute("SELECT * FROM farm_profiles WHERE user_id != ? ORDER BY updated_at DESC LIMIT ?",
                                      (uid, limit)).fetchall()
            entries = []
            for row in rows:
                state = json.loads(row["state"])
                entries.append({"user_id": str(row["user_id"]), "username": row["username"], "avatar_url": row["avatar_url"],
                                "level": catalog.farm_level(state["xp"]),
                                "mature_plots": sum(bool(plot.get("crop_id")) and now >= plot["mature_at"] for plot in state["plots"]),
                                "protected_until": state["created_at"] + catalog.RULES["newcomer_protection_seconds"]})
            return {"entries": entries, "server_time": now}

    async def list_farms(self, profile, limit=20):
        return await asyncio.to_thread(self._list_farms, profile, limit)

    @staticmethod
    def _event(connection, owner, actor, action, message, now):
        connection.execute("INSERT INTO farm_events (owner_id, actor_id, action, message, created_at) VALUES (?, ?, ?, ?, ?)",
                           (owner, actor, action, message, now))

    def _apply(self, connection, farm, action, params, now, target=None):
        state = farm["state"]
        level = catalog.farm_level(state["xp"])
        crop_id, plot_id = params["crop_id"], params["plot_id"]
        quantity, quality = params["quantity"], params["quality"]
        result = {"action": action}
        if action == "buy_seed":
            crop = self._crop(crop_id)
            self._integer(quantity, 1, 99, "购买数量")
            if level < crop["unlock_level"]:
                raise FarmError(f"灵圃达到 {crop['unlock_level']} 级才能购买{crop['name']}")
            self._coins(connection, farm, -crop["seed_price"] * quantity, f"购买{crop['name']}灵种×{quantity}")
            self._add_inventory(state["seeds"], crop_id, quantity)
            result["message"] = f"买入{crop['name']}灵种 × {quantity}"
        elif action == "plant":
            crop = self._crop(crop_id)
            plot = self._plot(state, plot_id)
            if plot.get("crop_id"):
                raise FarmError("这块灵田已有灵植，请先收获")
            if level < crop["unlock_level"]:
                raise FarmError(f"灵圃达到 {crop['unlock_level']} 级才能种植{crop['name']}")
            self._add_inventory(state["seeds"], crop_id, -1)
            aura = catalog.AURA_LEVELS[state["aura_level"]]
            duration = crop["grow_seconds"] / aura["growth_multiplier"]
            mutation_chance = catalog.RULES["base_mutation_chance"] + aura["mutation_bonus"]
            roll = self.rng.random()
            quality = "celestial" if roll < catalog.RULES["celestial_mutation_chance"] else "spirit" if roll < mutation_chance else "normal"
            plot.update({"crop_id": crop_id, "harvest_id": uuid4().hex, "planted_at": now,
                         "mature_at": now + duration, "duration": duration, "watered": False,
                         "has_pest": self.rng.random() < catalog.RULES["pest_chance"],
                         "pest_at": now + duration * 0.35, "pest_cleared": False,
                         "quality": quality, "mutation_chance": mutation_chance, "stolen_count": 0, "stolen_by": []})
            result["message"] = f"种下了{crop['name']}"
        elif action in {"water", "pest", "harvest"}:
            plot = self._plot(state, plot_id)
            if not plot.get("crop_id"):
                raise FarmError("这块灵田还没有种植")
            crop = self._crop(plot["crop_id"])
            if action == "water":
                if now >= plot["mature_at"]:
                    raise FarmError("灵植已经成熟，可以收获了")
                if plot["watered"]:
                    raise FarmError("这一茬已经浇过灵泉")
                plot["watered"] = True
                plot["mature_at"] = max(now, plot["mature_at"] - plot["duration"] * catalog.RULES["water_time_reduction"])
                result["message"] = "灵泉滋养，生长时间缩短了"
            elif action == "pest":
                if not plot["has_pest"] or plot["pest_cleared"] or now < plot["pest_at"]:
                    raise FarmError("这株灵植暂时没有虫害")
                plot["pest_cleared"] = True
                result["message"] = "已驱散害虫，保住了收成"
            else:
                if now < plot["mature_at"]:
                    raise FarmError("灵植尚未成熟")
                quantity = self._yield(plot, now) - plot["stolen_count"]
                quality = plot["quality"]
                self._add_inventory(state["produce"], f"{crop['id']}:{quality}", quantity)
                state["xp"] += crop["xp"]
                state["stats"]["harvests"] += 1
                state["stats"]["mutations"] += int(quality != "normal")
                plot.clear()
                plot["plot_id"] = plot_id
                result.update({"message": f"收获{crop['name']} × {quantity}", "crop_id": crop["id"],
                               "quality": quality, "quantity": quantity, "xp_gained": crop["xp"]})
        elif action == "sell":
            crop = self._crop(crop_id)
            self._integer(quantity, 1, 9999, "出售数量")
            if quality not in catalog.QUALITY_MULTIPLIERS:
                raise FarmError("灵植品质不正确")
            self._add_inventory(state["produce"], f"{crop_id}:{quality}", -quantity)
            amount = crop["sale_price"] * catalog.QUALITY_MULTIPLIERS[quality] * quantity
            self._coins(connection, farm, amount, f"出售{crop['name']}({quality})×{quantity}")
            result.update({"message": f"出售{crop['name']}，获得 {amount} 灵石", "coins": amount})
        elif action == "expand":
            upgrade = next((item for item in catalog.LAND_LEVELS if item["plot_count"] == state["unlocked_plots"] + 1), None)
            if upgrade is None:
                raise FarmError("已开辟全部灵田")
            if level < upgrade["required_level"]:
                raise FarmError(f"灵圃达到 {upgrade['required_level']} 级才能扩建")
            self._coins(connection, farm, -upgrade["cost"], f"开辟第{upgrade['plot_count']}块灵田")
            state["unlocked_plots"] += 1
            state["plots"].append({"plot_id": state["unlocked_plots"]})
            result["message"] = f"已开辟第 {state['unlocked_plots']} 块灵田"
        elif action == "upgrade_aura":
            if state["aura_level"] + 1 >= len(catalog.AURA_LEVELS):
                raise FarmError("聚灵阵已升至最高阶")
            upgrade = catalog.AURA_LEVELS[state["aura_level"] + 1]
            if level < upgrade["required_level"]:
                raise FarmError(f"灵圃达到 {upgrade['required_level']} 级才能升级聚灵阵")
            self._coins(connection, farm, -upgrade["cost"], f"聚灵阵升级{upgrade['level']}阶")
            previous = catalog.AURA_LEVELS[state["aura_level"]]["growth_multiplier"]
            state["aura_level"] += 1
            # 已种作物只加速剩余生长期，不能追溯补发产物或重抽变异。
            for plot in state["plots"]:
                if plot.get("crop_id") and plot["mature_at"] > now:
                    ratio = previous / upgrade["growth_multiplier"]
                    plot["mature_at"] = now + (plot["mature_at"] - now) * ratio
                    plot["duration"] *= ratio
                    if plot["pest_at"] > now:
                        plot["pest_at"] = now + (plot["pest_at"] - now) * ratio
            result["message"] = f"聚灵阵升至 {state['aura_level']} 阶，生长更快；新灵种变异机会提高"
        elif action == "steal":
            if target is None or target["user_id"] == farm["user_id"]:
                raise FarmError("不能偷自己的灵植")
            plot = self._plot(target["state"], plot_id)
            if not self._can_steal(target, plot, farm["user_id"], now):
                raise FarmError("这块灵田暂时不能偷取：尚未成熟、处于新手保护或本茬已被采摘")
            today = datetime.fromtimestamp(now, timezone(timedelta(hours=8))).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            count = connection.execute("SELECT count(*) FROM farm_events WHERE actor_id=? AND action='steal' AND created_at>=?",
                                       (farm["user_id"], today)).fetchone()[0]
            if count >= catalog.RULES["steal_daily_limit"]:
                raise FarmError("今日已经偷取 10 次，明天再来吧")
            crop = self._crop(plot["crop_id"])
            self._add_inventory(state["produce"], f"{crop['id']}:{plot['quality']}", 1)
            plot["stolen_count"] += 1
            plot["stolen_by"].append(str(farm["user_id"]))
            state["stats"]["stolen"] += 1
            result.update({"message": f"悄悄摘走了{crop['name']} × 1", "quantity": 1, "crop_id": crop["id"], "quality": plot["quality"]})
        else:
            raise FarmError("不支持这个灵圃操作")
        return result

    def _act(self, profile, action, params, request_id):
        uid = self._user_id(profile["user_id"])
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 100 or not request_id.isascii():
            raise FarmError("请使用有效的操作编号")
        if action != "steal" and params["target_user_id"] is not None:
            raise FarmError("只能对自己的灵圃执行此操作")
        target_id = self._user_id(params["target_user_id"]) if params["target_user_id"] is not None else None
        if action == "steal" and target_id is None:
            raise FarmError("请选择要拜访的道友")
        fingerprint = self._dump({"action": action, **params, "target_user_id": target_id})
        now = self.clock()
        with self._transaction() as connection:
            farm = self._load(connection, uid, now, profile)
            target = self._load(connection, target_id, now) if target_id is not None else None
            previous = connection.execute("SELECT fingerprint, result FROM farm_actions WHERE user_id=? AND request_id=?",
                                          (uid, request_id)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise FarmError("这个操作编号已用于另一项操作，请刷新后重试", 409)
                result = json.loads(previous["result"])
                result["replayed"] = True
            else:
                result = self._apply(connection, farm, action, params, now, target)
                result["balance"] = self._balance(connection, uid)
                self._save(connection, farm, now)
                if target is not None:
                    self._save(connection, target, now)
                event_owner = target["user_id"] if target is not None else uid
                message = f"{farm['username']}：{result['message']}" if target is not None else result["message"]
                self._event(connection, event_owner, uid, action, message, now)
                connection.execute("INSERT INTO farm_actions (user_id, request_id, fingerprint, result, created_at) VALUES (?, ?, ?, ?, ?)",
                                   (uid, request_id, fingerprint, self._dump(result), now))
            response = self._render(connection, target or farm, uid, now)
            result["balance"] = self._balance(connection, uid)
            response["result"] = result
            return response

    async def act(self, profile, action, *, request_id, plot_id=None, crop_id=None,
                  quantity=1, quality="normal", target_user_id=None):
        params = {"plot_id": plot_id, "crop_id": crop_id, "quantity": quantity,
                  "quality": quality, "target_user_id": target_user_id}
        return await asyncio.to_thread(self._act, profile, action, params, request_id)
