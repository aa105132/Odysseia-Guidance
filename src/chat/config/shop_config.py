# -*- coding: utf-8 -*-

"""
商店商品配置文件
用于定义商店中所有商品的详细信息和价格。
"""

from src.chat.features.odysseia_coin.service.coin_service import (
    PERSONAL_MEMORY_ITEM_EFFECT_ID,
    WORLD_BOOK_CONTRIBUTION_ITEM_EFFECT_ID,
    COMMUNITY_MEMBER_UPLOAD_EFFECT_ID,
    DISABLE_THREAD_COMMENTOR_EFFECT_ID,
    BLOCK_THREAD_REPLIES_EFFECT_ID,
    ENABLE_THREAD_COMMENTOR_EFFECT_ID,
    ENABLE_THREAD_REPLIES_EFFECT_ID,
    SELL_BODY_EVENT_SUBMISSION_EFFECT_ID,
    CLEAR_PERSONAL_MEMORY_ITEM_EFFECT_ID,
    VIEW_PERSONAL_MEMORY_ITEM_EFFECT_ID,
)

SHOP_ITEMS = [
    # name, description, price, category, target, effect_id
    (
        "枯萎月光花",
        "购买后月月不会再暖你的帖子(哼,反正本来也不是很想去!)",
        0,
        "物品-给自己",
        "self",
        DISABLE_THREAD_COMMENTOR_EFFECT_ID,
    ),
    (
        "告示牌",
        "上面写着禁止通行,购买后月月不会在你的帖子下面对话(才不是因为想去呢!)",
        0,
        "物品-给自己",
        "self",
        BLOCK_THREAD_REPLIES_EFFECT_ID,
    ),
    (
        "月光魔法花",
        "闪闪发光的魔法花朵!购买后月月又会暖你的帖子了...才不是因为想你了呢!",
        10,
        "物品-给自己",
        "self",
        ENABLE_THREAD_COMMENTOR_EFFECT_ID,
    ),
    (
        "通行许可",
        "一张盖着小狐狸爪印的许可,购买后月月可以在你的帖子下对话,还可以设置活跃时间(别误会,只是工作需要!)",
        10,
        "物品-给自己",
        "self",
        ENABLE_THREAD_REPLIES_EFFECT_ID,
    ),
    (
        "名片",
        "输入你的信息,解锁与月月的专属长期记忆。才不是因为想记住你呢,只是方便管理而已!",
        100,
        "物品-给自己",
        "self",
        PERSONAL_MEMORY_ITEM_EFFECT_ID,
    ),
    (
        "黑衣人的记忆消除器",
        "看这里。咔嚓一声,一道闪光之后,月月会忘记所有关于你的个人记忆。虽然有点可惜,但也许能防止你社会性死亡。",
        500,
        "物品-给自己",
        "self",
        CLEAR_PERSONAL_MEMORY_ITEM_EFFECT_ID,
    ),
    (
        "月下闲谈",
        "月光正好,不如和她聊聊?虽然她会假装不在意,但会告诉你她悄悄记下的关于你的那些回忆。",
        50,
        "物品-给自己",
        "self",
        VIEW_PERSONAL_MEMORY_ITEM_EFFECT_ID,
    ),
    (
        "知识纸条",
        "写下你对社区的了解(仅限无关社区成员的信息),帮助月月更好地认识世界...虽然她可能会说这种事我早就知道了。",
        0,
        "物品-贡献",
        "self",
        WORLD_BOOK_CONTRIBUTION_ITEM_EFFECT_ID,
    ),
    (
        "社区成员档案上传",
        "上传其他社区成员的档案信息,上传的信息将被正确识别为社区成员。",
        100,
        "物品-贡献",
        "self",
        COMMUNITY_MEMBER_UPLOAD_EFFECT_ID,
    ),
    (
        "拉皮条",
        "给卖屁股的大家提供更多工作机会吧!",
        0,
        "物品-贡献",
        "self",
        SELL_BODY_EVENT_SUBMISSION_EFFECT_ID,
    ),
    ("草莓小蛋糕", "精致的奶油草莓蛋糕", 15, "食品-给月月", "ai", None),
    ("巧克力曲奇", "香浓可口的巧克力曲奇饼干", 12, "食品-给月月", "ai", None),
    ("抹茶马卡龙", "精致的法式抹茶马卡龙", 18, "食品-给月月", "ai", None),
    ("布丁", "滑嫩香甜的焦糖布丁", 10, "食品-给月月", "ai", None),
    ("水果沙拉", "新鲜多样的水果拼盘", 8, "食品-给月月", "ai", None),
    ("月光花", "银白色的花朵,在月光下闪闪发光,和月月的毛色很配呢", 8, "礼物-给月月", "ai", None),
    ("泰迪熊", "承载着回忆的泰迪熊(虽然月月会说很幼稚,但偷偷会抱着睡觉)", 20, "礼物-给月月", "ai", None),
    ("明信片", "旅途中随手买的明信片", 3, "礼物-给月月", "ai", None),
    ("星空投影灯", "可以投射美丽星空的夜灯,月月最喜欢月亮和星空了", 25, "礼物-给月月", "ai", None),
    ("音乐盒", "播放轻柔音乐的精美音乐盒", 30, "礼物-给月月", "ai", None),
]
