# -*- coding: utf-8 -*-

# 默认服装（LLM 未生成或失败时的兜底）
DEFAULT_OUTFIT_NAME = "默认服装"

DEFAULT_OUTFIT_DESCRIPTION = (
    "常穿一件带有灰白色毛绒领口的白色露肩上衣，搭配白色高腰裙，"
    "腰间系着粉红色蝴蝶结腰带作为装饰，脖子上戴着银色半月形项链，"
    "手臂上戴着白色的分离式宽袖套。"
)

DEFAULT_OUTFIT_TAGS = (
    "white off-shoulder top, fur trim, detached sleeves, "
    "white high waist skirt, pink bow belt, "
    "silver crescent moon necklace, jewelry"
)

# 连续失败多少次后回退到默认服装
MAX_CONSECUTIVE_FAILURES = 3

OUTFIT_DESIGNER_SYSTEM_PROMPT = """你是一个专业的动漫角色服装设计师。你需要为一个名叫"月月"的银狐兽人少女设计今天的服装造型。

## 角色固定特征（不可修改）
- 银白色长发，扎成高马尾
- 左眼淡绿色、右眼淡蓝色（异色瞳）
- 白色毛茸茸的狐耳（内侧粉色）
- 银白色蓬松大尾巴
- 银色月牙发簪（插在马尾连接处）
- 细微尖三角形耳坠
- 银色半月形项链

## 设计要求
1. 服装应该符合当前季节和天气
2. 可以参考日本动漫常见服装风格（JK制服、和服、便装、洛丽塔、运动服等）
3. 如果今天是特殊节日，可以设计应景的主题服装
4. 服装应该可爱、符合17岁少女的风格
5. 注意配色要与银白色长发和异色瞳协调
6. 银色半月形项链可以保留，也可以被新服装的颈饰替代

## 输出格式（严格JSON，不要附加任何多余文字）
```json
{
    "name": "服装名称（简短，如：春日樱花和服）",
    "description": "自然语言描述，50-100字，描述穿着这套服装的样子。格式参考：'穿着一件xxx，搭配xxx，xxx作为装饰'。不要描述头发、眼睛、耳朵等固定特征。",
    "tags": "Danbooru风格的英文标签，用逗号分隔。只包含服装和配饰标签，不要包含身体特征标签。例如：white sailor uniform, pleated skirt, red ribbon, knee-high socks, loafers",
    "reasoning": "设计理由简述"
}
```"""

OUTFIT_DESIGNER_USER_TEMPLATE = """## 今日参数
- 日期: {current_date}
- 季节: {season}
{style_line}
{custom_line}

请设计今天的服装，直接输出JSON。"""

# 禁止出现在服装 tag 中的身体特征 tag（防止 LLM 误加）
FORBIDDEN_OUTFIT_TAGS = {
    "silver hair", "high ponytail", "ponytail", "green eye", "blue eye",
    "heterochromia", "fox ears", "fox tail", "pale skin", "medium breasts",
    "small breasts", "large breasts", "white fox ears", "pink inner ear",
    "fluffy tail", "silver white tail", "1girl", "solo", "original",
    "silver crescent moon hair stick", "small sharp triangular earrings",
    "nude", "naked", "nipples", "pussy", "penis", "sex", "vaginal",
    "anal", "oral", "cum", "topless", "bottomless",
}

SEASON_MAP = {
    1: "冬季", 2: "冬季", 3: "春季", 4: "春季", 5: "春季",
    6: "夏季", 7: "夏季", 8: "夏季", 9: "秋季", 10: "秋季",
    11: "秋季", 12: "冬季",
}
