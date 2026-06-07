# -*- coding: utf-8 -*-
"""
21点游戏 Discord UI 组件
使用按钮和嵌入消息，ephemeral 模式避免刷屏
"""

import discord
from discord import ui
import logging
import asyncio
from typing import Optional

from src.chat.features.games.services.blackjack_game import (
    BlackjackGame, GameState, GameResult, Hand, blackjack_sessions
)
from src.chat.features.odysseia_coin.service.coin_service import coin_service

log = logging.getLogger(__name__)


def _get_total_payout(game: BlackjackGame) -> int:
    """获取本局总赔付（普通赔付 + 保险赔付）。"""
    return game.payout + game.insurance_payout


async def _settle_game_payout(game: BlackjackGame, reason: str) -> int:
    """按本局最终赔付结算余额并返回最新余额。"""
    balance = await coin_service.get_balance(game.player_id)
    total_payout = _get_total_payout(game)
    if total_payout > 0:
        balance = await coin_service.add_coins(game.player_id, total_payout, reason)
    return balance


def _get_active_view_cls(game: BlackjackGame):
    """根据当前状态选择应展示的操作视图类。"""
    if game.state == GameState.WAITING_INSURANCE:
        return InsuranceDecisionView
    return GamePlayView


def _create_active_view(game: BlackjackGame) -> ui.View:
    """根据当前状态创建对应的操作视图。"""
    return _get_active_view_cls(game)(game)


def _build_action_embed(
    game: BlackjackGame,
    balance: int,
    stage_text: str,
    *,
    dealer_cards=None,
    player_cards=None,
    hide_dealer_first: bool = False,
    hide_last_player_card: bool = False,
    footer_text: str = "动画演出中，请稍候..."
) -> discord.Embed:
    """创建动画阶段使用的过渡 Embed。"""
    current_dealer_cards = list(
        dealer_cards if dealer_cards is not None else game.dealer_hand.cards
    )
    current_player_cards = list(
        player_cards if player_cards is not None else game.player_hand.cards
    )

    dealer_hand = Hand(cards=current_dealer_cards)
    player_hand = Hand(cards=current_player_cards)

    if hide_dealer_first and current_dealer_cards:
        dealer_value_text = "?点"
        dealer_display = "🂠"
        if len(current_dealer_cards) > 1:
            dealer_display += " " + " ".join(
                card.to_emoji() for card in current_dealer_cards[1:]
            )
    else:
        dealer_value_text = f"{dealer_hand.get_value()}点"
        dealer_display = dealer_hand.to_display()

    player_display_cards = current_player_cards
    if hide_last_player_card and len(player_display_cards) > 1:
        player_display_cards = player_display_cards[:-1]
    display_player_hand = Hand(cards=list(player_display_cards))
    player_value_text = f"{display_player_hand.get_value()}点"
    player_display = display_player_hand.to_display()

    embed = discord.Embed(
        title="🎰 21点 - 动画演出中",
        description=stage_text,
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name=f"🎰 月月的手牌 [{dealer_value_text}]",
        value=f"```{dealer_display}```",
        inline=False,
    )
    embed.add_field(
        name=f"👤 你的手牌 [{player_value_text}]",
        value=f"```{player_display}```",
        inline=False,
    )
    embed.add_field(
        name="💰 当前下注",
        value=f"**{game.bet}** 灵石",
        inline=True,
    )
    embed.add_field(
        name="💳 你的余额",
        value=f"**{balance}** 灵石",
        inline=True,
    )
    if game.has_insurance:
        embed.add_field(
            name="🛡️ 当前保险",
            value=f"**{game.insurance_bet}** 灵石",
            inline=True,
        )
    embed.set_footer(text=footer_text)
    return embed


async def _edit_game_message(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: Optional[ui.View],
) -> None:
    """统一处理首次和后续消息编辑。"""
    if not interaction.response.is_done():
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.edit_original_response(embed=embed, view=view)


async def _animate_hit_feedback(
    interaction: discord.Interaction,
    game: BlackjackGame,
    balance: int,
    action_text: str,
    result_text: str,
) -> None:
    """播放玩家要牌动画。"""
    preview_embed = _build_action_embed(
        game,
        balance,
        action_text,
        hide_dealer_first=True,
        hide_last_player_card=True,
    )
    await _edit_game_message(interaction, embed=preview_embed, view=None)
    await asyncio.sleep(1.2)

    if game.is_finished():
        balance = await _settle_game_payout(
            game,
            f"21点结算 ({game.result.value if game.result else 'unknown'})"
        )
        final_embed = create_result_embed(game, balance)
        await _edit_game_message(
            interaction,
            embed=final_embed,
            view=GameEndView(game),
        )
        return

    result_embed = _build_action_embed(
        game,
        balance,
        result_text,
        hide_dealer_first=True,
        footer_text="你可以继续操作。",
    )
    self_view = _create_active_view(game)
    await _edit_game_message(interaction, embed=result_embed, view=self_view)


async def _animate_dealer_turn(
    interaction: discord.Interaction,
    game: BlackjackGame,
    balance: int,
    dealer_cards_before,
    settlement_reason: str,
    *,
    prefix_text: Optional[str] = None,
) -> None:
    """播放庄家翻底牌和逐张补牌动画。"""
    final_dealer_cards = list(game.dealer_hand.cards)
    stages = []

    reveal_text = "月月亮出了底牌..."
    if dealer_cards_before:
        reveal_text = f"月月亮出了底牌：{dealer_cards_before[0].to_emoji()}"
    if prefix_text:
        reveal_text = f"{prefix_text}\n{reveal_text}"
    stages.append((reveal_text, final_dealer_cards[: min(2, len(final_dealer_cards))]))

    for index in range(2, len(final_dealer_cards)):
        stages.append(
            (
                f"月月补到 {final_dealer_cards[index].to_emoji()}...",
                final_dealer_cards[: index + 1],
            )
        )

    for stage_index, (text, dealer_cards) in enumerate(stages):
        stage_embed = _build_action_embed(
            game,
            balance,
            text,
            dealer_cards=dealer_cards,
            footer_text="月月正在补牌...",
        )
        await _edit_game_message(interaction, embed=stage_embed, view=None)
        if stage_index != len(stages) - 1:
            await asyncio.sleep(1.1)

    await asyncio.sleep(0.8)
    final_balance = await _settle_game_payout(game, settlement_reason)
    final_embed = create_result_embed(game, final_balance)
    await _edit_game_message(
        interaction,
        embed=final_embed,
        view=GameEndView(game),
    )


class BetModal(ui.Modal, title="下注金额"):
    """下注金额输入模态框"""
    
    bet_input = ui.TextInput(
        label="请输入下注金额",
        placeholder="输入灵石数量...",
        min_length=1,
        max_length=10,
        required=True
    )
    
    def __init__(self, original_interaction: discord.Interaction = None):
        super().__init__()
        # 保存原始交互，用于编辑消息而不是发送新消息
        self.original_interaction = original_interaction
    
    async def on_submit(self, interaction: discord.Interaction):
        """处理下注提交"""
        from src.chat.config.chat_config import COIN_CONFIG
        
        try:
            bet_amount = int(self.bet_input.value)
            min_bet = COIN_CONFIG.get("BLACKJACK_MIN_BET", 10)
            max_bet = COIN_CONFIG.get("BLACKJACK_MAX_BET", None)  # None表示无上限
            
            if bet_amount < min_bet:
                await interaction.response.send_message(
                    f"❌ 下注金额太少！最低下注 **{min_bet}** 灵石。", ephemeral=True
                )
                return
            
            # 只有设置了max_bet才检查上限
            if max_bet is not None and bet_amount > max_bet:
                await interaction.response.send_message(
                    f"❌ 下注金额太多！最高下注 **{max_bet}** 灵石。\n月月可不想被你赢太多！", ephemeral=True
                )
                return
            
            user_id = interaction.user.id
            balance = await coin_service.get_balance(user_id)
            
            if balance < bet_amount:
                await interaction.response.send_message(
                    f"❌ 余额不足！你只有 **{balance}** 灵石，但想下注 **{bet_amount}**。",
                    ephemeral=True
                )
                return
            
            # 扣除灵石
            new_balance = await coin_service.remove_coins(
                user_id, bet_amount, "21点游戏下注"
            )
            
            # 创建或获取游戏会话
            game = blackjack_sessions.create_session(user_id)
            success, message = game.start_game(bet_amount)
            
            if not success:
                # 退还灵石
                await coin_service.add_coins(user_id, bet_amount, "21点下注失败退还")
                await interaction.response.send_message(
                    f"❌ {message}", ephemeral=True
                )
                return
            
            # 创建游戏嵌入和按钮
            embed = create_game_embed(game, new_balance)
            
            # 检查游戏是否已经结束（如黑杰克）
            if game.is_finished():
                view = GameEndView(game)
                new_balance = await _settle_game_payout(
                    game,
                    f"21点结算 ({game.result.value if game.result else 'unknown'})"
                )
                embed = create_result_embed(game, new_balance)
            else:
                view = _create_active_view(game)
            
            # 如果有原始交互（从"再来一局"来的），编辑那条消息
            if self.original_interaction:
                try:
                    await interaction.response.defer()
                    await self.original_interaction.edit_original_response(
                        embed=embed, view=view
                    )
                except Exception as e:
                    log.warning(f"编辑原消息失败，发送新消息: {e}")
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.response.send_message(
                    embed=embed, view=view, ephemeral=True
                )
            
        except ValueError:
            await interaction.response.send_message(
                "❌ 请输入有效的数字！", ephemeral=True
            )
        except Exception as e:
            log.error(f"下注处理错误: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ 发生错误，请稍后再试。", ephemeral=True
            )


class InsuranceDecisionView(ui.View):
    """保险决策阶段的按钮视图。"""

    def __init__(self, game: BlackjackGame):
        super().__init__(timeout=300)
        self.game = game

    @ui.button(label="买保险", style=discord.ButtonStyle.success, emoji="🛡️")
    async def buy_insurance_button(self, interaction: discord.Interaction, button: ui.Button):
        """购买保险按钮。"""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message(
                "❌ 这不是你的游戏！", ephemeral=True
            )
            return

        async with blackjack_sessions.get_lock(self.game.player_id):
            if not self.game.can_buy_insurance():
                await interaction.response.send_message(
                    "❌ 现在无法购买保险！", ephemeral=True
                )
                return

            insurance_cost = self.game.get_insurance_cost()
            balance = await coin_service.get_balance(self.game.player_id)
            if balance < insurance_cost:
                await interaction.response.send_message(
                    f"❌ 余额不足购买保险！需要 **{insurance_cost}** 灵石，你只有 **{balance}**。",
                    ephemeral=True
                )
                return

            await coin_service.remove_coins(
                self.game.player_id, insurance_cost, "21点购买保险"
            )

            success, message = self.game.buy_insurance()
            if not success:
                await coin_service.add_coins(
                    self.game.player_id, insurance_cost, "21点保险失败返还"
                )
                await interaction.response.send_message(
                    f"❌ {message}", ephemeral=True
                )
                return

            if self.game.is_finished():
                balance = await _settle_game_payout(
                    self.game,
                    f"21点结算 ({self.game.result.value if self.game.result else 'unknown'})"
                )
                embed = create_result_embed(self.game, balance)
                view = GameEndView(self.game)
            else:
                balance = await coin_service.get_balance(self.game.player_id)
                embed = create_game_embed(self.game, balance)
                view = _create_active_view(self.game)

            await interaction.response.edit_message(embed=embed, view=view)

    @ui.button(label="不买保险", style=discord.ButtonStyle.secondary, emoji="➡️")
    async def skip_insurance_button(self, interaction: discord.Interaction, button: ui.Button):
        """跳过保险按钮。"""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message(
                "❌ 这不是你的游戏！", ephemeral=True
            )
            return

        async with blackjack_sessions.get_lock(self.game.player_id):
            success, message = self.game.skip_insurance()
            if not success:
                await interaction.response.send_message(
                    f"❌ {message}", ephemeral=True
                )
                return

            if self.game.is_finished():
                balance = await _settle_game_payout(
                    self.game,
                    f"21点结算 ({self.game.result.value if self.game.result else 'unknown'})"
                )
                embed = create_result_embed(self.game, balance)
                view = GameEndView(self.game)
            else:
                balance = await coin_service.get_balance(self.game.player_id)
                embed = create_game_embed(self.game, balance)
                view = _create_active_view(self.game)

            await interaction.response.edit_message(embed=embed, view=view)


class GamePlayView(ui.View):
    """游戏进行中的按钮视图"""
    
    def __init__(self, game: BlackjackGame):
        super().__init__(timeout=300)  # 5分钟超时
        self.game = game
        self._update_buttons()
    
    def _update_buttons(self):
        """根据游戏状态更新按钮"""
        is_player_turn = self.game.state == GameState.PLAYER_TURN
        self.hit_button.disabled = not is_player_turn
        self.stand_button.disabled = not is_player_turn
        self.double_button.disabled = not (is_player_turn and self.game.can_double())
        self.surrender_button.disabled = not (is_player_turn and self.game.can_surrender())
    
    @ui.button(label="要牌", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit_button(self, interaction: discord.Interaction, button: ui.Button):
        """要牌按钮"""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message(
                "❌ 这不是你的游戏！", ephemeral=True
            )
            return
        
        async with blackjack_sessions.get_lock(self.game.player_id):
            response = self.game.player_hit()
            if not response.success:
                await interaction.response.send_message(
                    f"❌ {response.result_text}", ephemeral=True
                )
                return

            balance = await coin_service.get_balance(self.game.player_id)
            await _animate_hit_feedback(
                interaction,
                self.game,
                balance,
                response.action_text,
                response.result_text,
            )
    
    @ui.button(label="停牌", style=discord.ButtonStyle.secondary, emoji="✋")
    async def stand_button(self, interaction: discord.Interaction, button: ui.Button):
        """停牌按钮"""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message(
                "❌ 这不是你的游戏！", ephemeral=True
            )
            return
        
        async with blackjack_sessions.get_lock(self.game.player_id):
            dealer_cards_before = list(self.game.dealer_hand.cards)
            success, message = self.game.player_stand()
            if not success:
                await interaction.response.send_message(
                    f"❌ {message}", ephemeral=True
                )
                return

            balance = await coin_service.get_balance(self.game.player_id)
            await _animate_dealer_turn(
                interaction,
                self.game,
                balance,
                dealer_cards_before,
                f"21点结算 ({self.game.result.value if self.game.result else 'unknown'})",
                prefix_text="你选择了停牌，轮到月月行动了...",
            )
    
    @ui.button(label="加倍", style=discord.ButtonStyle.success, emoji="💰")
    async def double_button(self, interaction: discord.Interaction, button: ui.Button):
        """加倍按钮"""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message(
                "❌ 这不是你的游戏！", ephemeral=True
            )
            return
        
        async with blackjack_sessions.get_lock(self.game.player_id):
            if not self.game.can_double():
                await interaction.response.send_message(
                    "❌ 现在不能加倍！", ephemeral=True
                )
                return

            # 检查余额是否足够加倍
            original_bet = self.game.bet
            player_cards_before = list(self.game.player_hand.cards)
            dealer_cards_before = list(self.game.dealer_hand.cards)
            balance = await coin_service.get_balance(self.game.player_id)
            
            if balance < original_bet:
                await interaction.response.send_message(
                    f"❌ 余额不足以加倍！需要 **{original_bet}** 灵石，你只有 **{balance}**。",
                    ephemeral=True
                )
                return
            
            # 扣除加倍金额
            await coin_service.remove_coins(
                self.game.player_id, original_bet, "21点加倍下注"
            )
            
            response = self.game.player_double()
            if not response.success:
                await coin_service.add_coins(
                    self.game.player_id, original_bet, "21点加倍失败返还"
                )
                await interaction.response.send_message(
                    f"❌ {response.result_text}", ephemeral=True
                )
                return

            current_balance = await coin_service.get_balance(self.game.player_id)

            if response.secondary_action_text:
                stage_one_embed = _build_action_embed(
                    self.game,
                    current_balance,
                    response.secondary_action_text,
                    dealer_cards=dealer_cards_before,
                    player_cards=player_cards_before,
                )
                await _edit_game_message(
                    interaction,
                    embed=stage_one_embed,
                    view=None,
                )
                await asyncio.sleep(max(response.secondary_delay_seconds, 0.6))
            else:
                await interaction.response.defer()

            stage_two_embed = _build_action_embed(
                self.game,
                current_balance,
                response.action_text,
                dealer_cards=dealer_cards_before,
                hide_dealer_first=True,
                hide_last_player_card=True,
            )
            await _edit_game_message(
                interaction,
                embed=stage_two_embed,
                view=None,
            )
            await asyncio.sleep(1.0)

            if self.game.result == GameResult.PLAYER_BUST:
                final_balance = await _settle_game_payout(
                    self.game,
                    f"21点加倍结算 ({self.game.result.value if self.game.result else 'unknown'})"
                )
                final_embed = create_result_embed(self.game, final_balance)
                await _edit_game_message(
                    interaction,
                    embed=final_embed,
                    view=GameEndView(self.game),
                )
                return

            await _animate_dealer_turn(
                interaction,
                self.game,
                current_balance,
                dealer_cards_before,
                f"21点加倍结算 ({self.game.result.value if self.game.result else 'unknown'})",
                prefix_text=f"加倍！抽到 {self.game.player_hand.cards[-1].to_emoji()}，轮到月月行动了...",
            )
    
    @ui.button(label="投降", style=discord.ButtonStyle.danger, emoji="🏳️")
    async def surrender_button(self, interaction: discord.Interaction, button: ui.Button):
        """投降按钮"""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message(
                "❌ 这不是你的游戏！", ephemeral=True
            )
            return
        
        async with blackjack_sessions.get_lock(self.game.player_id):
            success, message = self.game.player_surrender()
            
            if not success:
                await interaction.response.send_message(
                    f"❌ {message}", ephemeral=True
                )
                return
            
            balance = await _settle_game_payout(
                self.game,
                "21点投降结算"
            )
            
            embed = create_result_embed(self.game, balance)
            view = GameEndView(self.game)
            
            await interaction.response.edit_message(embed=embed, view=view)
    
    async def on_timeout(self):
        """超时处理"""
        # 游戏超时，没收赌注
        blackjack_sessions.remove_session(self.game.player_id)


class GameEndView(ui.View):
    """游戏结束的按钮视图"""
    
    def __init__(self, game: BlackjackGame):
        super().__init__(timeout=120)  # 2分钟超时
        self.game = game
    
    @ui.button(label="再来一局", style=discord.ButtonStyle.primary, emoji="🔄")
    async def play_again_button(self, interaction: discord.Interaction, button: ui.Button):
        """再来一局按钮"""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message(
                "❌ 这不是你的游戏！", ephemeral=True
            )
            return
        
        # 清理旧会话
        blackjack_sessions.remove_session(self.game.player_id)
        
        # 显示下注模态框，传入原始交互用于编辑同一条消息
        modal = BetModal(original_interaction=interaction)
        await interaction.response.send_modal(modal)
    
    @ui.button(label="结束游戏", style=discord.ButtonStyle.secondary, emoji="🚪")
    async def end_game_button(self, interaction: discord.Interaction, button: ui.Button):
        """结束游戏按钮"""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message(
                "❌ 这不是你的游戏！", ephemeral=True
            )
            return
        
        # 清理会话
        blackjack_sessions.remove_session(self.game.player_id)
        
        balance = await coin_service.get_balance(self.game.player_id)
        
        embed = discord.Embed(
            title="🎰 21点游戏结束",
            description=f"感谢游玩！\n\n💰 你的余额：**{balance}** 灵石",
            color=discord.Color.blue()
        )
        
        await interaction.response.edit_message(embed=embed, view=None)
    
    async def on_timeout(self):
        """超时处理"""
        blackjack_sessions.remove_session(self.game.player_id)


class StartGameView(ui.View):
    """开始游戏的视图"""
    
    def __init__(self):
        super().__init__(timeout=120)
    
    @ui.button(label="开始游戏", style=discord.ButtonStyle.success, emoji="🎰")
    async def start_button(self, interaction: discord.Interaction, button: ui.Button):
        """开始游戏按钮"""
        user_id = interaction.user.id
        
        # 检查是否已有进行中的游戏
        if blackjack_sessions.has_active_session(user_id):
            await interaction.response.send_message(
                "❌ 你已经有一局进行中的游戏！请先完成当前游戏。",
                ephemeral=True
            )
            return
        
        modal = BetModal()
        await interaction.response.send_modal(modal)


def create_game_embed(game: BlackjackGame, balance: int) -> discord.Embed:
    """创建游戏进行中的嵌入消息"""
    embed = discord.Embed(
        title="🎰 21点 - 游戏进行中",
        color=discord.Color.gold()
    )
    
    # 月月手牌（隐藏第一张）
    dealer_display = game.dealer_hand.to_display(hide_first=True)
    embed.add_field(
        name="🎰 月月的手牌 [?点]",
        value=f"```{dealer_display}```",
        inline=False
    )
    
    # 玩家手牌
    player_value = game.player_hand.get_value()
    player_display = game.player_hand.to_display()
    embed.add_field(
        name=f"👤 你的手牌 [{player_value}点]",
        value=f"```{player_display}```",
        inline=False
    )
    
    embed.add_field(
        name="💰 当前下注",
        value=f"**{game.bet}** 灵石",
        inline=True
    )
    
    embed.add_field(
        name="💳 你的余额",
        value=f"**{balance}** 灵石",
        inline=True
    )
    
    if game.state == GameState.WAITING_INSURANCE:
        insurance_cost = game.get_insurance_cost()
        embed.add_field(
            name="🛡️ 保险选择",
            value=(
                f"月月明牌是 **A**，现在可以选择是否购买保险。\n"
                f"保险费用：**{insurance_cost}** 灵石（原注一半）"
            ),
            inline=False
        )
        embed.set_footer(text="请先选择：买保险 / 不买保险")
    else:
        embed.set_footer(text="选择 要牌/停牌/加倍/投降")
    
    return embed


def create_result_embed(game: BlackjackGame, balance: int) -> discord.Embed:
    """创建游戏结果的嵌入消息"""
    
    # 根据结果设置颜色和标题
    result_info = {
        GameResult.PLAYER_WIN: ("🎉 恭喜获胜！", discord.Color.green()),
        GameResult.DEALER_WIN: ("😔 月月获胜", discord.Color.red()),
        GameResult.TIE: ("🤝 平局", discord.Color.gold()),
        GameResult.PLAYER_BLACKJACK: ("🃏 黑杰克！", discord.Color.purple()),
        GameResult.PLAYER_BUST: ("💥 爆牌了", discord.Color.red()),
        GameResult.DEALER_BUST: ("🎉 月月爆牌！", discord.Color.green()),
        GameResult.PLAYER_SURRENDER: ("🏳️ 投降", discord.Color.orange()),
    }
    
    title, color = result_info.get(game.result, ("游戏结束", discord.Color.blue()))
    
    embed = discord.Embed(
        title=f"🎰 21点 - {title}",
        color=color
    )
    
    # 月月手牌（显示所有）
    dealer_value = game.dealer_hand.get_value()
    dealer_display = game.dealer_hand.to_display()
    embed.add_field(
        name=f"🎰 月月的手牌 [{dealer_value}点]",
        value=f"```{dealer_display}```",
        inline=False
    )
    
    # 玩家手牌
    player_value = game.player_hand.get_value()
    player_display = game.player_hand.to_display()
    embed.add_field(
        name=f"👤 你的手牌 [{player_value}点]",
        value=f"```{player_display}```",
        inline=False
    )
    
    # 计算盈亏
    total_payout = game.payout + game.insurance_payout
    total_cost = game.bet + game.insurance_bet
    profit = total_payout - total_cost
    profit_text = f"+{profit}" if profit > 0 else str(profit)
    
    embed.add_field(
        name="💰 赔付结算",
        value=f"下注：**{game.bet}** | 赔付：**{total_payout}** (`{profit_text}`)",
        inline=False
    )
    
    embed.add_field(
        name="💳 当前余额",
        value=f"**{balance}** 灵石",
        inline=True
    )
    
    # 月月的反馈
    embed.add_field(
        name="💬 月月说",
        value=f"「{game.get_dealer_remark()}」",
        inline=False
    )
    
    return embed
