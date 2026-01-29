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
    BlackjackGame, GameState, GameResult, blackjack_sessions
)
from src.chat.features.odysseia_coin.service.coin_service import coin_service

log = logging.getLogger(__name__)


class BetModal(ui.Modal, title="下注金额"):
    """下注金额输入模态框"""
    
    bet_input = ui.TextInput(
        label="请输入下注金额",
        placeholder="输入月光币数量...",
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
                    f"❌ 下注金额太少！最低下注 **{min_bet}** 月光币。", ephemeral=True
                )
                return
            
            # 只有设置了max_bet才检查上限
            if max_bet is not None and bet_amount > max_bet:
                await interaction.response.send_message(
                    f"❌ 下注金额太多！最高下注 **{max_bet}** 月光币。\n月月可不想被你赢太多！", ephemeral=True
                )
                return
            
            user_id = interaction.user.id
            balance = await coin_service.get_balance(user_id)
            
            if balance < bet_amount:
                await interaction.response.send_message(
                    f"❌ 余额不足！你只有 **{balance}** 月光币，但想下注 **{bet_amount}**。",
                    ephemeral=True
                )
                return
            
            # 扣除月光币
            new_balance = await coin_service.remove_coins(
                user_id, bet_amount, "21点游戏下注"
            )
            
            # 创建或获取游戏会话
            game = blackjack_sessions.create_session(user_id)
            success, message = game.start_game(bet_amount)
            
            if not success:
                # 退还月光币
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
                # 处理赔付
                if game.payout > 0:
                    new_balance = await coin_service.add_coins(
                        user_id, game.payout, f"21点获胜 ({game.result.value})"
                    )
                embed = create_result_embed(game, new_balance)
            else:
                view = GamePlayView(game)
            
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


class GamePlayView(ui.View):
    """游戏进行中的按钮视图"""
    
    def __init__(self, game: BlackjackGame):
        super().__init__(timeout=300)  # 5分钟超时
        self.game = game
        self._update_buttons()
    
    def _update_buttons(self):
        """根据游戏状态更新按钮"""
        # 加倍按钮只在前两张牌时可用
        self.double_button.disabled = not self.game.can_double()
        # 投降按钮只在前两张牌时可用
        self.surrender_button.disabled = not self.game.can_surrender()
    
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
            
            balance = await coin_service.get_balance(self.game.player_id)
            
            if self.game.is_finished():
                # 处理赔付
                if self.game.payout > 0:
                    balance = await coin_service.add_coins(
                        self.game.player_id, 
                        self.game.payout, 
                        f"21点获胜 ({self.game.result.value})"
                    )
                embed = create_result_embed(self.game, balance)
                view = GameEndView(self.game)
            else:
                embed = create_game_embed(self.game, balance)
                self._update_buttons()
                view = self
            
            await interaction.response.edit_message(embed=embed, view=view)
    
    @ui.button(label="停牌", style=discord.ButtonStyle.secondary, emoji="✋")
    async def stand_button(self, interaction: discord.Interaction, button: ui.Button):
        """停牌按钮"""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message(
                "❌ 这不是你的游戏！", ephemeral=True
            )
            return
        
        async with blackjack_sessions.get_lock(self.game.player_id):
            success, message = self.game.player_stand()
            
            balance = await coin_service.get_balance(self.game.player_id)
            
            # 处理赔付
            if self.game.payout > 0:
                balance = await coin_service.add_coins(
                    self.game.player_id, 
                    self.game.payout, 
                    f"21点获胜 ({self.game.result.value})"
                )
            
            embed = create_result_embed(self.game, balance)
            view = GameEndView(self.game)
            
            await interaction.response.edit_message(embed=embed, view=view)
    
    @ui.button(label="加倍", style=discord.ButtonStyle.success, emoji="💰")
    async def double_button(self, interaction: discord.Interaction, button: ui.Button):
        """加倍按钮"""
        if interaction.user.id != self.game.player_id:
            await interaction.response.send_message(
                "❌ 这不是你的游戏！", ephemeral=True
            )
            return
        
        async with blackjack_sessions.get_lock(self.game.player_id):
            # 检查余额是否足够加倍
            original_bet = self.game.bet
            balance = await coin_service.get_balance(self.game.player_id)
            
            if balance < original_bet:
                await interaction.response.send_message(
                    f"❌ 余额不足以加倍！需要 **{original_bet}** 月光币，你只有 **{balance}**。",
                    ephemeral=True
                )
                return
            
            # 扣除加倍金额
            await coin_service.remove_coins(
                self.game.player_id, original_bet, "21点加倍下注"
            )
            
            response = self.game.player_double()
            
            balance = await coin_service.get_balance(self.game.player_id)
            
            # 处理赔付
            if self.game.payout > 0:
                balance = await coin_service.add_coins(
                    self.game.player_id, 
                    self.game.payout, 
                    f"21点加倍获胜 ({self.game.result.value})"
                )
            
            embed = create_result_embed(self.game, balance)
            view = GameEndView(self.game)
            
            await interaction.response.edit_message(embed=embed, view=view)
    
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
            
            balance = await coin_service.get_balance(self.game.player_id)
            
            # 处理投降返还（一半赌注）
            if self.game.payout > 0:
                balance = await coin_service.add_coins(
                    self.game.player_id, 
                    self.game.payout, 
                    "21点投降返还"
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
            description=f"感谢游玩！\n\n💰 你的余额：**{balance}** 月光币",
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
        value=f"**{game.bet}** 月光币",
        inline=True
    )
    
    embed.add_field(
        name="💳 你的余额",
        value=f"**{balance}** 月光币",
        inline=True
    )
    
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
        value=f"**{balance}** 月光币",
        inline=True
    )
    
    # 月月的反馈
    embed.add_field(
        name="💬 月月说",
        value=f"「{game.get_dealer_remark()}」",
        inline=False
    )
    
    return embed