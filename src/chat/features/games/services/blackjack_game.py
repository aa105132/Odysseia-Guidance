# -*- coding: utf-8 -*-
"""
21点（黑杰克）游戏模块
独立的游戏逻辑，可被Discord UI调用
支持单人模式，使用 ephemeral 消息避免刷屏
"""

import random
import asyncio
from typing import Optional, Dict, List, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum


class GameState(Enum):
    """游戏状态枚举"""
    WAITING_BET = "waiting_bet"          # 等待下注
    WAITING_INSURANCE = "waiting_insurance"  # 等待保险决定
    PLAYER_TURN = "player_turn"          # 玩家回合
    DEALER_TURN = "dealer_turn"          # 庄家回合
    FINISHED = "finished"                # 游戏结束


class GameResult(Enum):
    """游戏结果枚举"""
    PLAYER_WIN = "player_win"        # 玩家获胜
    DEALER_WIN = "dealer_win"        # 庄家获胜
    TIE = "tie"                      # 平局
    PLAYER_BLACKJACK = "blackjack"   # 玩家21点
    PLAYER_BUST = "player_bust"      # 玩家爆牌
    DEALER_BUST = "dealer_bust"      # 庄家爆牌
    PLAYER_SURRENDER = "surrender"   # 玩家投降


@dataclass
class AnimatedResponse:
    """
    带动画效果的响应结构
    用于实现"伪动画"效果：先显示动作提示，延迟后显示结果
    """
    success: bool                    # 操作是否成功
    action_text: str                 # 动作提示文字（如"正在抓牌..."）
    result_text: str                 # 结果文字（加粗显示）
    is_double_win: bool = False      # 是否为加倍获胜（需要双倍加粗）
    delay_seconds: float = 2.0       # 延迟秒数
    secondary_action_text: Optional[str] = None  # 第二阶段动作文字（用于加倍逻辑）
    secondary_delay_seconds: float = 1.0         # 第二阶段延迟
    
    def get_action_display(self) -> str:
        """获取动作阶段的显示文字"""
        return self.action_text
    
    def get_result_display(self) -> str:
        """获取结果阶段的显示文字（带加粗）"""
        if self.is_double_win:
            # 双倍加粗：使用 ***text*** 实现粗斜体，或重复强调
            return f"**『{self.result_text}』**"
        else:
            return f"**{self.result_text}**"


@dataclass
class Card:
    """扑克牌类"""
    suit: str   # 花色: ♠️ ♥️ ♦️ ♣️
    rank: str   # 点数: A, 2-10, J, Q, K
    
    @property
    def value(self) -> int:
        """获取牌面值（A默认为11，后续计算时可能调整为1）"""
        if self.rank in ["J", "Q", "K"]:
            return 10
        elif self.rank == "A":
            return 11
        else:
            return int(self.rank)
    
    def __str__(self) -> str:
        return f"{self.suit}{self.rank}"
    
    def to_emoji(self) -> str:
        """返回带emoji的牌面显示"""
        suit_emoji = {
            "♠️": "♠️", "♥️": "♥️", "♦️": "♦️", "♣️": "♣️"
        }
        return f"{suit_emoji.get(self.suit, self.suit)}{self.rank}"


class Deck:
    """牌组类"""
    SUITS = ["♠️", "♥️", "♦️", "♣️"]
    RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
    
    def __init__(self, num_decks: int = 1):
        """初始化牌组"""
        self.cards: List[Card] = []
        self.num_decks = num_decks
        self.reset()
    
    def reset(self):
        """重置牌组"""
        self.cards = []
        for _ in range(self.num_decks):
            for suit in self.SUITS:
                for rank in self.RANKS:
                    self.cards.append(Card(suit, rank))
        self.shuffle()
    
    def shuffle(self):
        """洗牌"""
        random.shuffle(self.cards)
    
    def draw(self) -> Optional[Card]:
        """抽一张牌"""
        if not self.cards:
            self.reset()
        return self.cards.pop() if self.cards else None
    
    def remaining(self) -> int:
        """剩余牌数"""
        return len(self.cards)


@dataclass
class Hand:
    """手牌类"""
    cards: List[Card] = field(default_factory=list)
    
    def add_card(self, card: Card):
        """添加一张牌"""
        self.cards.append(card)
    
    def get_value(self) -> int:
        """计算手牌点数（自动处理A的值）"""
        value = sum(card.value for card in self.cards)
        aces = sum(1 for card in self.cards if card.rank == "A")
        
        # 如果爆牌且有A，将A从11调整为1
        while value > 21 and aces > 0:
            value -= 10
            aces -= 1
        
        return value
    
    def is_blackjack(self) -> bool:
        """是否为黑杰克（两张牌21点）"""
        return len(self.cards) == 2 and self.get_value() == 21
    
    def is_bust(self) -> bool:
        """是否爆牌"""
        return self.get_value() > 21
    
    def clear(self):
        """清空手牌"""
        self.cards = []
    
    def __str__(self) -> str:
        return " ".join(card.to_emoji() for card in self.cards)
    
    def to_display(self, hide_first: bool = False) -> str:
        """显示手牌（可隐藏第一张）"""
        if hide_first and len(self.cards) > 0:
            hidden = "🂠 " + " ".join(card.to_emoji() for card in self.cards[1:])
            return hidden
        return str(self)


@dataclass
class BlackjackGame:
    """21点游戏类（单人模式）"""
    player_id: int                          # 玩家Discord ID
    bet: int = 0                            # 下注金额
    is_doubled: bool = False                # 是否已加倍
    state: GameState = GameState.WAITING_BET
    player_hand: Hand = field(default_factory=Hand)
    dealer_hand: Hand = field(default_factory=Hand)
    deck: Deck = field(default_factory=lambda: Deck(num_decks=1))
    result: Optional[GameResult] = None
    payout: int = 0                         # 赔付金额
    # 保险相关属性
    insurance_bet: int = 0                  # 保险下注金额
    has_insurance: bool = False             # 是否购买了保险
    insurance_available: bool = False       # 是否可以购买保险（庄家明牌为A）
    insurance_payout: int = 0               # 保险赔付金额
    
    def start_game(self, bet: int) -> Tuple[bool, str]:
        """
        开始游戏，发初始牌
        
        返回: (成功标志, 消息)
        """
        if self.state != GameState.WAITING_BET:
            return False, "游戏已在进行中！"
        
        if bet <= 0:
            return False, "下注金额必须大于0！"
        
        self.bet = bet
        self.player_hand = Hand()
        self.dealer_hand = Hand()
        
        # 确保牌组有足够的牌
        if self.deck.remaining() < 10:
            self.deck.reset()
        
        # 发牌：玩家-庄家-玩家-庄家
        self.player_hand.add_card(self.deck.draw())
        self.dealer_hand.add_card(self.deck.draw())
        self.player_hand.add_card(self.deck.draw())
        self.dealer_hand.add_card(self.deck.draw())
        
        # 检查庄家明牌是否为A，如果是则进入保险决策阶段
        upcard = self.get_dealer_upcard()
        if upcard and upcard.rank == "A":
            self.insurance_available = True
            self.state = GameState.WAITING_INSURANCE
            return True, "庄家明牌是A，请选择是否购买保险"
        
        # 不需要保险决策，直接检查黑杰克
        return self._check_blackjack_after_insurance()
    
    def get_dealer_upcard(self) -> Optional[Card]:
        """获取庄家明牌（第二张牌，面朝上的牌）"""
        if len(self.dealer_hand.cards) >= 2:
            return self.dealer_hand.cards[1]
        return None
    
    def can_buy_insurance(self) -> bool:
        """检查是否可以购买保险"""
        return self.state == GameState.WAITING_INSURANCE and self.insurance_available
    
    def get_insurance_cost(self) -> int:
        """获取保险费用（原注码的一半）"""
        return self.bet // 2
    
    def buy_insurance(self) -> Tuple[bool, str]:
        """购买保险"""
        if not self.can_buy_insurance():
            return False, "现在无法购买保险"
        
        self.has_insurance = True
        self.insurance_bet = self.get_insurance_cost()
        
        # 进入黑杰克检查阶段
        return self._check_blackjack_after_insurance()
    
    def skip_insurance(self) -> Tuple[bool, str]:
        """跳过保险"""
        if self.state != GameState.WAITING_INSURANCE:
            return False, "现在不在保险决策阶段"
        
        # 进入黑杰克检查阶段
        return self._check_blackjack_after_insurance()
    
    def _check_blackjack_after_insurance(self) -> Tuple[bool, str]:
        """保险决策后检查黑杰克"""
        # 检查是否有黑杰克
        player_bj = self.player_hand.is_blackjack()
        dealer_bj = self.dealer_hand.is_blackjack()
        
        # 处理保险赔付
        if self.has_insurance:
            if dealer_bj:
                # 庄家21点，保险赔付 2:1（返还保险金 + 2倍赔付）
                self.insurance_payout = self.insurance_bet * 3
            else:
                # 庄家不是21点，没收保险金
                self.insurance_payout = 0
        
        if player_bj and dealer_bj:
            # 双方都是黑杰克，平局
            self.state = GameState.FINISHED
            self.result = GameResult.TIE
            self.payout = self.bet  # 退还本金
            return True, "双方都是黑杰克！平局！"
        
        if player_bj:
            # 玩家黑杰克，1.5倍赔付
            self.state = GameState.FINISHED
            self.result = GameResult.PLAYER_BLACKJACK
            self.payout = int(self.bet * 2.5)  # 本金 + 1.5倍奖金
            return True, "🎉 黑杰克！恭喜获胜！"
        
        if dealer_bj:
            # 庄家黑杰克
            self.state = GameState.FINISHED
            self.result = GameResult.DEALER_WIN
            self.payout = 0
            return True, "庄家黑杰克，您输了！"
        
        self.state = GameState.PLAYER_TURN
        return True, "游戏开始！请选择 要牌 或 停牌"
    
    def player_hit(self) -> Union[AnimatedResponse, Tuple[bool, str]]:
        """
        玩家要牌
        
        返回: AnimatedResponse 对象，包含动画提示和结果
        """
        if self.state != GameState.PLAYER_TURN:
            return AnimatedResponse(
                success=False,
                action_text="",
                result_text="现在不是您的回合！"
            )
        
        card = self.deck.draw()
        self.player_hand.add_card(card)
        
        action_text = "正在抓牌..."
        
        if self.player_hand.is_bust():
            self.state = GameState.FINISHED
            self.result = GameResult.PLAYER_BUST
            self.payout = 0
            return AnimatedResponse(
                success=True,
                action_text=action_text,
                result_text=f"抽到 {card.to_emoji()}，爆牌了！您输了！"
            )
        
        if self.player_hand.get_value() == 21:
            # 正好21点，自动停牌
            stand_result = self._player_stand_internal()
            return AnimatedResponse(
                success=True,
                action_text=action_text,
                result_text=f"抽到 {card.to_emoji()}，21点！{stand_result}"
            )
        
        return AnimatedResponse(
            success=True,
            action_text=action_text,
            result_text=f"抽到 {card.to_emoji()}，当前点数：{self.player_hand.get_value()}"
        )
    
    def _player_stand_internal(self) -> str:
        """
        玩家停牌的内部逻辑（不检查状态，返回结果字符串）
        """
        self.state = GameState.DEALER_TURN
        
        # 庄家抽牌直到17点或以上
        while self.dealer_hand.get_value() < 17:
            self.dealer_hand.add_card(self.deck.draw())
        
        # 判定结果
        player_value = self.player_hand.get_value()
        dealer_value = self.dealer_hand.get_value()
        
        self.state = GameState.FINISHED
        
        if self.dealer_hand.is_bust():
            self.result = GameResult.DEALER_BUST
            self.payout = self.bet * 2
            return "庄家爆牌！恭喜您获胜！"
        
        if player_value > dealer_value:
            self.result = GameResult.PLAYER_WIN
            self.payout = self.bet * 2
            return "恭喜您获胜！"
        elif player_value < dealer_value:
            self.result = GameResult.DEALER_WIN
            self.payout = 0
            return "庄家获胜，您输了！"
        else:
            self.result = GameResult.TIE
            self.payout = self.bet
            return "平局！退还本金"
    
    def player_stand(self) -> Tuple[bool, str]:
        """
        玩家停牌，庄家开始抽牌
        
        返回: (成功标志, 消息)
        """
        if self.state != GameState.PLAYER_TURN:
            return False, "现在不是您的回合！"
        
        result = self._player_stand_internal()
        return True, result
    
    def player_double(self) -> AnimatedResponse:
        """
        玩家加倍（只能在前两张牌时使用）
        
        返回: AnimatedResponse 对象，包含动画提示和结果
        """
        if self.state != GameState.PLAYER_TURN:
            return AnimatedResponse(
                success=False,
                action_text="",
                result_text="现在不是您的回合！"
            )
        
        if len(self.player_hand.cards) != 2:
            return AnimatedResponse(
                success=False,
                action_text="",
                result_text="只能在前两张牌时加倍！"
            )
        
        # 加倍下注
        self.bet *= 2
        self.is_doubled = True
        
        # 只抽一张牌然后自动停牌
        card = self.deck.draw()
        self.player_hand.add_card(card)
        
        action_text = "正在抓牌..."
        
        if self.player_hand.is_bust():
            self.state = GameState.FINISHED
            self.result = GameResult.PLAYER_BUST
            self.payout = 0
            return AnimatedResponse(
                success=True,
                action_text=action_text,
                result_text=f"加倍！抽到 {card.to_emoji()}，爆牌了！您输了！"
            )
        
        # 自动停牌
        stand_result = self._player_stand_internal()
        
        # 检查是否为加倍获胜（需要双倍加粗）
        is_double_win = self.result in [GameResult.PLAYER_WIN, GameResult.DEALER_BUST]
        
        return AnimatedResponse(
            success=True,
            action_text=action_text,
            result_text=f"加倍！抽到 {card.to_emoji()}。{stand_result}",
            is_double_win=is_double_win,
            secondary_action_text=f"用户选择加倍！",
            secondary_delay_seconds=1.0,
            delay_seconds=2.0
        )
    
    def get_game_display(self, show_dealer: bool = False, hide_last_player_card: bool = False) -> str:
        """
        获取游戏状态显示
        
        参数:
            show_dealer: 是否显示庄家全部手牌
            hide_last_player_card: 是否隐藏玩家最后一张抓到的牌（用于动画过渡）
        """
        lines = []
        
        # 庄家手牌
        if show_dealer or self.state == GameState.FINISHED:
            dealer_display = self.dealer_hand.to_display()
            dealer_value = self.dealer_hand.get_value()
            lines.append(f"🎰 **月月手牌** [`{dealer_value}点`]")
            lines.append(f"   > {dealer_display}")
        else:
            lines.append(f"🎰 **月月手牌** [`?点`]")
            lines.append(f"   > {self.dealer_hand.to_display(hide_first=True)}")
        
        lines.append("")
        
        # 玩家手牌逻辑处理（动画过渡）
        if hide_last_player_card and len(self.player_hand.cards) > 1:
            # 创建临时手牌信息用于显示，不包含最后一张牌
            temp_cards = self.player_hand.cards[:-1]
            # 临时计算点数
            temp_value = sum(c.value for c in temp_cards)
            temp_aces = sum(1 for c in temp_cards if c.rank == "A")
            while temp_value > 21 and temp_aces > 0:
                temp_value -= 10
                temp_aces -= 1
            
            player_value = temp_value
            player_cards_str = " ".join(c.to_emoji() for c in temp_cards)
        else:
            player_value = self.player_hand.get_value()
            player_cards_str = self.player_hand.to_display()

        lines.append(f"👤 **您的手牌** [`{player_value}点`]")
        lines.append(f"   > {player_cards_str}")
        
        lines.append("")
        lines.append(f"💰 **当前下注**：`{self.bet}` 灵石")
        
        # 显示保险信息
        if self.has_insurance:
            lines.append(f"🛡️ **保险**：`{self.insurance_bet}` 灵石")
        
        return "\n".join(lines)

    def get_dealer_remark(self) -> str:
        """根据结果获取月月的短结论反馈"""
        # 特殊逻辑：加倍胜出
        if self.is_doubled and self.result in [GameResult.PLAYER_WIN, GameResult.DEALER_BUST]:
            return "恭喜，您是大赢家！"
        
        remarks = {
            GameResult.PLAYER_WIN: "手气不错，这局算你赢了！",
            GameResult.DEALER_WIN: "承让了，看来运气在我这边。",
            GameResult.TIE: "运气不相上下，这局就算打平了。",
            GameResult.PLAYER_BLACKJACK: "天选之子！竟然是黑杰克！",
            GameResult.PLAYER_BUST: "很遗憾，就差一点！",
            GameResult.DEALER_BUST: "哎呀，我这手牌竟然爆了...",
            GameResult.PLAYER_SURRENDER: "识时务者为俊杰，明智的选择~"
        }
        return remarks.get(self.result, "游戏结束。")
    
    def get_result_display(self) -> str:
        """获取游戏结果显示（优化排版与月月反馈）"""
        if self.state != GameState.FINISHED:
            return "游戏进行中..."
        
        result_text = {
            GameResult.PLAYER_WIN: "🎉 您获胜了！",
            GameResult.DEALER_WIN: "😔 月月获胜",
            GameResult.TIE: "🤝 平局",
            GameResult.PLAYER_BLACKJACK: "🃏 黑杰克！",
            GameResult.PLAYER_BUST: "💥 爆牌",
            GameResult.DEALER_BUST: "🎉 月月爆牌！",
            GameResult.PLAYER_SURRENDER: "🏳️ 投降"
        }
        
        # 计算盈亏（包括保险）
        total_payout = self.payout + self.insurance_payout
        total_cost = self.bet + self.insurance_bet
        profit = total_payout - total_cost
        profit_text = f"+{profit}" if profit > 0 else str(profit)
        
        result_title = result_text.get(self.result, '游戏结束')
        if self.is_doubled and self.result in [GameResult.PLAYER_WIN, GameResult.DEALER_BUST, GameResult.PLAYER_BLACKJACK]:
            result_title = f"『{result_title}』"
        
        lines = [
            self.get_game_display(show_dealer=True),
            "",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"**【结算结果】** **{result_title}**",
        ]
        
        # 添加保险结算信息
        if self.has_insurance:
            if self.insurance_payout > 0:
                insurance_profit = self.insurance_payout - self.insurance_bet
                lines.append(f"🛡️ **保险结算**：月月21点！获得 **+{insurance_profit}** 灵石")
            else:
                lines.append(f"🛡️ **保险结算**：月月非21点，损失 **-{self.insurance_bet}** 灵石")
        
        lines.extend([
            f"**💰 最终赔付**：**{total_payout}** 灵石 (`{profit_text}`)",
            "",
            f"> 💬 **月月**：「{self.get_dealer_remark()}」",
            "━━━━━━━━━━━━━━━━━━━━━"
        ])
        
        return "\n".join(lines)
    
    def is_finished(self) -> bool:
        """游戏是否结束"""
        return self.state == GameState.FINISHED
    
    def can_double(self) -> bool:
        """是否可以加倍"""
        return (self.state == GameState.PLAYER_TURN and
                len(self.player_hand.cards) == 2)
    
    def can_surrender(self) -> bool:
        """是否可以投降（只能在前两张牌时）"""
        return (self.state == GameState.PLAYER_TURN and
                len(self.player_hand.cards) == 2)
    
    def player_surrender(self) -> Tuple[bool, str]:
        """
        玩家投降，返还一半赌注
        
        返回: (成功标志, 消息)
        """
        if not self.can_surrender():
            return False, "只能在前两张牌时投降！"
        
        self.state = GameState.FINISHED
        self.result = GameResult.PLAYER_SURRENDER
        self.payout = self.bet // 2  # 返还一半赌注
        
        return True, f"您选择投降，返还一半赌注 ({self.payout} 灵石)"
    
    def reset_for_new_round(self):
        """重置游戏以开始新一轮"""
        self.bet = 0
        self.is_doubled = False
        self.state = GameState.WAITING_BET
        self.player_hand = Hand()
        self.dealer_hand = Hand()
        self.result = None
        self.payout = 0
        # 重置保险相关属性
        self.insurance_bet = 0
        self.has_insurance = False
        self.insurance_available = False
        self.insurance_payout = 0


class BlackjackSessionManager:
    """21点游戏会话管理器（单人模式）"""
    
    def __init__(self):
        self.sessions: Dict[int, BlackjackGame] = {}
        self._locks: Dict[int, asyncio.Lock] = {}  # 玩家操作锁，防止并发操作
    
    def get_session(self, player_id: int) -> Optional[BlackjackGame]:
        """获取玩家的游戏会话"""
        return self.sessions.get(player_id)
    
    def get_lock(self, player_id: int) -> asyncio.Lock:
        """
        获取玩家的操作锁
        用于防止同一玩家的并发操作（如快速连续点击按钮）
        """
        if player_id not in self._locks:
            self._locks[player_id] = asyncio.Lock()
        return self._locks[player_id]
    
    def create_session(self, player_id: int) -> BlackjackGame:
        """创建新的游戏会话"""
        game = BlackjackGame(player_id=player_id)
        self.sessions[player_id] = game
        # 确保有对应的锁
        if player_id not in self._locks:
            self._locks[player_id] = asyncio.Lock()
        return game
    
    def remove_session(self, player_id: int):
        """移除游戏会话"""
        if player_id in self.sessions:
            del self.sessions[player_id]
        # 同时清理操作锁
        if player_id in self._locks:
            del self._locks[player_id]
    
    def has_active_session(self, player_id: int) -> bool:
        """检查玩家是否有进行中的游戏"""
        session = self.get_session(player_id)
        return session is not None and not session.is_finished()


# 全局游戏会话管理器
blackjack_sessions = BlackjackSessionManager()