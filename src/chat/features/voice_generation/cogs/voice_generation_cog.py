# -*- coding: utf-8 -*-

"""
语音生成斜杠命令 Cog
提供 /语音生成 交互面板：编辑文本、参数、试听、发送，并支持管理员保存音色。
"""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.chat.config import chat_config
from src.chat.features.voice_generation.services.voice_service import voice_service
from src.chat.features.voice_generation.ui.views import (
    VoiceGenerationPanelView,
    VoiceGenerationSession,
)

log = logging.getLogger(__name__)


class VoiceGenerationCog(commands.Cog):
    """语音生成功能模块。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="语音生成", description="打开语音生成面板，编辑文本并生成语音")
    @app_commands.describe(
        text="要朗读的文本（可选）",
        voice_type="音色 ID（可选）",
        speed_ratio="语速（0.2~3.0，可选）",
        pitch_ratio="音调（0.1~3.0，可选）",
        emotion="情感（可选）",
    )
    @app_commands.rename(
        text="文本",
        voice_type="音色",
        speed_ratio="语速",
        pitch_ratio="音调",
        emotion="情感",
    )
    async def voice_generation(
        self,
        interaction: discord.Interaction,
        text: Optional[str] = None,
        voice_type: Optional[str] = None,
        speed_ratio: Optional[float] = None,
        pitch_ratio: Optional[float] = None,
        emotion: Optional[str] = None,
    ):
        """/语音生成 命令实现。"""
        if not voice_service.is_available():
            await interaction.response.send_message(
                "语音服务当前不可用，请联系管理员检查配置。",
                ephemeral=True,
            )
            return

        max_text_length = max(
            20,
            int(chat_config.VOICE_CONFIG.get("MAX_TEXT_LENGTH", 500)),
        )
        normalized_text = (text or "").strip()
        if len(normalized_text) > max_text_length:
            normalized_text = normalized_text[:max_text_length]

        session = VoiceGenerationSession(
            text=normalized_text,
            voice_type=(voice_type or "").strip() or None,
            speed_ratio=speed_ratio,
            pitch_ratio=pitch_ratio,
            emotion=(emotion or "").strip() or None,
            enable_emotion=None,
            emotion_scale=None,
        )

        view = VoiceGenerationPanelView(
            author_id=interaction.user.id,
            session=session,
        )
        embed = view.build_panel_embed()

        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True,
        )

        try:
            view.message = await interaction.original_response()
        except Exception as e:
            log.debug("获取 /语音生成 面板原始消息失败（可忽略）: %s", e)

        log.info(
            "用户 %s(%s) 打开语音生成面板",
            interaction.user.display_name,
            interaction.user.id,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceGenerationCog(bot))