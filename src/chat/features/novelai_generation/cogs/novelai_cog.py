# -*- coding: utf-8 -*-

"""
NovelAI 图像生成命令 Cog
提供 /draw (交互式绘图面板) 命令
预设管理功能已整合到 /draw 面板内
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands

from src.chat.features.novelai_generation.services.novelai_service import novelai_service

log = logging.getLogger(__name__)


class NovelAICog(commands.Cog):
    """NovelAI 图像生成功能模块"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ================================================================
    # /draw - 打开交互式绘图面板（含预设管理）
    # ================================================================

    @app_commands.command(name="draw", description="NovelAI 绘图 - 打开交互式绘图面板（含预设管理）")
    async def draw(self, interaction: discord.Interaction):
        """打开 NovelAI 绘图面板"""
        from ..ui.views import NovelAIDrawPanel, NovelAISession

        if not novelai_service.is_available():
            await interaction.response.send_message(
                "NovelAI 绘图服务当前未启用。请联系管理员在 Dashboard 中配置。",
                ephemeral=True,
            )
            return

        session = NovelAISession()
        panel = NovelAIDrawPanel(session=session, user_id=interaction.user.id)
        embed = panel.build_panel_embed()
        await interaction.response.send_message(embed=embed, view=panel, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(NovelAICog(bot))
