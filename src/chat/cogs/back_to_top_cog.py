# -*- coding: utf-8 -*-

"""
回顶功能 Cog
当用户在帖子中发送"回顶"、"回到顶楼"等关键词时，
自动发送帖子顶楼的链接，并在3分钟后自动删除消息。
"""

import discord
from discord.ext import commands
import logging
import asyncio
import re
import random

log = logging.getLogger(__name__)

# 回顶关键词列表
BACK_TO_TOP_KEYWORDS = [
    "回顶",
    "回到顶楼",
    "返回顶楼",
    "回顶楼",
    "顶楼链接",
    "回到顶部",
    "返回顶部",
    "回到1楼",
    "回到一楼",
    "跳转顶楼",
    "去顶楼",
    "看顶楼",
    "顶楼在哪",
    "btt",  # back to top
]

# 傲娇风格的回顶台词
TSUNDERE_RESPONSES = [
    "哼！这么简单的事情还需要我帮忙吗？给你链接，不、不是因为想帮你才给的！",
    "真是的，连顶楼都找不到吗？算了，特别给你个链接好了…才不是心疼你呢！",
    "喂，就这一次哦！别以为我每次都会帮你…才、才没有在意你呢！",
    "又迷路了吗？没办法…虽然很麻烦，但就帮你这一次！",
    "明明自己翻一下就能找到的…真拿你没办法，给你链接啦！",
    "哈？要我帮你找顶楼？好吧好吧，谁让我今天心情好呢…",
    "你这个笨蛋！下次自己记得在哪里好不好！先给你链接…",
    "呜…虽然很不想承认，但帮助迷路的人也是我的工作啦…链接给你！",
    "这种小事…也、也不是不能帮你啦！就特别给你一次机会！",
    "真是的，每次都要麻烦我…算了，看在你诚心诚意的份上！",
]

# 消息删除延迟时间（秒）
DELETE_DELAY_SECONDS = 180  # 3分钟


class BackToTopCog(commands.Cog):
    """处理帖子回顶功能的Cog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 编译正则表达式以提高效率
        pattern = "|".join(re.escape(kw) for kw in BACK_TO_TOP_KEYWORDS)
        self.keyword_pattern = re.compile(pattern, re.IGNORECASE)

    def _is_back_to_top_request(self, content: str) -> bool:
        """检查消息内容是否为回顶请求"""
        # 消息内容较短且包含关键词
        # 限制消息长度以避免误判
        if len(content) > 30:
            return False
        return bool(self.keyword_pattern.search(content))

    async def _get_thread_first_message_link(self, thread: discord.Thread) -> str | None:
        """获取帖子第一条消息（顶楼）的链接"""
        try:
            # 获取帖子的起始消息
            # 对于Forum帖子，starter_message是顶楼
            if thread.starter_message:
                return thread.starter_message.jump_url
            
            # 如果starter_message不可用，尝试获取
            try:
                starter = await thread.fetch_message(thread.id)
                return starter.jump_url
            except discord.NotFound:
                pass
            
            # 如果还是获取不到，使用历史记录获取第一条消息
            async for first_msg in thread.history(limit=1, oldest_first=True):
                return first_msg.jump_url
            
            return None
        except Exception as e:
            log.error(f"获取帖子顶楼链接时出错: {e}", exc_info=True)
            return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """监听消息，检测回顶请求"""
        # 忽略机器人消息
        if message.author.bot:
            return
        
        # 只处理帖子中的消息
        if not isinstance(message.channel, discord.Thread):
            return
        
        # 检查是否为回顶请求
        if not self._is_back_to_top_request(message.content):
            return
        
        thread = message.channel
        
        log.info(
            f"用户 {message.author.name} ({message.author.id}) "
            f"在帖子 '{thread.name}' 中请求回顶"
        )
        
        # 获取顶楼链接
        top_link = await self._get_thread_first_message_link(thread)
        
        if not top_link:
            # 如果无法获取链接，构造一个基本链接
            # 格式: https://discord.com/channels/{guild_id}/{channel_id}/{message_id}
            # 对于Thread，thread.id 通常就是第一条消息的ID
            if thread.guild:
                top_link = f"https://discord.com/channels/{thread.guild.id}/{thread.id}/{thread.id}"
            else:
                log.warning(f"无法获取帖子 {thread.id} 的顶楼链接")
                return
        
        try:
            # 随机选择一句傲娇台词
            tsundere_line = random.choice(TSUNDERE_RESPONSES)
            
            # 发送回顶链接
            embed = discord.Embed(
                title="📍 顶楼传送门",
                description=f"{tsundere_line}\n\n[👆 点击这里回到顶楼]({top_link})",
                color=discord.Color.from_rgb(255, 182, 193)  # 粉色，符合傲娇风格
            )
            embed.set_footer(text="此消息将在3分钟后自动删除 | 才不是怕刷屏呢！")
            
            reply_msg = await message.reply(embed=embed, mention_author=False)
            
            log.info(f"已发送回顶链接到帖子 '{thread.name}'")
            
            # 等待3分钟后删除消息
            await asyncio.sleep(DELETE_DELAY_SECONDS)
            
            try:
                await reply_msg.delete()
                log.info(f"已自动删除回顶链接消息 (帖子: '{thread.name}')")
            except discord.NotFound:
                # 消息已被手动删除
                pass
            except discord.Forbidden:
                log.warning(f"无权限删除回顶链接消息 (帖子: '{thread.name}')")
            
        except discord.Forbidden:
            log.warning(f"无权限在帖子 '{thread.name}' 中发送回顶链接")
        except Exception as e:
            log.error(f"发送回顶链接时出错: {e}", exc_info=True)


async def setup(bot: commands.Bot):
    """将这个Cog添加到机器人中"""
    await bot.add_cog(BackToTopCog(bot))