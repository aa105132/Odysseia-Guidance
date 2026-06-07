# -*- coding: utf-8 -*-
"""
21点（黑杰克）游戏 Discord Cog
使用按钮和嵌入消息，ephemeral 模式避免刷屏
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging

from src.chat.features.games.services.blackjack_game import blackjack_sessions
from src.chat.features.games.ui.blackjack_ui import (
    BetModal, StartGameView, create_game_embed, GamePlayView
)
from src.chat.features.odysseia_coin.service.coin_service import coin_service

log = logging.getLogger(__name__)


class BlackjackCog(commands.Cog):
    """处理21点游戏的Cog - 使用按钮和嵌入消息"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="二十一点", description="来一场紧张刺激的21点吧？")
    async def blackjack(self, interaction: discord.Interaction):
        """
        当用户输入 /blackjack 命令时被调用。
        使用公开消息，3分钟后自动删除
        """
        from src.chat.config.chat_config import COIN_CONFIG
        
        user_id = interaction.user.id
        min_bet = COIN_CONFIG.get("BLACKJACK_MIN_BET", 10)
        max_bet = COIN_CONFIG.get("BLACKJACK_MAX_BET", None)
        
        # 检查是否已有进行中的游戏
        if blackjack_sessions.has_active_session(user_id):
            # 获取现有游戏
            game = blackjack_sessions.get_session(user_id)
            balance = await coin_service.get_balance(user_id)
            
            embed = create_game_embed(game, balance)
            view = GamePlayView(game)
            
            await interaction.response.send_message(
                content="你有一局进行中的游戏：",
                embed=embed,
                view=view,
                ephemeral=True
            )
            return
        
        # 获取用户余额
        balance = await coin_service.get_balance(user_id)
        
        if balance < min_bet:
            await interaction.response.send_message(
                f"❌ 余额不足！至少需要 **{min_bet}** 灵石才能玩21点。\n"
                f"你目前只有 **{balance}** 灵石，去赚点钱再来吧~",
                ephemeral=True
            )
            return
        
        # 构建下注范围显示
        if max_bet is None:
            bet_range = f"**{min_bet}** 灵石起，无上限"
        else:
            bet_range = f"**{min_bet}** - **{max_bet}** 灵石"
        
        # 创建欢迎嵌入
        embed = discord.Embed(
            title="🎰 月月的21点牌桌",
            description=(
                "欢迎来到月月的赌桌！\n\n"
                "**游戏规则：**\n"
                "• 目标是让手牌点数尽量接近21点，但不能超过\n"
                "• A可以算1点或11点\n"
                "• J/Q/K都算10点\n"
                "• 两张牌21点是「黑杰克」，赔率1.5倍\n"
                "• 加倍：只能在前两张牌时使用，加倍下注后只能再抽一张\n"
                "• 投降：只能在前两张牌时使用，返还一半赌注\n\n"
                f"💰 你的余额：**{balance}** 灵石\n"
                f"📊 下注范围：{bet_range}"
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text="点击下方按钮开始游戏，输入下注金额")
        
        view = StartGameView()
        
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True
        )
        
        log.info(f"用户 {user_id} 打开了21点游戏")

    @app_commands.command(name="余额", description="查看你的灵石余额")
    async def blackjack_balance(self, interaction: discord.Interaction):
        """查看余额命令"""
        user_id = interaction.user.id
        balance = await coin_service.get_balance(user_id)
        
        embed = discord.Embed(
            title="💰 灵石余额",
            description=f"你目前拥有 **{balance}** 灵石",
            color=discord.Color.blue()
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    """将这个Cog添加到机器人中"""
    await bot.add_cog(BlackjackCog(bot))
