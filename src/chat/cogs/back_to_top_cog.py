# -*- coding: utf-8 -*-

"""
回顶功能 Cog
当用户在帖子中发送"/回顶"、"回顶"、"回到顶楼"、"/回到顶楼"时，
自动发送帖子顶楼的链接，并在3分钟后自动删除消息。
"""

import discord
from discord.ext import commands
import logging
import asyncio
import random
import json
import os
from pathlib import Path

log = logging.getLogger(__name__)

# 仅响应这四个精确触发词
BACK_TO_TOP_TRIGGERS = {
    "/回顶",
    "回顶",
    "回到顶楼",
    "/回到顶楼",
}

# 3分钟后需删除的用户指令消息
DELETE_USER_MESSAGES = {
    "回顶",
    "/回顶",
    "/回到顶楼",
}

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

# 普通文字频道顶楼缓存文件
CACHE_FILE = Path(__file__).parent.parent.parent / "data" / "channel_top_cache.json"


def _load_channel_top_cache() -> dict:
    """加载普通频道顶楼缓存"""
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_channel_top_cache(cache: dict) -> None:
    """保存普通频道顶楼缓存"""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"保存频道顶楼缓存失败: {e}")


class BackToTopCog(commands.Cog):
    """处理帖子回顶功能的Cog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.channel_top_cache: dict[str, str] = _load_channel_top_cache()

    def _is_back_to_top_request(self, content: str) -> bool:
        """检查消息内容是否为回顶请求"""
        return content in BACK_TO_TOP_TRIGGERS

    def _should_delete_user_message(self, content: str) -> bool:
        """判断是否需要在延迟后删除用户触发消息"""
        return content in DELETE_USER_MESSAGES

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

    async def _get_regular_channel_first_message_link(self, channel) -> str | None:
        """获取普通文字频道第一条消息的链接（带缓存）"""
        channel_id = str(channel.id)
        # 先查缓存
        if channel_id in self.channel_top_cache:
            return self.channel_top_cache[channel_id]
        # 查不到则从历史记录获取第一条消息
        try:
            async for first_msg in channel.history(limit=1, oldest_first=True):
                link = first_msg.jump_url
                self.channel_top_cache[channel_id] = link
                _save_channel_top_cache(self.channel_top_cache)
                return link
        except Exception as e:
            log.error(f"获取普通频道顶楼链接时出错: {e}", exc_info=True)
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """监听消息，检测回顶请求"""
        # 忽略机器人消息
        if message.author.bot:
            return

        # 检查是否为回顶请求
        if not self._is_back_to_top_request(message.content):
            return

        channel = message.channel

        # 帖子频道
        if isinstance(channel, discord.Thread):
            top_link = await self._get_thread_first_message_link(channel)
            channel_label = f"帖子 '{channel.name}'"
        # 普通文字频道
        elif isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            top_link = await self._get_regular_channel_first_message_link(channel)
            channel_label = f"频道 '{channel.name}'"
        else:
            return
        
        if not top_link:
            if isinstance(channel, discord.Thread) and channel.guild:
                top_link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}/{channel.id}"
            else:
                log.warning(f"无法获取 {channel_label} 的顶楼链接")
                return
        
        try:
            # 先给用户的请求消息打✅，表示已收到
            try:
                await message.add_reaction("✅")
            except discord.Forbidden:
                log.debug(f"无权限给消息添加反应")
            except Exception as e:
                log.debug(f"添加反应时出错: {e}")
            
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
            
            log.info(f"已发送回顶链接到{channel_label}")
            
            # 等待3分钟后删除消息
            await asyncio.sleep(DELETE_DELAY_SECONDS)
            
            try:
                await reply_msg.delete()
                log.info(f"已自动删除回顶链接消息 ({channel_label})")
            except discord.NotFound:
                # 消息已被手动删除
                pass
            except discord.Forbidden:
                log.warning(f"无权限删除回顶链接消息 ({channel_label})")

            try:
                await message.delete()
                log.info(f"已自动删除用户回顶消息 ({channel_label})")
            except discord.NotFound:
                # 用户消息已被手动删除
                pass
            except discord.Forbidden:
                log.warning(f"无权限删除用户回顶消息 ({channel_label})")
            
        except discord.Forbidden:
            log.warning(f"无权限在{channel_label} 中发送回顶链接")
        except Exception as e:
            log.error(f"发送回顶链接时出错: {e}", exc_info=True)


async def setup(bot: commands.Bot):
    """将这个Cog添加到机器人中"""
    await bot.add_cog(BackToTopCog(bot))