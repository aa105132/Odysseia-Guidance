# -*- coding: utf-8 -*-

"""
NovelAI 图像生成命令 Cog
提供 /draw (交互式绘图面板) 和 /nai预设 (画师串预设管理) 命令
"""

import logging
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, List

from src.chat.config.chat_config import NOVELAI_CONFIG
from src.chat.features.novelai_generation.services.novelai_service import novelai_service
from src.chat.utils.database import chat_db_manager

log = logging.getLogger(__name__)


class NovelAICog(commands.Cog):
    """NovelAI 图像生成功能模块"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ================================================================
    # /draw - 打开交互式绘图面板
    # ================================================================

    @app_commands.command(name="draw", description="NovelAI 绘图 - 打开交互式绘图面板")
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

    # ================================================================
    # /nai预设 - 画师串预设管理命令组
    # ================================================================

    nai_preset = app_commands.Group(name="nai预设", description="管理 NovelAI 画师串预设")

    # --- 预设名称自动补全 ---

    async def preset_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """预设名称自动补全"""
        try:
            presets = await chat_db_manager.search_novelai_presets(
                interaction.user.id, current
            )
            return [
                app_commands.Choice(
                    name=p["name"][:100], value=p["name"][:100]
                )
                for p in presets
            ][:25]
        except Exception as e:
            log.error(f"预设名称自动补全错误: {e}")
            return []

    # --- /nai预设 保存 ---

    @nai_preset.command(name="保存", description="保存一个画师串预设")
    @app_commands.describe(
        名称="预设名称（最多 50 个字符）",
        画师串="画师串内容(英文tag，最多 2000 个字符)",
        负面提示词="自定义负面提示词(可选)",
    )
    async def preset_save(
        self,
        interaction: discord.Interaction,
        名称: str,
        画师串: str,
        负面提示词: str = "",
    ):
        """保存画师串预设"""
        user_id = interaction.user.id

        # 参数校验：名称长度
        if len(名称) > 50:
            await interaction.response.send_message(
                "预设名称不能超过 50 个字符。",
                ephemeral=True,
            )
            return

        # 参数校验：画师串长度
        if len(画师串) > 2000:
            await interaction.response.send_message(
                "画师串内容不能超过 2000 个字符。",
                ephemeral=True,
            )
            return

        # 检查预设数量上限（最多 25 个）
        existing_presets = await chat_db_manager.get_novelai_presets(user_id)
        existing_names = [p["name"] for p in existing_presets]

        if len(existing_presets) >= 25 and 名称 not in existing_names:
            await interaction.response.send_message(
                f"你已保存 {len(existing_presets)} 个预设，已达上限（25 个）。请先删除一些旧预设再保存新的。",
                ephemeral=True,
            )
            return

        # 保存预设
        success = await chat_db_manager.save_novelai_preset(
            user_id=user_id,
            name=名称,
            artist_string=画师串,
            negative_prompt=负面提示词,
        )

        if success:
            embed = discord.Embed(
                title="预设已保存",
                description=f"预设名称: **{名称}**",
                color=0x2ECC71,
            )
            embed.add_field(
                name="画师串",
                value=f"```\n{画师串[:500]}\n```",
                inline=False,
            )
            if 负面提示词:
                embed.add_field(
                    name="负面提示词",
                    value=f"```\n{负面提示词[:300]}\n```",
                    inline=False,
                )
            embed.set_footer(text="使用 /draw 打开绘图面板时可选择此预设")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(
                "保存预设失败，请稍后重试。",
                ephemeral=True,
            )

    # --- /nai预设 列表 ---

    @nai_preset.command(name="列表", description="查看你保存的所有预设")
    async def preset_list(self, interaction: discord.Interaction):
        """列出用户的所有预设"""
        user_id = interaction.user.id
        presets = await chat_db_manager.get_novelai_presets(user_id)

        if not presets:
            await interaction.response.send_message(
                "你还没有保存任何预设。使用 `/nai预设 保存` 来创建一个！",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="你的 NovelAI 画师串预设",
            description=f"共 {len(presets)} / 25 个预设",
            color=0x3498DB,
        )

        for i, preset in enumerate(presets[:25]):
            # 显示名称、画师串前 50 个字符、创建时间
            artist_preview = preset["artist_string"][:50]
            if len(preset["artist_string"]) > 50:
                artist_preview += "..."

            created_at = preset.get("created_at", "未知")
            # 只取日期部分（如果是完整时间戳）
            if isinstance(created_at, str) and len(created_at) > 10:
                created_at = created_at[:10]

            embed.add_field(
                name=f"{i + 1}. {preset['name']}",
                value=f"`{artist_preview}`\n创建于: {created_at}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- /nai预设 查看 ---

    @nai_preset.command(name="查看", description="查看预设的详细内容")
    @app_commands.describe(名称="预设名称")
    @app_commands.autocomplete(名称=preset_name_autocomplete)
    async def preset_view(
        self, interaction: discord.Interaction, 名称: str
    ):
        """查看预设详情"""
        user_id = interaction.user.id
        preset = await chat_db_manager.get_novelai_preset(user_id, 名称)

        if not preset:
            await interaction.response.send_message(
                f"没有找到名为「{名称}」的预设。",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"预设: {preset['name']}",
            color=0x9B59B6,
        )
        embed.add_field(
            name="画师串",
            value=f"```\n{preset['artist_string'][:1000]}\n```",
            inline=False,
        )
        if preset.get("negative_prompt"):
            embed.add_field(
                name="负面提示词",
                value=f"```\n{preset['negative_prompt'][:500]}\n```",
                inline=False,
            )
        embed.set_footer(text=f"创建时间: {preset.get('created_at', '未知')}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- /nai预设 删除 ---

    @nai_preset.command(name="删除", description="删除一个预设")
    @app_commands.describe(名称="预设名称")
    @app_commands.autocomplete(名称=preset_name_autocomplete)
    async def preset_delete(
        self, interaction: discord.Interaction, 名称: str
    ):
        """删除预设"""
        user_id = interaction.user.id

        # 先检查是否存在
        preset = await chat_db_manager.get_novelai_preset(user_id, 名称)
        if not preset:
            await interaction.response.send_message(
                f"没有找到名为「{名称}」的预设。",
                ephemeral=True,
            )
            return

        success = await chat_db_manager.delete_novelai_preset(user_id, 名称)
        if success:
            await interaction.response.send_message(
                f"已删除预设「{名称}」。",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "删除预设失败，请稍后重试。",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(NovelAICog(bot))
