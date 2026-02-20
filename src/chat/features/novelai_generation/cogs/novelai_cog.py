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
from src.chat.utils.database import chat_db_manager

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
        from ..ui.views import NovelAIDrawPanel, NovelAISession, SIZE_PRESETS

        if not novelai_service.is_available():
            await interaction.response.send_message(
                "NovelAI 绘图服务当前未启用。请联系管理员在 Dashboard 中配置。",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        session = NovelAISession()

        # 读取用户持久化的生成参数偏好
        try:
            generation_settings = await chat_db_manager.get_novelai_generation_settings(user_id)
            session.width = generation_settings.get("width", session.width)
            session.height = generation_settings.get("height", session.height)
            session.steps = generation_settings.get("steps", session.steps)
            session.scale = generation_settings.get("scale", session.scale)
            session.sampler = generation_settings.get("sampler", session.sampler)

            size_label = None
            for label, (w, h) in SIZE_PRESETS.items():
                if w == session.width and h == session.height:
                    size_label = label
                    break
            session.size_label = size_label or f"{session.width}x{session.height}"
        except Exception as e:
            log.warning(f"加载用户 {user_id} 的 NovelAI 生成参数偏好失败: {e}")

        # 读取用户持久化的画师串模式
        try:
            state = await chat_db_manager.get_novelai_active_preset_state(user_id)
            active_mode = state.get("active_mode", "default")
            active_preset_name = state.get("active_preset_name")

            if active_mode == "none":
                session.artist_prefix_mode = "none"
            elif active_mode == "preset" and active_preset_name:
                preset = None
                display_name = active_preset_name

                if str(active_preset_name).startswith("管理员/"):
                    admin_name = str(active_preset_name).split("/", 1)[1]
                    preset = await chat_db_manager.get_novelai_admin_preset(admin_name)
                    display_name = f"管理员/{admin_name}"
                else:
                    preset = await chat_db_manager.get_novelai_preset(user_id, active_preset_name)
                    display_name = active_preset_name

                if preset and preset.get("artist_string"):
                    session.artist_prefix_mode = "preset"
                    session.preset_name = display_name
                    session.preset_artist_string = preset["artist_string"]
                else:
                    await chat_db_manager.set_novelai_active_preset_state(user_id, "default")
            else:
                session.artist_prefix_mode = "default"
        except Exception as e:
            log.warning(f"加载用户 {user_id} 的画师串状态失败: {e}")

        panel = NovelAIDrawPanel(session=session, user_id=user_id)
        embed = panel.build_panel_embed()
        await interaction.response.send_message(embed=embed, view=panel, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(NovelAICog(bot))
