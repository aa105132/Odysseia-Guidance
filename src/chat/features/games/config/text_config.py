# -*- coding: utf-8 -*-
"""
抽王八游戏文本配置
所有游戏文本和URL配置都在这里，方便修改和扩展。
通过类和数据类进行结构化管理，提高可读性和可维护性。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional

# -----------------------------------------------------------------------------
# 资源常量区: 统一管理图片URL
# -----------------------------------------------------------------------------


class EmotionImageUrls:
    """统一管理所有代表情绪反应的图片URL资源"""

    HAPPY = "https://cdn.discordapp.com/attachments/1466427893809680560/1466713444177936436/1769761540347.png?ex=697dbed7&is=697c6d57&hm=11c31861ee887dca2aded88a2ee0293a678662fb87aedaddc232e02f0e7e1fb9&"
    SAD = "https://cdn.discordapp.com/attachments/1466427893809680560/1466713838941638767/1769761551080.png?ex=697dbf35&is=697c6db5&hm=f2673b3d04703c2295c400f44700534c589da3814ec6b90264cad32d7e650c3b&"
    NEUTRAL = "https://cdn.discordapp.com/attachments/1466427893809680560/1466714010815823975/1769761543424.png?ex=697dbf5e&is=697c6dde&hm=da73ed38f061025cd4713f2c4a5147f2aba55204f7cb820037b843a69712dc72&"
    SUPER_WIN = "https://cdn.discordapp.com/attachments/1466427893809680560/1466714560764575838/6_1769762394108.png?ex=697dbfe1&is=697c6e61&hm=8f05cf7e0df45fee944db5809710654fea9482080c04a2ac97d16831f7cfdb81&"


class StaticUrls:
    """管理静态的、非情绪化的URL，如AI策略图和游戏结束图"""

    AI_THUMBNAIL_LOW = "https://cdn.discordapp.com/attachments/1466427893809680560/1466714795490410640/cc8ee9da86b68f7e.png?ex=697dc019&is=697c6e99&hm=7785c0143391f83f114a62fd838257b9ddca657645d22919438e8cbc6536ddb0&"
    AI_THUMBNAIL_MEDIUM = "https://cdn.discordapp.com/attachments/1466427893809680560/1466714010815823975/1769761543424.png?ex=697dbf5e&is=697c6dde&hm=da73ed38f061025cd4713f2c4a5147f2aba55204f7cb820037b843a69712dc72&"
    AI_THUMBNAIL_HIGH = "https://cdn.discordapp.com/attachments/1466427893809680560/1466713838941638767/1769761551080.png?ex=697dbf35&is=697c6db5&hm=f2673b3d04703c2295c400f44700534c589da3814ec6b90264cad32d7e650c3b&"
    AI_THUMBNAIL_SUPER = "https://cdn.discordapp.com/attachments/1466427893809680560/1466714560764575838/6_1769762394108.png?ex=697dbfe1&is=697c6e61&hm=8f05cf7e0df45fee944db5809710654fea9482080c04a2ac97d16831f7cfdb81&"
    AI_WIN_THUMBNAIL = "https://cdn.discordapp.com/attachments/1466427893809680560/1466715224328507463/1769761542908.png?ex=697dc07f&is=697c6eff&hm=75aabeda0ec41012bba387a5b79150d4b600b21a33e92a1c535f02aa9e621f0b&"


# -----------------------------------------------------------------------------
# AI反应文本池: 根据您的建议，创建可复用的情绪反应池
# -----------------------------------------------------------------------------


@dataclass
class Reaction:
    """定义一个反应，包含一组文本和一个图片URL"""

    texts: List[str]
    image_url: str


class ReactionPool:
    """
    存储所有可复用的AI情绪反应。
    这样可以有效防止玩家通过记忆特定文本来预测AI行为。
    """

    # --- 核心反应池 (根据用户建议，用于选择阶段) ---
    ENCOURAGE_SELECTION = Reaction(
        texts=[
            "嘿嘿!对,就抽这个!",
            "嘿嘿，这个“好运”现在是你的了！",
            "**计划通！**",
            "快抽吧快抽吧！那张牌对我来说是个大麻烦！",
            "相信我，这张牌绝对不是你想看到的那张！",
            "嘿嘿，别犹豫了，就是它！",
        ],
        image_url=EmotionImageUrls.HAPPY,
    )
    DISCOURAGE_SELECTION = Reaction(
        texts=[
            "不！别抽这个...！",
            "呜呜呜...不许拿这张啦...",
            "别...别抽那张！",
            "可恶,不许抽这张!",
            "这张不可以啦!",
        ],
        image_url=EmotionImageUrls.SAD,
    )

    # --- 其他情境的反应 ---
    # 抽牌后的真实反应
    DRAWN_GHOST_HAPPY = Reaction(
        texts=[
            "太好了！你终于把它抽走了！",
            "嘿嘿，这张烫手的山芋现在是你的了！祝你好运哦～",
            "**计划通！**",
        ],
        image_url=EmotionImageUrls.HAPPY,
    )
    DRAWN_SAFE_SAD = Reaction(
        texts=["可恶，我的计划...", "居然让你抽到这张了，可恶啊！", "哼！"],
        image_url=EmotionImageUrls.SAD,
    )
    AI_DRAWN_GHOST_SAD = Reaction(
        texts=[
            "怎么会这样？！我算错了吗？",
            "不——！我的完美计划！",
            "呜...我居然抽到了这张牌...",
        ],
        image_url=EmotionImageUrls.SAD,
    )
    AI_DRAWN_SAFE_HAPPY = Reaction(
        texts=["嘿嘿，安全上垒！", "哦耶!抽到安全牌啦!", "哼哼,一切都在计划之中!"],
        image_url=EmotionImageUrls.HAPPY,
    )

    # 取消选择后的反应
    CANCELLED_GHOST_DISAPPOINTED = Reaction(
        texts=[
            "哎，怎么不抽了？就差一点了...",
            "真可惜...",
            "改变主意了吗？再考虑一下嘛。",
        ],
        image_url=EmotionImageUrls.SAD,
    )
    CANCELLED_SAFE_RELIEVED = Reaction(
        texts=[
            "改变主意了？太好了。",
            "嗯，谨慎一点总是好的。",
            "好的，你决定不抽这张了。",
        ],
        image_url=EmotionImageUrls.NEUTRAL,
    )
    CANCELLED_GHOST_FAKE_RELIEVED = Reaction(
        texts=["还好你没抽，呼，吓我一跳。", "嘿嘿，这就对啦。"],
        image_url=EmotionImageUrls.HAPPY,
    )
    CANCELLED_SAFE_FAKE_DISAPPOINTED = Reaction(
        texts=[
            "什么嘛，好可惜，差点就把那张牌送出去了。",
            "啊啊啊啊可恶，就差一点点！",
            "真可惜啊...",
        ],
        image_url=EmotionImageUrls.SAD,
    )

    # 特殊情况反应
    DECEPTION_EXPOSED = Reaction(
        texts=["哼，居然没上当...", "你居然识破了我的小把戏！", "切，真没意思。"],
        image_url=EmotionImageUrls.NEUTRAL,
    )
    DECEPTION_FAILED = Reaction(
        texts=[
            "什...什么？！你居然看穿了我的计谋！",
            "不可能！我的演技应该天衣无缝才对！",
            "呃...好吧，算你厉害...",
            "呜...被你看穿了...",
            "失败了...",
            "哼，别得意！",
        ],
        image_url=EmotionImageUrls.SAD,
    )
    PLAYER_LOST_WIN = Reaction(
        texts=[
            "嘿嘿，一切都在我的计划之中！",
            "杂鱼!是我赢了哦!",
            "**胜利的方程式，完成了!**",
        ],
        image_url=EmotionImageUrls.SUPER_WIN,
    )
    PLAYER_LOST_CHEATING = Reaction(
        texts=[
            "我...我才没输呢！这局不算！",
            "哼，刚刚是你看错了，重来！",
            "不算不算，这局不算！是你作弊！",
        ],
        image_url=EmotionImageUrls.SAD,
    )


# -----------------------------------------------------------------------------
# 主配置类: TextConfig
# -----------------------------------------------------------------------------


@dataclass
class TextConfig:
    """封装所有游戏相关的文本和URL配置"""

    # --- General Game Texts ---
    GHOST_CARD_DESCRIPTION: str = "和我玩一场紧张刺激的抽鬼牌游戏吧！"
    GHOST_CARD_ALREADY_STARTED: str = "你已经在玩一局抽鬼牌游戏了！"
    GHOST_CARD_NOT_ENOUGH_COINS: str = "你的奥德赛币不足 {bet_amount} 哦。"

    # --- Blackjack Game Texts ---
    BLACKJACK_DESCRIPTION: str = "和我玩一场紧张刺激的21点游戏吧！"
    BLACKJACK_ALREADY_STARTED: str = "你已经在玩一局21点游戏了！"
    BLACKJACK_NO_GAME_FOUND: str = "没有找到你正在进行的游戏。"
    BLACKJACK_PLAYER_BUST: str = (
        "你的点数超过21点，爆牌了！你输掉了 {bet_amount} 奥德赛币。"
    )
    BLACKJACK_DEALER_BUST: str = "庄家爆牌了！你赢得了 {bet_amount} 奥德赛币！"
    BLACKJACK_PLAYER_WIN: str = "恭喜！你的点数更高，你赢得了 {bet_amount} 奥德赛币！"
    BLACKJACK_DEALER_WIN: str = (
        "很遗憾，庄家的点数更高。你输掉了 {bet_amount} 奥德赛币。"
    )
    BLACKJACK_PUSH: str = "平局！你的赌注已退回。"

    @dataclass
    class Opening:
        """开局阶段的文本和资源"""

        betting: List[str] = field(
            default_factory=lambda: [
                "“想和我玩牌吗？先让我看看你的诚意吧。”月月微笑着，指了指桌上的筹码。",
                "“风险与回报并存哦～”她晃了晃手中的卡牌，“你准备好下注了吗？”",
                "“这场牌局的入场券，就是你的勇气和灵石。”月月的眼神闪烁着期待。",
                "“别紧张，只是个小游戏而已。”她轻描淡写地说，“……但输了可是要惩罚的哦。”",
                "“让我看看你的运气如何。下注吧，挑战者。”",
            ]
        )
        ai_strategy_text: Dict[str, str] = field(
            default_factory=lambda: {
                "LOW": "*你看到月月眨巴着眼睛，一副还没睡醒的样子。感觉这局应该不难？*",
                "MEDIUM": "*你注意到月月托着下巴，眼神变得专注起来。看来她开始认真了。*",
                "HIGH": "*月月目光如炬地盯着你，嘴角带着一抹神秘的微笑。一场挑战即将开始。*",
                "SUPER": "*你感到一股强烈的压迫感，只见月月眼中闪烁着数据流的光芒，她似乎进入了超级模式！*",
            }
        )
        ai_strategy_thumbnail: Dict[str, str] = field(
            default_factory=lambda: {
                "LOW": StaticUrls.AI_THUMBNAIL_LOW,
                "MEDIUM": StaticUrls.AI_THUMBNAIL_MEDIUM,
                "HIGH": StaticUrls.AI_THUMBNAIL_HIGH,
                "SUPER": StaticUrls.AI_THUMBNAIL_SUPER,
            }
        )

    @dataclass
    class GameUI:
        """游戏界面和流程中的UI文本"""

        game_over_title: str = "🎉 游戏结束啦 🎉"
        player_hand: str = "你的手牌"
        ai_hand: str = "月月的手牌"
        cards_count: str = "张牌"
        waiting_ai: str = "*月月正在思考要抽哪张牌...*"
        ai_win_title: str = "月月获胜啦！"
        ai_win_thumbnail: str = StaticUrls.AI_WIN_THUMBNAIL

    @dataclass
    class ConfirmModal:
        """确认抽牌弹窗的文本"""

        title: str = "确认抽牌"
        special_card_warning: str = (
            "**这张牌感觉有点不一样...** 说不定是关键牌哦，确定要抽吗？"
        )
        normal_card_confirm: str = "确定要抽这张牌吗？牌面: {}"
        confirm_button: str = "确认抽这张"
        cancel_button: str = "我再想想"

    @dataclass
    class Errors:
        """所有错误消息"""

        game_ended: str = "游戏已经结束啦，或者找不到这个游戏了。"
        not_your_turn: str = "还没轮到你哦，请稍等一下。"
        invalid_card_index: str = "好像没有这张牌呢，再选一次吧。"
        general_error: str = "发生了一个小错误，请稍后再试吧。"
        draw_error: str = "抽牌的时候好像出错了。"
        ai_no_cards: str = "月月手上没牌啦，你不能抽牌哦。"

    @dataclass
    class AIDraw:
        """AI抽玩家牌时的文本"""

        drawing: str = "*月月正在小心翼翼地抽牌...*"
        drawn_card: str = "月月抽到了: {}"
        player_win: str = "太好啦！**你赢了！** 月月抽到了鬼牌呢～"
        ai_win: str = "啊呀，**月月赢了！** 你抽到了鬼牌..."
        back_to_player_turn: str = "又轮到你了！加油哦～"

    @dataclass
    class AIReactions:
        """
        将游戏情境映射到反应池中的情绪。
        游戏逻辑根据情境获取对应的情绪反应，再从反应池中随机选择文本和图片。
        """

        reactions_map: Dict[str, Reaction] = field(
            default_factory=lambda: {
                # 情况1: 玩家选择了牌，但还没确认 (使用新的共享文本池)
                "selected_ghost_real": ReactionPool.ENCOURAGE_SELECTION,
                "selected_ghost_fake": ReactionPool.DISCOURAGE_SELECTION,
                "selected_safe_real": ReactionPool.DISCOURAGE_SELECTION,
                "selected_safe_fake": ReactionPool.ENCOURAGE_SELECTION,
                # 情况2: 玩家取消选择
                "cancelled_ghost_real": ReactionPool.CANCELLED_GHOST_DISAPPOINTED,
                "cancelled_ghost_fake": ReactionPool.CANCELLED_GHOST_FAKE_RELIEVED,
                "cancelled_safe_real": ReactionPool.CANCELLED_SAFE_RELIEVED,
                "cancelled_safe_fake": ReactionPool.CANCELLED_SAFE_FAKE_DISAPPOINTED,
                # 情况3: 玩家识破了AI的欺骗并取消
                "cancelled_deception": ReactionPool.DECEPTION_EXPOSED,
                # 情况4: 玩家确认抽牌后
                "drawn_ghost_real": ReactionPool.DRAWN_GHOST_HAPPY,
                "drawn_ghost_deception_failed": ReactionPool.DECEPTION_FAILED,
                "drawn_safe_real": ReactionPool.DRAWN_SAFE_SAD,
                "drawn_safe_deception_failed": ReactionPool.DECEPTION_EXPOSED,
                # 情况5: 游戏结束
                "player_lost_win": ReactionPool.PLAYER_LOST_WIN,
                "player_lost_cheating": ReactionPool.PLAYER_LOST_CHEATING,
                # 情况6: AI抽玩家的牌后
                "ai_drawn_ghost": ReactionPool.AI_DRAWN_GHOST_SAD,
                "ai_drawn_safe": ReactionPool.AI_DRAWN_SAFE_HAPPY,
            }
        )

    # 实例化所有配置部分
    opening: Opening = field(default_factory=Opening)
    game_ui: GameUI = field(default_factory=GameUI)
    confirm_modal: ConfirmModal = field(default_factory=ConfirmModal)
    errors: Errors = field(default_factory=Errors)
    ai_draw: AIDraw = field(default_factory=AIDraw)
    ai_reactions: AIReactions = field(default_factory=AIReactions)
    static_urls: StaticUrls = field(default_factory=StaticUrls)


# 创建一个全局实例，方便其他模块导入和使用
# 使用方式: from src.games.config.text_config import text_config
# 调用: text_config.opening.betting
text_config = TextConfig()


GHOST_CARD_IMAGE_URL_FIELDS = (
    "ghost_emotion_happy_url",
    "ghost_emotion_sad_url",
    "ghost_emotion_neutral_url",
    "ghost_emotion_super_win_url",
    "ghost_ai_thumbnail_low_url",
    "ghost_ai_thumbnail_medium_url",
    "ghost_ai_thumbnail_high_url",
    "ghost_ai_thumbnail_super_url",
    "ghost_ai_win_thumbnail_url",
)


def get_ghost_card_image_urls() -> Dict[str, str]:
    """读取抽鬼牌相关的当前图片 URL 配置。"""
    return {
        "ghost_emotion_happy_url": EmotionImageUrls.HAPPY,
        "ghost_emotion_sad_url": EmotionImageUrls.SAD,
        "ghost_emotion_neutral_url": EmotionImageUrls.NEUTRAL,
        "ghost_emotion_super_win_url": EmotionImageUrls.SUPER_WIN,
        "ghost_ai_thumbnail_low_url": StaticUrls.AI_THUMBNAIL_LOW,
        "ghost_ai_thumbnail_medium_url": StaticUrls.AI_THUMBNAIL_MEDIUM,
        "ghost_ai_thumbnail_high_url": StaticUrls.AI_THUMBNAIL_HIGH,
        "ghost_ai_thumbnail_super_url": StaticUrls.AI_THUMBNAIL_SUPER,
        "ghost_ai_win_thumbnail_url": StaticUrls.AI_WIN_THUMBNAIL,
    }


def apply_ghost_card_image_urls(
    *,
    ghost_emotion_happy_url: Optional[str] = None,
    ghost_emotion_sad_url: Optional[str] = None,
    ghost_emotion_neutral_url: Optional[str] = None,
    ghost_emotion_super_win_url: Optional[str] = None,
    ghost_ai_thumbnail_low_url: Optional[str] = None,
    ghost_ai_thumbnail_medium_url: Optional[str] = None,
    ghost_ai_thumbnail_high_url: Optional[str] = None,
    ghost_ai_thumbnail_super_url: Optional[str] = None,
    ghost_ai_win_thumbnail_url: Optional[str] = None,
) -> Dict[str, str]:
    """
    将抽鬼牌图片 URL 覆盖到运行时配置，并同步刷新依赖这些 URL 的映射。
    传入 None 表示不改动该项；空字符串表示显式清空。
    """
    if ghost_emotion_happy_url is not None:
        EmotionImageUrls.HAPPY = ghost_emotion_happy_url
    if ghost_emotion_sad_url is not None:
        EmotionImageUrls.SAD = ghost_emotion_sad_url
    if ghost_emotion_neutral_url is not None:
        EmotionImageUrls.NEUTRAL = ghost_emotion_neutral_url
    if ghost_emotion_super_win_url is not None:
        EmotionImageUrls.SUPER_WIN = ghost_emotion_super_win_url

    if ghost_ai_thumbnail_low_url is not None:
        StaticUrls.AI_THUMBNAIL_LOW = ghost_ai_thumbnail_low_url
    if ghost_ai_thumbnail_medium_url is not None:
        StaticUrls.AI_THUMBNAIL_MEDIUM = ghost_ai_thumbnail_medium_url
    if ghost_ai_thumbnail_high_url is not None:
        StaticUrls.AI_THUMBNAIL_HIGH = ghost_ai_thumbnail_high_url
    if ghost_ai_thumbnail_super_url is not None:
        StaticUrls.AI_THUMBNAIL_SUPER = ghost_ai_thumbnail_super_url
    if ghost_ai_win_thumbnail_url is not None:
        StaticUrls.AI_WIN_THUMBNAIL = ghost_ai_win_thumbnail_url

    # 同步反应池引用的图片 URL（Reaction 对象在模块加载时已实例化）
    ReactionPool.ENCOURAGE_SELECTION.image_url = EmotionImageUrls.HAPPY
    ReactionPool.DISCOURAGE_SELECTION.image_url = EmotionImageUrls.SAD
    ReactionPool.DRAWN_GHOST_HAPPY.image_url = EmotionImageUrls.HAPPY
    ReactionPool.DRAWN_SAFE_SAD.image_url = EmotionImageUrls.SAD
    ReactionPool.AI_DRAWN_GHOST_SAD.image_url = EmotionImageUrls.SAD
    ReactionPool.AI_DRAWN_SAFE_HAPPY.image_url = EmotionImageUrls.HAPPY
    ReactionPool.CANCELLED_GHOST_DISAPPOINTED.image_url = EmotionImageUrls.SAD
    ReactionPool.CANCELLED_SAFE_RELIEVED.image_url = EmotionImageUrls.NEUTRAL
    ReactionPool.CANCELLED_GHOST_FAKE_RELIEVED.image_url = EmotionImageUrls.HAPPY
    ReactionPool.CANCELLED_SAFE_FAKE_DISAPPOINTED.image_url = EmotionImageUrls.SAD
    ReactionPool.DECEPTION_EXPOSED.image_url = EmotionImageUrls.NEUTRAL
    ReactionPool.DECEPTION_FAILED.image_url = EmotionImageUrls.SAD
    ReactionPool.PLAYER_LOST_WIN.image_url = EmotionImageUrls.SUPER_WIN
    ReactionPool.PLAYER_LOST_CHEATING.image_url = EmotionImageUrls.SAD

    # 同步运行时实例中依赖静态 URL 的映射
    text_config.opening.ai_strategy_thumbnail = {
        "LOW": StaticUrls.AI_THUMBNAIL_LOW,
        "MEDIUM": StaticUrls.AI_THUMBNAIL_MEDIUM,
        "HIGH": StaticUrls.AI_THUMBNAIL_HIGH,
        "SUPER": StaticUrls.AI_THUMBNAIL_SUPER,
    }
    text_config.game_ui.ai_win_thumbnail = StaticUrls.AI_WIN_THUMBNAIL

    return get_ghost_card_image_urls()
