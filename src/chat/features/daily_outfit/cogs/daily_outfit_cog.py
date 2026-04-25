# -*- coding: utf-8 -*-
import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
from datetime import time, timezone, timedelta

from src.chat.features.daily_outfit.services.outfit_service import outfit_service
from src.chat.features.daily_outfit.config.outfit_constants import DEFAULT_OUTFIT_NAME

log = logging.getLogger(__name__)
BEIJING_TZ = timezone(timedelta(hours=8))


class DailyOutfitCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_outfit_task.start()

    def cog_unload(self):
        self.daily_outfit_task.cancel()

    @tasks.loop(time=time(8, 0, tzinfo=BEIJING_TZ))
    async def daily_outfit_task(self):
        """每天北京时间 8:00 自动换装"""
        from src.chat.config import chat_config
        cfg = chat_config.DAILY_OUTFIT_CONFIG

        if not cfg.get("ENABLED", True):
            log.debug("每日换装功能已禁用，跳过。")
            return

        log.info("开始执行每日自动换装...")
        try:
            result = await outfit_service.design_new_outfit()
            log.info(f"每日换装完成: {result.get('name', '未知')}")
            await self._send_notification(result)
        except Exception as e:
            log.error(f"每日换装失败: {e}", exc_info=True)

    @daily_outfit_task.before_loop
    async def before_daily_outfit_task(self):
        await self.bot.wait_until_ready()
        await outfit_service.initialize()

    async def _send_notification(self, result: dict):
        """换装后在指定频道发送通知"""
        from src.chat.config import chat_config
        channel_id = chat_config.DAILY_OUTFIT_CONFIG.get("NOTIFICATION_CHANNEL_ID", 0)
        if not channel_id:
            return

        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            log.warning(f"换装通知频道 {channel_id} 未找到。")
            return

        outfit_name = result.get("name", "今日造型")
        description = result.get("description", "")
        reasoning = result.get("reasoning", "")

        embed = discord.Embed(
            title=f"✨ 今日造型：{outfit_name}",
            description=f"今天的月月{description}",
            color=discord.Color.from_str("#FFB7C5"),
        )
        if reasoning:
            embed.set_footer(text=f"设计灵感：{reasoning}")

        try:
            await channel.send(embed=embed)
            log.info(f"换装通知已发送到频道 {channel_id}。")
        except Exception as e:
            log.error(f"发送换装通知失败: {e}", exc_info=True)

    @app_commands.command(name="换装", description="为月月更换今日服装造型（管理员专用）")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(style="指定风格偏好（可选，如：和风、运动系、哥特洛丽塔）")
    async def change_outfit(self, interaction: discord.Interaction, style: str = None):
        await interaction.response.defer(ephemeral=True)

        try:
            result = await outfit_service.design_new_outfit(force_style=style)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ 换装失败：{e}", ephemeral=True)
            return

        outfit_name = result.get("name", "今日造型")
        description = result.get("description", "")
        tags = result.get("tags", "")
        reasoning = result.get("reasoning", "")

        embed = discord.Embed(
            title=f"✅ 换装成功：{outfit_name}",
            color=discord.Color.from_str("#FFB7C5"),
        )
        embed.add_field(name="服装描述", value=description or "无", inline=False)
        embed.add_field(name="生图标签", value=f"`{tags}`" if tags else "无", inline=False)
        if reasoning:
            embed.set_footer(text=f"设计灵感：{reasoning}")

        await interaction.followup.send(embed=embed, ephemeral=True)
        await self._send_notification(result)

    @app_commands.command(name="恢复默认服装", description="将月月恢复为默认服装（管理员专用）")
    @app_commands.default_permissions(manage_guild=True)
    async def revert_outfit(self, interaction: discord.Interaction):
        await outfit_service.revert_to_default()
        await interaction.response.send_message(
            f"✅ 已恢复为默认服装：{DEFAULT_OUTFIT_NAME}", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(DailyOutfitCog(bot))
