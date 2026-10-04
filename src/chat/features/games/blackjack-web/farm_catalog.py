"""灵圃的作物与成长路线；灵石价格和成长时间属于游戏设计，并非原著报价。"""

from urllib.parse import quote


def _crop(crop_id, name, tier, level, minutes, seed, sale, xp, description, chapter):
    return {
        "id": crop_id, "name": name, "tier": tier, "unlock_level": level,
        "grow_seconds": minutes * 60, "seed_price": seed, "sale_price": sale,
        "base_yield": 4, "xp": xp, "description": description,
        "source_url": "https://fanren-wiki.pages.dev/" + quote("灵草信息") + "/" + quote(name) + "/",
        "source_chapter": chapter, "icon_key": crop_id,
    }


CROPS = (
    _crop("qixing", "七星草", "凡品", 1, 15, 12, 5, 8,
          "十年以上可作符纸原料，适合初入灵圃练手。", "第137章"),
    _crop("huangjing", "黄精芝", "凡品", 1, 30, 24, 10, 12,
          "常见灵草，药龄越久越珍贵；是灵圃的稳定起步作物。", "第163章"),
    _crop("zihou", "紫猴花", "灵品", 2, 90, 60, 26, 24,
          "筑基丹主药之一。本灵圃以阵法培育灵种，为玩法改编。", "第158章"),
    _crop("yusui", "玉髓芝", "灵品", 3, 180, 130, 58, 42,
          "筑基丹主药之一；原著无种子，游戏以洞天灵种模拟培育。", "第158—159章"),
    _crop("nishang", "霓裳草", "珍品", 4, 360, 280, 126, 72,
          "又名诱妖草，百年展叶，奇香引妖；灵圃以珊瑚灵土适生。", "第395—932章"),
    _crop("tianyuan", "天元果", "珍品", 6, 720, 650, 295, 125,
          "原著中一颗可延寿百年，游戏作为长期培育的珍果。", "第826章"),
    _crop("jinlei", "金雷竹", "地品", 8, 1440, 1500, 685, 220,
          "三大神木之一，长成金雷竹材，是灵圃后期的珍贵收成。", "第826章"),
    _crop("jiuqu", "九曲灵参", "天品", 10, 2160, 3200, 1470, 360,
          "天地灵气所化，凝婴灵物；游戏灵种与短时生长均为洞天改编。", "第433—453章"),
)
CROP_BY_ID = {crop["id"]: crop for crop in CROPS}
LEVEL_XP = (0, 80, 240, 540, 1000, 1800, 3000, 4700, 7000, 10000, 14000, 19000)
LAND_LEVELS = (
    {"plot_count": 4, "required_level": 2, "cost": 180},
    {"plot_count": 5, "required_level": 3, "cost": 420},
    {"plot_count": 6, "required_level": 4, "cost": 900},
    {"plot_count": 7, "required_level": 5, "cost": 1800},
    {"plot_count": 8, "required_level": 6, "cost": 3500},
    {"plot_count": 9, "required_level": 7, "cost": 6500},
    {"plot_count": 10, "required_level": 8, "cost": 11000},
    {"plot_count": 11, "required_level": 9, "cost": 18000},
    {"plot_count": 12, "required_level": 10, "cost": 28000},
)
AURA_LEVELS = tuple(
    {"level": level, "required_level": required, "cost": cost,
     "growth_multiplier": 1 + level * 0.10, "mutation_bonus": level * 0.01}
    for level, required, cost in ((0, 1, 0), (1, 2, 300), (2, 4, 1200),
                                  (3, 6, 4000), (4, 8, 10000), (5, 10, 24000))
)
PETS = (
    {"id": "qingling_fox", "name": "青团灵狐", "price": 300, "unlock_level": 1,
     "guard_chance": 0.25, "water_bonus": 0.0, "icon_key": "qingling_fox",
     "description": "守护期间有25%概率抓住偷菜者，轻巧机灵的灵圃伙伴。"},
    {"id": "mountain_hound", "name": "巡山灵犬", "price": 650, "unlock_level": 2,
     "guard_chance": 0.40, "water_bonus": 0.0, "icon_key": "mountain_hound",
     "description": "守护期间有40%概率抓住偷菜者，擅长巡查田间。"},
    {"id": "dew_crane", "name": "衔露仙鹤", "price": 500, "unlock_level": 3,
     "guard_chance": 0.15, "water_bonus": 0.05, "icon_key": "dew_crane",
     "description": "守护期间有15%概率抓偷；浇水额外缩短5%基础生长期。"},
)
PET_BY_ID = {pet["id"]: pet for pet in PETS}
ITEMS = (
    {"id": "pet_food", "name": "灵兽口粮", "price": 40, "unlock_level": 1,
     "icon_key": "pet_food", "description": "喂给当前灵兽，增加24小时守护，最多积攒72小时。"},
    {"id": "spirit_dew", "name": "灵露", "price": 20, "unlock_level": 1,
     "icon_key": "spirit_dew", "description": "缩短一茬基础生长期的10%，每茬可用一次。"},
    {"id": "ward_talisman", "name": "护田符", "price": 35, "unlock_level": 1,
     "icon_key": "ward_talisman", "description": "保护一块已种植灵田1小时，每茬可用一次。"},
)
ITEM_BY_ID = {item["id"]: item for item in ITEMS}
QUALITY_MULTIPLIERS = {"normal": 1, "spirit": 2, "celestial": 5}
RULES = {
    "starter_plots": 3, "starter_seed_id": "huangjing", "starter_seeds": 6,
    "water_time_reduction": 0.10, "base_mutation_chance": 0.04,
    "celestial_mutation_chance": 0.004, "pest_chance": 0.25,
    "newcomer_protection_seconds": 86400, "steal_daily_limit": 10,
    "steal_per_harvest_limit": 1,
    "pet_food_seconds": 86400, "pet_guard_max_seconds": 259200,
    "spirit_dew_reduction": 0.10, "ward_seconds": 3600,
    "pet_note": "最多装备一只灵兽；购置附24小时守护，喂口粮每份延长24小时，最多剩余72小时。守护过期后灵兽休息。",
    "item_note": "灵露每茬一次，缩短10%基础生长期；护田符每茬一次，保护1小时。灵兽与道具均为灵圃玩法原创。",
    "quality_multipliers": QUALITY_MULTIPLIERS,
    "pricing_note": "名称与稀有度参考《凡人修仙传》灵草资料；灵种、售价、成长时间及变异为游戏改编，并非原著定价。",
    "care_note": "每茬浇水一次，缩短10%基础生长期；虫害减产1份，除虫可恢复。成熟后不会枯萎。",
    "steal_note": "新灵圃24小时保护；每人每日最多偷10次；被灵兽抓住也计入次数，同一人同一茬仅可尝试一次；同一茬所有访客合计最多偷1份。无虫留给主人至少3份，有虫未处理则至少2份。",
}


FIRE_LEVELS = (
    {"id": "wen", "name": "文火", "rank": 0, "description": "火势温和，适合药性娇弱的丹方。"},
    {"id": "zhong", "name": "中火", "rank": 1, "description": "文武相济，多数筑基前后的丹方用它。"},
    {"id": "wu", "name": "武火", "rank": 2, "description": "烈火猛炼，逼出珍药药力，也最容易炸炉。"},
)
FIRE_BY_ID = {fire["id"]: fire for fire in FIRE_LEVELS}
# 药性：负数偏寒、正数偏热；丹方寒热加上药引后越接近零，炉火越稳。
HERB_NATURES = {"qixing": -1, "huangjing": 1, "zihou": 1, "yusui": -1,
                "nishang": 0, "tianyuan": 1, "jinlei": 2, "jiuqu": -1}
NATURE_NAMES = {-2: "大寒", -1: "寒", 0: "平", 1: "热", 2: "大热"}
PILL_GRADES = (
    {"id": "low", "name": "下品", "multiplier": 1.0},
    {"id": "mid", "name": "中品", "multiplier": 2.2},
    {"id": "high", "name": "上品", "multiplier": 6.0},
)
PILL_GRADE_BY_ID = {grade["id"]: grade for grade in PILL_GRADES}


def _pill(pill_id, name, icon, sale, description):
    return {"id": pill_id, "name": name, "icon_key": icon, "sale_price": sale, "description": description}


# 现阶段只开放一、二品的入门丹药，丹药只用于炼制和出售。
# 1级成丹后随机1~4枚，条件期望两枚；每5级固定多一枚，成品售价和基础分布不变。
# 药引额外消耗一份，调和提高稳定性；前端显示含全部材料的平均回收，昂贵药引不保证划算。
PILLS = (
    _pill("xisui", "洗髓丹", "pill-green", 40, "一品丹药，丹色青碧，药铺常年收购。"),
    _pill("huanglong", "黄龙丹", "pill-gold", 64, "二品丹药，丹纹金黄，比洗髓丹更受药铺欢迎。"),
    _pill("yangqi", "养气丹", "pill-blue", 220, "二品丹药，丹气温润，是入门丹方里最值钱的一种。"),
    _pill("waste", "废丹", "pill-waste", 1, "药性冲突或寒热不平凝成的丹渣，只能低价卖掉。"),
)
PILL_BY_ID = {pill["id"]: pill for pill in PILLS}


def _recipe(recipe_id, pill_id, level, fire, materials, seconds, alchemy_xp, note):
    return {"id": recipe_id, "pill_id": pill_id, "name": PILL_BY_ID[pill_id]["name"],
            "unlock_level": level, "fire": fire, "materials": dict(materials),
            "heat": sum(HERB_NATURES[crop_id] * count for crop_id, count in materials.items()),
            "refine_seconds": seconds, "alchemy_xp": alchemy_xp,
            # 以下概率以成丹为条件，数量和品阶独立；普通成功75%，大成功25%。
            "yield_outcomes": [
                {"outcome": "success", "quantity": 1, "probability": 0.375},
                {"outcome": "success", "quantity": 2, "probability": 0.375},
                {"outcome": "great_success", "quantity": 3, "probability": 0.125},
                {"outcome": "great_success", "quantity": 4, "probability": 0.125},
            ], "note": note}


RECIPES = (
    # 炼制时长：丹炉同一时间只炼一炉，越值钱的丹方越久，限制单位时间的灵石产出。
    _recipe("xisui", "xisui", 1, "wen", {"qixing": 2, "huangjing": 1}, 300, 10,
            "七星草为主、黄精芝为辅；丹性偏寒，宜以热性药引调和，文火慢炼。"),
    _recipe("huanglong", "huanglong", 3, "zhong", {"huangjing": 2, "qixing": 1}, 480, 16,
            "黄精芝为主、七星草为辅；丹性偏热，宜以寒性药引调和，中火炼成。"),
    _recipe("yangqi", "yangqi", 4, "zhong", {"zihou": 2, "yusui": 1}, 900, 30,
            "紫猴花为主、玉髓芝为辅；丹性偏热，宜以寒性药引调和，中火炼成。"),
)
RECIPE_BY_ID = {recipe["id"]: recipe for recipe in RECIPES}
ALCHEMY_RULES = {
    # 下标是火候与丹方要求相差的档数。
    "fire_odds": [{"success": 0.80, "explode": 0.04},
                  {"success": 0.50, "explode": 0.12},
                  {"success": 0.20, "explode": 0.35}],
    "balanced_success_bonus": 0.12, "imbalance_success_penalty": 0.10,
    "imbalance_explode_penalty": 0.08,
    "grade_odds": {"normal": {"high": 0.0, "mid": 0.10},
                   "spirit": {"high": 0.05, "mid": 0.85},
                   "celestial": {"high": 0.85, "mid": 0.15}},
    "balanced_high_bonus": {"normal": 0.03, "spirit": 0.10, "celestial": 0.10},
    "note": "主药与辅药定丹方，药引调和寒热，火候对准丹方才稳；成丹后75%普通成功得1~2枚，25%大成功得3~4枚，各档内等概率。"
            "丹炉同一时间只炼一炉。炸炉损失全部药材，失准只得一枚废丹。药引也计成本，平均回收不等于保底收益。",
}


# 炼丹熟练度独立于洞天经验；只由完成一炉并收丹累积，不设灵石升级路径。
ALCHEMY_PROGRESSION = {
    "levels": [
        {"level": level, "name": name, "required_xp": xp,
         "duration_percent": 100 - (level - 1) * 20 // 19, "bonus_pills": level // 5}
        for level, (name, xp) in enumerate([
            ("青玉丹炉", 0), ("温玉丹炉", 100), ("凝灵丹炉", 300), ("赤霞丹炉", 700),
            ("紫霄丹炉", 1500), ("玄纹丹炉", 2800), ("碧焰丹炉", 4500), ("赤阳丹炉", 6800),
            ("流金丹炉", 9800), ("五行丹炉", 13500), ("云鼎丹炉", 18000), ("灵曜丹炉", 23500),
            ("星辉丹炉", 30000), ("月华丹炉", 37500), ("日曜丹炉", 46000), ("太玄丹炉", 56000),
            ("乾元丹炉", 67500), ("混元丹炉", 81000), ("造化丹炉", 98500), ("太清丹炉", 120000),
        ], start=1)
    ],
    "note": "收丹获得炼丹经验，无每日上限，累计达标自动升级丹炉，不消耗灵石。共20级，满级需120000经验。"
            "每炉经验按丹方固定，成丹、废丹、炸炉均可积累，与品阶和数量无关。"
            "每5级成功固定多1枚，最多多4枚；最高缩短20%用时。只影响新开炉，旧炉保持封炉记录。",
}


def alchemy_progress(xp):
    """等级从累计经验推导，不另外存储可购买或篡改的炉级。"""
    levels = ALCHEMY_PROGRESSION["levels"]
    index = max(index for index, level in enumerate(levels) if xp >= level["required_xp"])
    current = levels[index]
    return {"xp": xp, "level": current["level"], "name": current["name"],
            "level_xp": current["required_xp"],
            "next_level_xp": levels[index + 1]["required_xp"] if index + 1 < len(levels) else None,
            "max_level": levels[-1]["level"], "duration_percent": current["duration_percent"],
            "bonus_pills": current["bonus_pills"]}


def alchemy_duration(recipe, xp):
    # 整秒向上取整，前端使用同一公式；只在开炉时计算。
    return max(1, (recipe["refine_seconds"] * alchemy_progress(xp)["duration_percent"] + 99) // 100)


def refine_odds(recipe, fire_id, catalyst_id=None):
    rules = ALCHEMY_RULES
    diff = abs(FIRE_BY_ID[fire_id]["rank"] - FIRE_BY_ID[recipe["fire"]]["rank"])
    base = rules["fire_odds"][diff]
    balance = recipe["heat"] + (HERB_NATURES[catalyst_id] if catalyst_id else 0)
    imbalance = abs(balance)
    success = base["success"] + (rules["balanced_success_bonus"] if imbalance == 0
                                  else -rules["imbalance_success_penalty"] * imbalance)
    explode = base["explode"] + rules["imbalance_explode_penalty"] * imbalance
    success = round(min(0.95, max(0.05, success)), 4)
    explode = round(min(1 - success, min(0.9, max(0.01, explode))), 4)
    return {"success": success, "explode": explode, "waste": round(1 - success - explode, 4),
            "balance": balance, "fire_diff": diff}


def grade_odds(quality, balanced):
    odds = ALCHEMY_RULES["grade_odds"][quality]
    high = round(min(1.0, odds["high"] + (ALCHEMY_RULES["balanced_high_bonus"][quality] if balanced else 0)), 4)
    mid = round(min(odds["mid"], 1 - high), 4)
    return {"high": high, "mid": mid, "low": round(1 - high - mid, 4)}


def alchemy_yield_outcomes(recipe, bonus_pills=0):
    """基础成功分布平移固定炉级增产；不改变档位、概率或目录原值。"""
    return [{**choice, "quantity": choice["quantity"] + bonus_pills}
            for choice in recipe["yield_outcomes"]]


def refine_yield(recipe, roll):
    """独立抽取成丹后的档位与数量，不改变品阶概率。"""
    threshold = 0.0
    for choice in recipe["yield_outcomes"]:
        threshold = round(threshold + choice["probability"], 4)
        if roll < threshold:
            return {"outcome": choice["outcome"], "quantity": choice["quantity"]}
    raise ValueError("成丹产量概率未覆盖抽签值")


def pill_price(pill_id, grade):
    return max(1, round(PILL_BY_ID[pill_id]["sale_price"] * PILL_GRADE_BY_ID[grade]["multiplier"]))


def farm_level(xp):
    return sum(xp >= threshold for threshold in LEVEL_XP)


def get_catalog():
    return {"crops": [dict(crop) for crop in CROPS],
            "pets": [dict(pet) for pet in PETS], "items": [dict(item) for item in ITEMS],
            "land_levels": [dict(level) for level in LAND_LEVELS],
            "aura_levels": [dict(level) for level in AURA_LEVELS],
            "rules": dict(RULES), "level_xp": list(LEVEL_XP),
            "alchemy": {"recipes": [json_copy(recipe) for recipe in RECIPES],
                        "pills": [dict(pill) for pill in PILLS],
                        "fires": [dict(fire) for fire in FIRE_LEVELS],
                        "grades": [dict(grade) for grade in PILL_GRADES],
                        "herb_natures": dict(HERB_NATURES),
                        "nature_names": {str(key): name for key, name in NATURE_NAMES.items()},
                        "rules": json_copy(ALCHEMY_RULES),
                        "progression": json_copy(ALCHEMY_PROGRESSION)}}


def json_copy(value):
    if isinstance(value, dict):
        return {key: json_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_copy(item) for item in value]
    return value
