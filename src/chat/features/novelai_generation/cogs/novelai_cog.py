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
from src.chat.config.chat_config import NOVELAI_CONFIG
from src.chat.utils.database import chat_db_manager

log = logging.getLogger(__name__)


async def _sync_novelai_runtime_config_from_db() -> None:
    """将数据库中的 NovelAI 全局配置同步到运行时配置。"""
    mapping = [
        ("novelai_model", "MODEL", str),
        ("novelai_default_width", "DEFAULT_WIDTH", int),
        ("novelai_default_height", "DEFAULT_HEIGHT", int),
        ("novelai_default_steps", "DEFAULT_STEPS", int),
        ("novelai_default_scale", "DEFAULT_SCALE", float),
        ("novelai_default_sampler", "DEFAULT_SAMPLER", str),
        ("novelai_generation_cost", "IMAGE_GENERATION_COST", int),
        ("novelai_default_negative", "DEFAULT_NEGATIVE_PROMPT", str),
        ("novelai_default_artist_string", "DEFAULT_ARTIST_STRING", str),
    ]

    for db_key, config_key, caster in mapping:
        try:
            raw_value = await chat_db_manager.get_global_setting(db_key)
            if raw_value is None:
                continue
            NOVELAI_CONFIG[config_key] = caster(raw_value)
        except Exception as e:
            log.warning(f"同步 NovelAI 配置失败: {db_key} -> {config_key}, error={e}")


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

        try:
            await _sync_novelai_runtime_config_from_db()
        except Exception as e:
            log.warning(f"打开 /draw 前同步 NovelAI 运行时配置失败: {e}")

        if not novelai_service.is_available():
            await interaction.response.send_message(
                "NovelAI 绘图服务当前未启用。请联系管理员在 Dashboard 中配置。",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        session = NovelAISession()

        # 会话默认参数直接跟随 Dashboard NovelAI 全局配置
        size_label = None
        for label, (w, h) in SIZE_PRESETS.items():
            if w == session.width and h == session.height:
                size_label = label
                break
        session.size_label = size_label or f"{session.width}x{session.height}"

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
