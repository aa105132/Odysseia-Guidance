# -*- coding: utf-8 -*-

import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)


class DiscordAnnotationCog(commands.Cog):
    """提供消息右键“Discord 标注信息”功能（原生标注消息）。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._annotation_context_menu = app_commands.ContextMenu(
            name="Discord标注信息",
            callback=self.annotate_discord_message,
        )
        self._unannotation_context_menu = app_commands.ContextMenu(
            name="取消Discord标注",
            callback=self.unannotate_discord_message,
        )
        self.bot.tree.add_command(self._annotation_context_menu)
        self.bot.tree.add_command(self._unannotation_context_menu)

    def cog_unload(self):
        self.bot.tree.remove_command(
            self._annotation_context_menu.name, type=self._annotation_context_menu.type
        )
        self.bot.tree.remove_command(
            self._unannotation_context_menu.name,
            type=self._unannotation_context_menu.type,
        )

    async def annotate_discord_message(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        channel = interaction.channel
        # 仅允许在论坛帖子中使用，不允许普通聊天频道。
        if not isinstance(channel, discord.Thread) or not isinstance(
            channel.parent, discord.ForumChannel
        ):
            await interaction.response.send_message(
                "❌ 聊天频道不允许进行标注，仅支持帖子。", ephemeral=True
            )
            return

        if channel.owner_id != interaction.user.id:
            await interaction.response.send_message(
                "❌ 仅帖主可以进行标注。", ephemeral=True
            )
            return

        # 原生标注：直接执行 Discord pin。
        if message.pinned:
            await interaction.response.send_message(
                "ℹ️ 这条消息已经是标注状态。", ephemeral=True
            )
            return

        me = channel.guild.me
        if me is not None:
            perms = channel.permissions_for(me)
            if not perms.manage_messages:
                await interaction.response.send_message(
                    "❌ 我没有“管理消息”权限，无法执行原生标注。", ephemeral=True
                )
                return

        try:
            await message.pin(
                reason=f"由帖主 {interaction.user.id} 通过右键菜单执行原生标注"
            )
            await interaction.response.send_message(
                "✅ 已执行 Discord 原生标注。", ephemeral=True
            )
        except discord.HTTPException as e:
            log.error(f"执行原生标注失败: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ 标注失败，请检查权限或标注数量上限。", ephemeral=True
            )

    async def unannotate_discord_message(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        channel = interaction.channel
        # 仅允许在论坛帖子中使用，不允许普通聊天频道。
        if not isinstance(channel, discord.Thread) or not isinstance(
            channel.parent, discord.ForumChannel
        ):
            await interaction.response.send_message(
                "❌ 聊天频道不允许进行取消标注，仅支持帖子。", ephemeral=True
            )
            return

        if channel.owner_id != interaction.user.id:
            await interaction.response.send_message(
                "❌ 仅帖主可以取消标注。", ephemeral=True
            )
            return

        # 原生取消标注：直接执行 Discord unpin。
        if not message.pinned:
            await interaction.response.send_message(
                "ℹ️ 这条消息当前不是标注状态。", ephemeral=True
            )
            return

        me = channel.guild.me
        if me is not None:
            perms = channel.permissions_for(me)
            if not perms.manage_messages:
                await interaction.response.send_message(
                    "❌ 我没有“管理消息”权限，无法执行取消标注。", ephemeral=True
                )
                return

        try:
            await message.unpin(
                reason=f"由帖主 {interaction.user.id} 通过右键菜单执行取消标注"
            )
            await interaction.response.send_message(
                "✅ 已取消 Discord 原生标注。", ephemeral=True
            )
        except discord.HTTPException as e:
            log.error(f"执行取消标注失败: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ 取消标注失败，请检查权限。", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(DiscordAnnotationCog(bot))
