# -*- coding: utf-8 -*-

import logging
from datetime import timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from src.chat.features.odysseia_coin.service.shop_service import shop_service

log = logging.getLogger(__name__)


class DiscordAnnotationModal(discord.ui.Modal, title="新增 Discord 标注"):
    def __init__(self, source_message: discord.Message):
        super().__init__(timeout=300)
        self.source_message = source_message

        default_title = f"消息标注 - {source_message.author.display_name}"
        self.title_input = discord.ui.TextInput(
            label="标注标题",
            placeholder="请输入标注标题",
            style=discord.TextStyle.short,
            required=True,
            max_length=100,
            default=default_title[:100],
        )
        self.annotation_input = discord.ui.TextInput(
            label="标注内容",
            placeholder="请填写你对这条消息的标注说明",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000,
        )
        self.add_item(self.title_input)
        self.add_item(self.annotation_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        thread = interaction.channel if isinstance(interaction.channel, discord.Thread) else None
        if thread is None:
            await interaction.followup.send("❌ 仅支持在帖子内进行标注。", ephemeral=True)
            return

        if thread.owner_id != interaction.user.id:
            await interaction.followup.send("❌ 仅帖主可以进行标注。", ephemeral=True)
            return

        created_at_beijing = self.source_message.created_at.astimezone(
            timezone(timedelta(hours=8))
        )
        source_content = self.source_message.content.strip() or "（无文本内容）"
        annotation_content = (
            f"【Discord 消息标注】\n"
            f"标注人：{interaction.user.display_name} ({interaction.user.id})\n"
            f"标注时间：{created_at_beijing.strftime('%Y-%m-%d %H:%M:%S')} (UTC+8)\n"
            f"消息作者：{self.source_message.author.display_name} ({self.source_message.author.id})\n"
            f"消息链接：{self.source_message.jump_url}\n\n"
            f"【原始消息】\n{source_content}\n\n"
            f"【标注说明】\n{self.annotation_input.value.strip()}"
        )

        success = await shop_service.add_discord_annotation(
            title=self.title_input.value.strip(),
            description=annotation_content,
            author_id=interaction.user.id,
            author_name=interaction.user.display_name,
            thread_id=thread.id,
            source_url=self.source_message.jump_url,
        )

        if success:
            await interaction.followup.send("✅ 标注已保存到知识库。", ephemeral=True)
        else:
            await interaction.followup.send(
                "❌ 保存标注失败，请稍后重试或联系管理员。", ephemeral=True
            )


class DiscordAnnotationCog(commands.Cog):
    """提供消息右键“Discord 标注信息”功能。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._annotation_context_menu = app_commands.ContextMenu(
            name="Discord标注信息",
            callback=self.annotate_discord_message,
        )
        self.bot.tree.add_command(self._annotation_context_menu)

    def cog_unload(self):
        self.bot.tree.remove_command(
            self._annotation_context_menu.name, type=self._annotation_context_menu.type
        )

    async def annotate_discord_message(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        channel = interaction.channel
        if not isinstance(channel, discord.Thread):
            await interaction.response.send_message(
                "❌ 聊天频道不允许进行标注，仅支持帖子。", ephemeral=True
            )
            return

        if channel.owner_id != interaction.user.id:
            await interaction.response.send_message(
                "❌ 仅帖主可以进行标注。", ephemeral=True
            )
            return

        await interaction.response.send_modal(DiscordAnnotationModal(message))


async def setup(bot: commands.Bot):
    await bot.add_cog(DiscordAnnotationCog(bot))

