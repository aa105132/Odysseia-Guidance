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
QUALITY_MULTIPLIERS = {"normal": 1, "spirit": 2, "celestial": 5}
RULES = {
    "starter_plots": 3, "starter_seed_id": "huangjing", "starter_seeds": 6,
    "water_time_reduction": 0.10, "base_mutation_chance": 0.04,
    "celestial_mutation_chance": 0.004, "pest_chance": 0.25,
    "newcomer_protection_seconds": 86400, "steal_daily_limit": 10,
    "steal_per_harvest_limit": 1,
    "quality_multipliers": QUALITY_MULTIPLIERS,
    "pricing_note": "名称与稀有度参考《凡人修仙传》灵草资料；灵种、售价、成长时间及变异为游戏改编，并非原著定价。",
    "care_note": "每茬浇水一次，缩短10%基础生长期；虫害减产1份，除虫可恢复。成熟后不会枯萎。",
    "steal_note": "新灵圃24小时保护；每人每日最多偷10次；同一茬所有访客合计最多偷1份。无虫留给主人至少3份，有虫未处理则至少2份。",
}


def farm_level(xp):
    return sum(xp >= threshold for threshold in LEVEL_XP)


def get_catalog():
    return {"crops": [dict(crop) for crop in CROPS],
            "land_levels": [dict(level) for level in LAND_LEVELS],
            "aura_levels": [dict(level) for level in AURA_LEVELS],
            "rules": dict(RULES), "level_xp": list(LEVEL_XP)}
