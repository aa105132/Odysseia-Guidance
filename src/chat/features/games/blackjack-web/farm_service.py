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

    @staticmethod
    def _extend_state(state):
        # 旧灵圃按需补齐 JSON 字段，不做强制数据迁移。
        state.setdefault("pets", {})
        state.setdefault("equipped_pet", None)
        state.setdefault("items", {})
        state["stats"].setdefault("guarded", 0)
        state["stats"].setdefault("caught", 0)
        state.setdefault("pills", {})
        state["stats"].setdefault("refines", 0)
        state["stats"].setdefault("pills_made", 0)
        state["stats"].setdefault("explosions", 0)
        state.setdefault("furnace", None)
        state.setdefault("alchemy_xp", 0)

    @staticmethod
    def _duration_label(seconds):
        seconds = max(0, int(-(-seconds // 1)))
        minutes, remainder = divmod(seconds, 60)
        if minutes and remainder:
            return f"{minutes} 分 {remainder} 秒"
        return f"{minutes} 分钟" if minutes else f"{remainder} 秒"

    @staticmethod
    def _furnace_view(state, now):
        furnace = state.get("furnace")
        if not furnace:
            return None
        # 炉中结果开炉时已由服务端抽定，出炉前不下发，前端只拿到进度。
        view = {"recipe_id": furnace["recipe_id"], "fire": furnace["fire"], "quality": furnace["quality"],
                "catalyst": furnace.get("catalyst"), "started_at": furnace["started_at"],
                "ready_at": furnace["ready_at"], "remaining_seconds": max(0, furnace["ready_at"] - now),
                "ready": now >= furnace["ready_at"]}
        for key in ("furnace_level", "alchemy_xp", "bonus_pills"):
            if key in furnace:
                # 固定经验/炉级加成与成败无关，可以公开；旧炉不伪造新规则快照。
                view[key] = furnace[key]
        if "yield_outcomes" in furnace:
            # 公开整张可能产量表，绝不公开本炉抽中的数量或档位。
            view["yield_outcomes"] = catalog.json_copy(furnace["yield_outcomes"])
        else:
            view["yield_quantity"] = furnace.get("yield_quantity", 1)
        return view

    def _load(self, connection, uid, now, profile=None):
        row = connection.execute("SELECT * FROM farm_profiles WHERE user_id = ?", (uid,)).fetchone()
        if row is None:
            if profile is None:
                raise FarmError("这位道友还没有开辟灵圃", 404)
            state = {"xp": 0, "aura_level": 0, "unlocked_plots": 3, "created_at": now,
                     "seeds": {"huangjing": 6}, "produce": {},
                     "plots": [{"plot_id": index} for index in range(1, 4)],
                     "stats": {"harvests": 0, "stolen": 0, "earned": 0, "spent": 0, "mutations": 0}}
            self._extend_state(state)
            farm = {"user_id": uid, "username": str(profile.get("username") or uid)[:100],
                    "avatar_url": str(profile.get("avatar_url") or "")[:1000], "state": state}
            # 新账号也可领入门灵种，初始化钱包不会赠送灵石。
            connection.execute("INSERT OR IGNORE INTO user_coins (user_id, balance) VALUES (?, 0)", (uid,))
            self._save(connection, farm, now)
            return farm
        farm = dict(row)
        farm["state"] = json.loads(farm["state"])
        self._extend_state(farm["state"])
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
    def _pet(pet_id):
        pet = catalog.PET_BY_ID.get(pet_id) if isinstance(pet_id, str) else None
        if pet is None:
            raise FarmError("没有这种灵兽")
        return pet

    @staticmethod
    def _item(item_id):
        item = catalog.ITEM_BY_ID.get(item_id) if isinstance(item_id, str) else None
        if item is None:
            raise FarmError("没有这种道具")
        return item

    @staticmethod
    def _recipe(recipe_id):
        recipe = catalog.RECIPE_BY_ID.get(recipe_id) if isinstance(recipe_id, str) else None
        if recipe is None:
            raise FarmError("没有这张丹方")
        return recipe

    @staticmethod
    def _fire(fire_id):
        fire = catalog.FIRE_BY_ID.get(fire_id) if isinstance(fire_id, str) else None
        if fire is None:
            raise FarmError("火候不正确")
        return fire

    @staticmethod
    def _pill_def(pill_id):
        pill = catalog.PILL_BY_ID.get(pill_id) if isinstance(pill_id, str) else None
        if pill is None:
            raise FarmError("没有这种丹药")
        return pill

    @staticmethod
    def _guardian(state, now):
        pet_id = state.get("equipped_pet")
        owned = state.get("pets", {}).get(pet_id)
        pet = catalog.PET_BY_ID.get(pet_id)
        if owned is None or pet is None:
            return None
        until = owned["guard_until"]
        return {"pet_id": pet_id, "name": pet["name"], "guard_chance": pet["guard_chance"],
                "water_bonus": pet["water_bonus"], "active": until > now,
                "guard_until": until, "remaining_seconds": max(0, until - now)}

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
            and str(viewer_id) not in plot.get("theft_attempted_by", [])
            and now >= plot.get("ward_until", 0)
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
                              "dew_used": False, "ward_used": False, "ward_until": None,
                              "ward_active": False, "theft_attempted": False,
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
                "dew_used": plot.get("dew_used", False), "ward_used": plot.get("ward_used", False),
                "ward_until": plot.get("ward_until"), "ward_active": now < plot.get("ward_until", 0),
                "theft_attempted": not own and str(viewer_id) in (plot.get("theft_attempted_by", []) + plot["stolen_by"]),
            })
        inventory = {"seeds": [], "produce": [], "items": [], "pills": []}
        if own:
            inventory["items"] = [{"item_id": item_id, "quantity": quantity} for item_id, quantity in state["items"].items()]
            inventory["seeds"] = [{"crop_id": cid, "quantity": quantity} for cid, quantity in state["seeds"].items()]
            for key, quantity in state["produce"].items():
                cid, quality = key.split(":")
                inventory["produce"].append({"crop_id": cid, "quality": quality, "quantity": quantity,
                                              "unit_price": self._crop(cid)["sale_price"] * catalog.QUALITY_MULTIPLIERS[quality]})
            for key, quantity in state["pills"].items():
                pid, grade = key.split(":")
                inventory["pills"].append({"pill_id": pid, "grade": grade, "quantity": quantity,
                                           "unit_price": catalog.pill_price(pid, grade)})
        activity = [dict(row) for row in connection.execute(
            """SELECT event_id, action, message, created_at FROM farm_events WHERE owner_id = ?
            AND (? OR action NOT IN ('buy_pet', 'equip_pet', 'buy_item', 'use_item', 'caught', 'refine', 'collect_pill', 'sell_pill'))
            ORDER BY event_id DESC LIMIT 20""",
            (farm["user_id"], own)).fetchall()]
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
            "furnace": self._furnace_view(state, now) if own else None,
            "alchemy": catalog.alchemy_progress(state["alchemy_xp"]) if own else None,
            "guardian": self._guardian(state, now),
            "pets": [{"pet_id": pet_id, "equipped": state["equipped_pet"] == pet_id,
                      "guard_until": pet_state["guard_until"],
                      "remaining_seconds": max(0, pet_state["guard_until"] - now),
                      "active": pet_state["guard_until"] > now}
                     for pet_id, pet_state in state["pets"].items()] if own else [],
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
        if action in {"buy_pet", "equip_pet"}:
            pet = self._pet(params.get("pet_id"))
            self._integer(quantity, 1, 1, "灵兽数量")
            pet_id = pet["id"]
            if action == "buy_pet":
                if pet_id in state["pets"]:
                    raise FarmError("已经拥有这只灵兽")
                if level < pet["unlock_level"]:
                    raise FarmError(f"灵圃达到 {pet['unlock_level']} 级才能结缘{pet['name']}")
                self._coins(connection, farm, -pet["price"], f"结缘{pet['name']}")
                state["pets"][pet_id] = {"guard_until": now + catalog.RULES["pet_food_seconds"]}
                if state["equipped_pet"] is None:
                    state["equipped_pet"] = pet_id
                result["message"] = f"与{pet['name']}结缘，附赠24小时守护"
            else:
                if pet_id not in state["pets"]:
                    raise FarmError("尚未拥有这只灵兽")
                state["equipped_pet"] = pet_id
                result["message"] = f"{pet['name']}开始照看灵圃"
            result["pet_id"] = pet_id
        elif action == "buy_item":
            item = self._item(params.get("item_id"))
            self._integer(quantity, 1, 99, "购买数量")
            if level < item["unlock_level"]:
                raise FarmError(f"灵圃达到 {item['unlock_level']} 级才能购买{item['name']}")
            self._coins(connection, farm, -item["price"] * quantity, f"购买{item['name']}×{quantity}")
            self._add_inventory(state["items"], item["id"], quantity)
            result.update({"message": f"买入{item['name']} × {quantity}", "item_id": item["id"], "quantity": quantity})
        elif action == "use_item":
            item = self._item(params.get("item_id"))
            self._integer(quantity, 1, 1, "使用数量")
            item_id = item["id"]
            if item_id == "pet_food":
                if plot_id is not None:
                    raise FarmError("灵兽口粮不需要指定灵田")
                guardian = self._guardian(state, now)
                if guardian is None:
                    raise FarmError("请先结缘并装备一只灵兽")
                until = max(now, guardian["guard_until"]) + catalog.RULES["pet_food_seconds"]
                if until > now + catalog.RULES["pet_guard_max_seconds"]:
                    raise FarmError("灵兽最多积攒72小时守护，晚些再喂吧")
                self._add_inventory(state["items"], item_id, -1)
                state["pets"][guardian["pet_id"]]["guard_until"] = until
                result.update({"pet_id": guardian["pet_id"], "message": f"{guardian['name']}吃饱啦，守护延长24小时"})
            else:
                plot = self._plot(state, plot_id)
                if not plot.get("crop_id"):
                    raise FarmError("这块灵田还没有种植")
                if item_id == "spirit_dew":
                    if now >= plot["mature_at"]:
                        raise FarmError("灵植已经成熟，无需使用灵露")
                    if plot.get("dew_used", False):
                        raise FarmError("这一茬已经使用过灵露")
                    self._add_inventory(state["items"], item_id, -1)
                    plot["dew_used"] = True
                    plot["mature_at"] = max(now, plot["mature_at"] - plot["duration"] * catalog.RULES["spirit_dew_reduction"])
                    result["message"] = "灵露滋养，生长时间进一步缩短了"
                else:
                    if plot.get("ward_used", False):
                        raise FarmError("这一茬已经使用过护田符")
                    self._add_inventory(state["items"], item_id, -1)
                    plot.update({"ward_used": True, "ward_until": now + catalog.RULES["ward_seconds"]})
                    result["message"] = "护田符已生效，这块灵田受到1小时保护"
            result.update({"item_id": item_id, "quantity": 1})
        elif action == "buy_seed":
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
                         "quality": quality, "mutation_chance": mutation_chance, "stolen_count": 0, "stolen_by": [],
                         "theft_attempted_by": [], "dew_used": False, "ward_used": False})
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
                guardian = self._guardian(state, now)
                bonus = guardian["water_bonus"] if guardian and guardian["active"] else 0
                reduction = catalog.RULES["water_time_reduction"] + bonus
                plot["mature_at"] = max(now, plot["mature_at"] - plot["duration"] * reduction)
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
                raise FarmError("这块灵田暂时不能偷取：尚未成熟、处于保护、本茬已尝试或已被采摘")
            today = datetime.fromtimestamp(now, timezone(timedelta(hours=8))).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            count = connection.execute("SELECT count(*) FROM farm_events WHERE actor_id=? AND action IN ('steal','steal_caught') AND created_at>=?",
                                       (farm["user_id"], today)).fetchone()[0]
            if count >= catalog.RULES["steal_daily_limit"]:
                raise FarmError("今日已经偷取 10 次，明天再来吧")
            crop = self._crop(plot["crop_id"])
            # 每次偷取无论成败都固定这次尝试；回执重放不会重新抽概率。
            plot.setdefault("theft_attempted_by", []).append(str(farm["user_id"]))
            guardian = self._guardian(target["state"], now)
            if guardian and guardian["active"] and self.rng.random() < guardian["guard_chance"]:
                state["stats"]["caught"] += 1
                target["state"]["stats"]["guarded"] += 1
                result.update({"message": f"被{guardian['name']}抓住啦，这次没能摘走灵植", "caught": True,
                               "quantity": 0, "pet_id": guardian["pet_id"], "crop_id": crop["id"]})
                return result
            self._add_inventory(state["produce"], f"{crop['id']}:{plot['quality']}", 1)
            plot["stolen_count"] += 1
            plot["stolen_by"].append(str(farm["user_id"]))
            state["stats"]["stolen"] += 1
            result.update({"message": f"悄悄摘走了{crop['name']} × 1", "caught": False, "quantity": 1, "crop_id": crop["id"], "quality": plot["quality"]})
        elif action == "refine":
            recipe = self._recipe(params.get("recipe_id"))
            fire = self._fire(params.get("fire"))
            self._integer(quantity, 1, 1, "开炉次数")
            if quality not in catalog.QUALITY_MULTIPLIERS:
                raise FarmError("灵植品质不正确")
            if level < recipe["unlock_level"]:
                raise FarmError(f"灵圃达到 {recipe['unlock_level']} 级才能炼制{recipe['name']}")
            if state["furnace"] is not None:
                raise FarmError("丹炉里还有一炉丹药，先收丹再开炉")
            catalyst = self._crop(crop_id) if crop_id is not None else None
            materials = dict(recipe["materials"])
            if catalyst is not None:
                materials[catalyst["id"]] = materials.get(catalyst["id"], 0) + 1
            for cid, count in materials.items():
                if state["produce"].get(f"{cid}:{quality}", 0) < count:
                    raise FarmError(f"{self._crop(cid)['name']}不足，需要 {count} 份")
            for cid, count in materials.items():
                self._add_inventory(state["produce"], f"{cid}:{quality}", -count)
            odds = catalog.refine_odds(recipe, fire["id"], catalyst["id"] if catalyst else None)
            progression = catalog.alchemy_progress(state["alchemy_xp"])
            seconds = catalog.alchemy_duration(recipe, state["alchemy_xp"])
            yields = catalog.alchemy_yield_outcomes(recipe, progression["bonus_pills"])
            # 药材先扣，结果开炉时由服务端抽定并封在炉中；回执重放不会重抽，出炉前也不下发结果。
            roll = self.rng.random()
            state["stats"]["refines"] += 1
            if roll < odds["success"]:
                grades = catalog.grade_odds(quality, odds["balance"] == 0)
                grade_roll = self.rng.random()
                grade = ("high" if grade_roll < grades["high"]
                         else "mid" if grade_roll < round(grades["high"] + grades["mid"], 4) else "low")
                sealed = {"pill_id": recipe["pill_id"], "grade": grade,
                          **catalog.refine_yield({"yield_outcomes": yields}, self.rng.random())}
            elif roll < round(odds["success"] + odds["explode"], 4):
                sealed = {"outcome": "explode", "pill_id": None, "grade": None, "quantity": 0}
            else:
                sealed = {"outcome": "waste", "pill_id": "waste", "grade": "low", "quantity": 1}
            minimum = min(choice["quantity"] for choice in yields)
            maximum = max(choice["quantity"] for choice in yields)
            state["furnace"] = {"recipe_id": recipe["id"], "fire": fire["id"], "quality": quality,
                                "catalyst": catalyst["id"] if catalyst else None,
                                "yield_outcomes": yields,
                                "furnace_level": progression["level"], "alchemy_xp": recipe["alchemy_xp"],
                                "bonus_pills": progression["bonus_pills"],
                                "started_at": now, "ready_at": now + seconds, **sealed}
            result.update({"recipe_id": recipe["id"], "fire": fire["id"], "heat": odds["balance"],
                           "odds": odds, "materials": materials, "quality": quality,
                           "ready_at": now + seconds, "refine_seconds": seconds,
                           "furnace_level": progression["level"], "alchemy_xp": recipe["alchemy_xp"],
                           "bonus_pills": progression["bonus_pills"],
                           "yield_outcomes": catalog.json_copy(yields),
                           "message": f"丹炉起火，{recipe['name']}约需 {self._duration_label(seconds)}炼成，成功可得 {minimum}～{maximum} 枚，收丹揭晓"})
        elif action == "collect_pill":
            self._integer(quantity, 1, 1, "收丹次数")
            furnace = state["furnace"]
            if furnace is None:
                raise FarmError("丹炉是空的，先开一炉吧")
            if now < furnace["ready_at"]:
                raise FarmError(f"丹药尚未炼成，还需 {self._duration_label(furnace['ready_at'] - now)}")
            recipe = catalog.RECIPE_BY_ID[furnace["recipe_id"]]
            state["furnace"] = None
            outcome = furnace["outcome"]
            result.update({"recipe_id": recipe["id"], "fire": furnace["fire"], "quality": furnace["quality"],
                           "outcome": outcome, "pill_id": furnace["pill_id"], "grade": furnace["grade"]})
            if outcome in {"success", "great_success"}:
                # 新炉随机数量早已封存；旧炉按历史固定数量，缺字段仍只发一枚。
                produced = furnace["quantity"] if "yield_outcomes" in furnace else furnace.get("yield_quantity", 1)
                self._add_inventory(state["pills"], f"{furnace['pill_id']}:{furnace['grade']}", produced)
                state["stats"]["pills_made"] += produced
                grade_name = catalog.PILL_GRADE_BY_ID[furnace["grade"]]["name"]
                label = "大成功！" if outcome == "great_success" else "炼制成功！"
                result.update({"quantity": produced, "message": f"{label}得{grade_name}{recipe['name']} × {produced}"})
            elif outcome == "explode":
                state["stats"]["explosions"] += 1
                result.update({"quantity": 0, "message": f"炸炉了！炼制{recipe['name']}的药材化为飞灰"})
            else:
                self._add_inventory(state["pills"], "waste:low", 1)
                result.update({"quantity": 1, "message": f"药性失和，{recipe['name']}凝成了一枚废丹"})
            # 完成一炉才记一次熟练度，与收丹、事件、幂等回执同事务提交。
            # 部署前尚未领取的旧炉按该丹方获得一次经验；历史收丹回执不补发。
            before = catalog.alchemy_progress(state["alchemy_xp"])
            gained = furnace.get("alchemy_xp", recipe["alchemy_xp"])
            state["alchemy_xp"] += gained
            after = catalog.alchemy_progress(state["alchemy_xp"])
            leveled_up = after["level"] > before["level"]
            result.update({"alchemy_xp_gained": gained, "alchemy_level_before": before["level"],
                           "alchemy_level": after["level"], "alchemy_leveled_up": leveled_up})
            result["message"] += f"；炼丹经验 +{gained}"
            if leveled_up:
                result["message"] += (f"，丹炉升至 {after['level']} 级·{after['name']}"
                                      f"（后续炼制用时缩短 {100 - after['duration_percent']}%，成功固定多 {after['bonus_pills']} 枚）")
        elif action == "sell_pill":
            pill = self._pill_def(params.get("pill_id"))
            grade = params.get("grade")
            if grade not in catalog.PILL_GRADE_BY_ID or (pill["id"] == "waste" and grade != "low"):
                raise FarmError("丹药品阶不正确")
            key = f"{pill['id']}:{grade}"
            grade_name = catalog.PILL_GRADE_BY_ID[grade]["name"]
            self._integer(quantity, 1, 9999, "出售数量")
            self._add_inventory(state["pills"], key, -quantity)
            amount = catalog.pill_price(pill["id"], grade) * quantity
            self._coins(connection, farm, amount, f"出售{grade_name}{pill['name']}×{quantity}")
            result.update({"message": f"出售{grade_name}{pill['name']}，获得 {amount} 灵石", "coins": amount,
                           "pill_id": pill["id"], "grade": grade, "quantity": quantity})
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
        # 新增的可选字段缺省不写指纹，部署前的幂等回执仍可重放。
        fingerprint_params = {key: value for key, value in params.items()
                              if key not in {"pet_id", "item_id", "recipe_id", "fire", "pill_id", "grade"}
                              or value is not None}
        fingerprint = self._dump({"action": action, **fingerprint_params, "target_user_id": target_id})
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
                event_action = "steal_caught" if action == "steal" and result.get("caught") else action
                self._event(connection, event_owner, uid, event_action, message, now)
                if event_action == "steal_caught":
                    # 偷取者的私有回执事件不重复计入每日偷取额度。
                    self._event(connection, uid, uid, "caught", f"拜访{target['username']}：{result['message']}", now)
                connection.execute("INSERT INTO farm_actions (user_id, request_id, fingerprint, result, created_at) VALUES (?, ?, ?, ?, ?)",
                                   (uid, request_id, fingerprint, self._dump(result), now))
            response = self._render(connection, target or farm, uid, now)
            result["balance"] = self._balance(connection, uid)
            response["result"] = result
            return response

    async def act(self, profile, action, *, request_id, plot_id=None, crop_id=None,
                  quantity=1, quality="normal", target_user_id=None, pet_id=None, item_id=None,
                  recipe_id=None, fire=None, pill_id=None, grade=None):
        params = {"plot_id": plot_id, "crop_id": crop_id, "quantity": quantity,
                  "quality": quality, "target_user_id": target_user_id, "pet_id": pet_id, "item_id": item_id,
                  "recipe_id": recipe_id, "fire": fire, "pill_id": pill_id, "grade": grade}
        return await asyncio.to_thread(self._act, profile, action, params, request_id)
