# -*- coding: utf-8 -*-
import discord
from discord.ext import commands
from discord import app_commands
import logging

log = logging.getLogger(__name__)

class BackToTopCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _get_first_message_url(self, channel):
        """获取频道第一条消息的跳转链接"""
        try:
            # 尝试获取第一条消息
            # history(limit=1, oldest_first=True) 会获取最旧的一条消息
            messages = [msg async for msg in channel.history(limit=1, oldest_first=True)]
            
            if messages:
                return messages[0].jump_url
            else:
                # 如果没有消息（空频道），则给频道链接
                return f"https://discord.com/channels/{channel.guild.id}/{channel.id}"
        except Exception as e:
            log.error(f"获取回顶链接失败: {e}")
            # 降级方案：直接构造频道链接
            return f"https://discord.com/channels/{channel.guild.id}/{channel.id}"

    @app_commands.command(name="回顶", description="生成一个跳转到当前频道顶部的链接（仅自己可见）")
    async def back_to_top_slash(self, interaction: discord.Interaction):
        """生成一个跳转到当前频道顶部的链接"""
        if not interaction.channel:
            await interaction.response.send_message("无法获取当前频道信息。", ephemeral=True)
            return

        url = await self._get_first_message_url(interaction.channel)
        await interaction.response.send_message(
            f"这是本频道的[第一条消息]({url})，点击即可回顶！<得意>", 
            ephemeral=True
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """监听含有'回顶'关键词的消息"""
        if message.author.bot:
            return

        # 简单的关键词匹配
        if "回顶" in message.content:
            # 避免在其他命令中误触发，但这通常是可以接受的
            # 或者可以检查是否完全匹配
            if len(message.content.strip()) > 10: # 如果句子太长，可能只是偶然提到，不触发
                return

            url = await self._get_first_message_url(message.channel)
            try:
                await message.reply(
                    f"哼，真是懒呢...给你，[回顶链接]({url})！<傲娇>",
                    mention_author=False
                )
            except Exception as e:
                log.error(f"发送回顶回复失败: {e}")

async def setup(bot):
    await bot.add_cog(BackToTopCog(bot))