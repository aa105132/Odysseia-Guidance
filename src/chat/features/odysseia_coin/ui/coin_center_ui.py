# -*- coding: utf-8 -*-
"""
月光币中心 UI 组件
整合余额查看、签到、排行榜、破产补贴、21点入口等功能
"""

import discord
from discord import ui
from discord.ext import commands
import logging
from typing import Optional, List
from datetime import datetime, timezone, timedelta

from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.config.chat_config import COIN_CONFIG

log = logging.getLogger(__name__)


class CoinCenterView(ui.View):
    """月光币中心主视图"""
    
    def __init__(self, bot: commands.Bot, user: discord.User | discord.Member):
        super().__init__(timeout=180)
        self.bot = bot
        self.user = user
        self.current_page = "main"  # main, leaderboard
        self.leaderboard_page = 0
    
    async def create_main_embed(self) -> discord.Embed:
        """创建主页面嵌入"""
        user_id = self.user.id
        balance = await coin_service.get_balance(user_id)
        rank = await coin_service.get_user_rank(user_id)
        last_checkin, streak = await coin_service.get_checkin_info(user_id)
        
        # 检查今天是否已签到
        beijing_tz = timezone(timedelta(hours=8))
        today = datetime.now(beijing_tz).date()
        already_checked_in = False
        if last_checkin:
            last_date = datetime.fromisoformat(last_checkin).date()
            already_checked_in = last_date >= today
        
        embed = discord.Embed(
            title="🌙 月光币中心",
            description=f"欢迎来到月光币中心，{self.user.mention}！",
            color=discord.Color.gold()
        )
        
        # 余额信息
        embed.add_field(
            name="💰 当前余额",
            value=f"**{balance:,}** 月光币",
            inline=True
        )
        
        # 排名信息
        rank_text = f"第 **{rank}** 名" if rank else "暂无排名"
        embed.add_field(
            name="🏆 排行榜",
            value=rank_text,
            inline=True
        )
        
        # 签到信息
        if already_checked_in:
            checkin_text = f"✅ 今日已签到\n🔥 连续 **{streak}** 天"
        else:
            checkin_text = "⏳ 今日未签到"
            if streak > 0:
                checkin_text += f"\n📅 上次连续 **{streak}** 天"
        embed.add_field(
            name="📋 签到状态",
            value=checkin_text,
            inline=True
        )
        
        # 奖励说明
        embed.add_field(
            name="📖 月光币获取方式",
            value=(
                f"• **每日签到**: {COIN_CONFIG['DAILY_CHECKIN_REWARD_MIN']}-{COIN_CONFIG['DAILY_CHECKIN_REWARD_MAX']} 月光币\n"
                f"  └ 连签奖励: 每天+{COIN_CONFIG['DAILY_CHECKIN_STREAK_BONUS']}，最高+{COIN_CONFIG['DAILY_CHECKIN_MAX_STREAK_BONUS']}\n"
                f"• **每日首次对话**: {COIN_CONFIG['DAILY_FIRST_CHAT_REWARD']} 月光币\n"
                f"• **发布帖子**: {COIN_CONFIG['FORUM_POST_REWARD']} 月光币\n"
                f"• **21点游戏**: 赌一把试试运气！"
            ),
            inline=False
        )
        
        # 破产补贴提示
        if balance < COIN_CONFIG["BANKRUPTCY_THRESHOLD"]:
            embed.add_field(
                name="💸 破产救济",
                value=f"余额低于 {COIN_CONFIG['BANKRUPTCY_THRESHOLD']} 可领取 **{COIN_CONFIG['BANKRUPTCY_SUBSIDY']}** 月光币补贴！",
                inline=False
            )
        
        embed.set_thumbnail(url=self.user.display_avatar.url)
        embed.set_footer(text="使用下方按钮进行操作")
        
        return embed
    
    async def create_leaderboard_embed(self) -> discord.Embed:
        """创建排行榜嵌入"""
        leaderboard = await coin_service.get_leaderboard(limit=20)
        
        embed = discord.Embed(
            title="🏆 月光币排行榜",
            description="显示拥有最多月光币的用户",
            color=discord.Color.gold()
        )
        
        # 分页显示（每页10个）
        start_idx = self.leaderboard_page * 10
        end_idx = min(start_idx + 10, len(leaderboard))
        page_data = leaderboard[start_idx:end_idx]
        
        if not page_data:
            embed.add_field(name="暂无数据", value="当前没有排行榜数据", inline=False)
        else:
            leaderboard_text = ""
            for i, entry in enumerate(page_data, start=start_idx + 1):
                user_id = entry["user_id"]
                balance = entry["balance"]
                
                # 获取用户名
                try:
                    user = self.bot.get_user(user_id)
                    username = user.display_name if user else f"用户{user_id}"
                except Exception:
                    username = f"用户{user_id}"
                
                # 奖牌
                medal = ""
                if i == 1:
                    medal = "🥇"
                elif i == 2:
                    medal = "🥈"
                elif i == 3:
                    medal = "🥉"
                else:
                    medal = f"#{i}"
                
                leaderboard_text += f"{medal} **{username}**: {balance:,} 月光币\n"
            
            total_pages = max(1, (len(leaderboard) + 9) // 10)
            embed.add_field(
                name=f"排行榜 (第 {self.leaderboard_page + 1}/{total_pages} 页)",
                value=leaderboard_text,
                inline=False
            )
        
        # 显示当前用户排名
        user_rank = await coin_service.get_user_rank(self.user.id)
        user_balance = await coin_service.get_balance(self.user.id)
        embed.set_footer(text=f"你的排名: 第{user_rank}名 | 余额: {user_balance:,} 月光币")
        
        return embed
    
    def _update_buttons(self):
        """根据当前页面更新按钮状态"""
        for child in self.children:
            if isinstance(child, ui.Button):
                # 排行榜翻页按钮只在排行榜页面显示
                if child.custom_id in ["prev_page", "next_page"]:
                    child.disabled = self.current_page != "leaderboard"
    
    @ui.button(label="每日签到", style=discord.ButtonStyle.success, emoji="📋", row=0)
    async def checkin_button(self, interaction: discord.Interaction, button: ui.Button):
        """每日签到按钮"""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("这不是你的面板！", ephemeral=True)
            return
        
        success, message, reward, streak = await coin_service.daily_checkin(self.user.id)
        
        # 更新主页面
        self.current_page = "main"
        embed = await self.create_main_embed()
        
        if success:
            await interaction.response.edit_message(embed=embed, view=self)
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    
    @ui.button(label="破产补贴", style=discord.ButtonStyle.danger, emoji="💸", row=0)
    async def bankruptcy_button(self, interaction: discord.Interaction, button: ui.Button):
        """破产补贴按钮"""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("这不是你的面板！", ephemeral=True)
            return
        
        success, message, new_balance = await coin_service.claim_bankruptcy_subsidy(self.user.id)
        
        if success:
            # 更新主页面
            self.current_page = "main"
            embed = await self.create_main_embed()
            await interaction.response.edit_message(embed=embed, view=self)
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    
    @ui.button(label="排行榜", style=discord.ButtonStyle.primary, emoji="🏆", row=0)
    async def leaderboard_button(self, interaction: discord.Interaction, button: ui.Button):
        """排行榜按钮"""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("这不是你的面板！", ephemeral=True)
            return
        
        self.current_page = "leaderboard"
        self.leaderboard_page = 0
        embed = await self.create_leaderboard_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    @ui.button(label="21点", style=discord.ButtonStyle.secondary, emoji="🎰", row=0)
    async def blackjack_button(self, interaction: discord.Interaction, button: ui.Button):
        """21点入口按钮"""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("这不是你的面板！", ephemeral=True)
            return
        
        # 导入21点相关组件
        from src.chat.features.games.services.blackjack_game import blackjack_sessions
        from src.chat.features.games.ui.blackjack_ui import (
            BetModal, StartGameView, create_game_embed, GamePlayView
        )
        
        user_id = interaction.user.id
        
        # 检查是否已有进行中的游戏
        if blackjack_sessions.has_active_session(user_id):
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
        
        balance = await coin_service.get_balance(user_id)
        
        if balance < COIN_CONFIG["BLACKJACK_MIN_BET"]:
            await interaction.response.send_message(
                f"❌ 余额不足！至少需要 **{COIN_CONFIG['BLACKJACK_MIN_BET']}** 月光币才能玩21点。",
                ephemeral=True
            )
            return
        
        # 显示21点欢迎界面
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
                f"💰 你的余额：**{balance}** 月光币\n"
                f"📊 下注范围：**{COIN_CONFIG['BLACKJACK_MIN_BET']}** - **{COIN_CONFIG['BLACKJACK_MAX_BET']}** 月光币"
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text="点击下方按钮开始游戏，输入下注金额")
        
        view = StartGameView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @ui.button(label="刷新", style=discord.ButtonStyle.secondary, emoji="🔄", row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: ui.Button):
        """刷新按钮"""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("这不是你的面板！", ephemeral=True)
            return
        
        if self.current_page == "leaderboard":
            embed = await self.create_leaderboard_embed()
        else:
            embed = await self.create_main_embed()
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    @ui.button(label="返回主页", style=discord.ButtonStyle.secondary, emoji="🏠", row=1)
    async def back_button(self, interaction: discord.Interaction, button: ui.Button):
        """返回主页按钮"""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("这不是你的面板！", ephemeral=True)
            return
        
        self.current_page = "main"
        embed = await self.create_main_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    @ui.button(label="上一页", style=discord.ButtonStyle.secondary, emoji="⬅️", custom_id="prev_page", row=1)
    async def prev_page_button(self, interaction: discord.Interaction, button: ui.Button):
        """上一页按钮"""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("这不是你的面板！", ephemeral=True)
            return
        
        if self.current_page == "leaderboard" and self.leaderboard_page > 0:
            self.leaderboard_page -= 1
            embed = await self.create_leaderboard_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.defer()
    
    @ui.button(label="下一页", style=discord.ButtonStyle.secondary, emoji="➡️", custom_id="next_page", row=1)
    async def next_page_button(self, interaction: discord.Interaction, button: ui.Button):
        """下一页按钮"""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("这不是你的面板！", ephemeral=True)
            return
        
        if self.current_page == "leaderboard":
            leaderboard = await coin_service.get_leaderboard(limit=20)
            total_pages = max(1, (len(leaderboard) + 9) // 10)
            if self.leaderboard_page < total_pages - 1:
                self.leaderboard_page += 1
                embed = await self.create_leaderboard_embed()
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.response.defer()
        else:
            await interaction.response.defer()
    
    async def on_timeout(self):
        """超时处理"""
        for item in self.children:
            if isinstance(item, (ui.Button, ui.Select)):
                item.disabled = True