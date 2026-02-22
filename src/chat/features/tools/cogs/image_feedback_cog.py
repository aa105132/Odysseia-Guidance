# -*- coding: utf-8 -*-

"""
图片负反馈监听 Cog：
- 监听指定反应（默认 💩）
- 达到阈值后封禁对应用户绘图权限
- 自动删除被举报的图片消息
"""

import logging
from typing import Optional

import discord
from discord.ext import commands

from src.chat.utils.database import chat_db_manager

log = logging.getLogger(__name__)


class ImageFeedbackCog(commands.Cog):
    """监听图片负反馈反应并执行封禁逻辑。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    async def _get_feedback_config() -> dict:
        return await chat_db_manager.get_image_feedback_runtime_config()

    @staticmethod
    def _is_feedback_enabled(feedback_config: dict) -> bool:
        enabled_raw = feedback_config.get("ENABLED", True)
        if isinstance(enabled_raw, bool):
            return enabled_raw
        if isinstance(enabled_raw, str):
            return enabled_raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(enabled_raw)

    @staticmethod
    def _get_trigger_count(feedback_config: dict) -> int:
        try:
            trigger_count = int(feedback_config.get("BAN_TRIGGER_COUNT", 3))
        except (TypeError, ValueError):
            trigger_count = 3
        return max(1, trigger_count)

    async def _get_channel(
        self, channel_id: int
    ) -> Optional[discord.abc.GuildChannel | discord.Thread | discord.abc.PrivateChannel]:
        channel = self.bot.get_channel(channel_id)
        if channel:
            return channel

        try:
            fetched_channel = await self.bot.fetch_channel(channel_id)
            return fetched_channel
        except Exception as e:
            log.warning(f"获取频道失败（channel_id={channel_id}）: {e}")
            return None

    @staticmethod
    def _get_report_count(message: discord.Message, report_emoji: str) -> int:
        for reaction in message.reactions:
            if str(reaction.emoji) == report_emoji:
                return int(reaction.count)
        return 0

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # 1) 忽略机器人自己的反应
        if not self.bot.user:
            return
        if payload.user_id == self.bot.user.id:
            return

        # 2) 检查功能开关并处理目标反馈 emoji
        feedback_config = await self._get_feedback_config()
        if not self._is_feedback_enabled(feedback_config):
            return

        report_emoji = str(feedback_config.get("REPORT_EMOJI", "💩"))
        if str(payload.emoji) != report_emoji:
            return

        # 3) 检查是否是我们登记过的生图消息
        message_record = await chat_db_manager.get_generated_image_message(payload.message_id)
        if not message_record:
            return

        # 已处理过的消息不再重复处理
        if bool(message_record.get("processed_for_ban")):
            return

        # 4) 获取频道和消息
        channel = await self._get_channel(payload.channel_id)
        if not channel or not hasattr(channel, "fetch_message"):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            # 消息已不存在，无需继续
            return
        except Exception as e:
            log.warning(f"拉取消息失败（message_id={payload.message_id}）: {e}")
            return

        # 仅处理机器人自己发出的图片消息
        if not message.author or message.author.id != self.bot.user.id:
            return

        # 5) 统计反馈反应数量，未达到阈值直接返回
        trigger_count = self._get_trigger_count(feedback_config)
        report_count = self._get_report_count(message, report_emoji)
        if report_count < trigger_count:
            return

        # 6) 原子标记，确保并发场景下只处理一次
        first_processed = await chat_db_manager.mark_generated_image_message_processed(
            payload.message_id
        )
        if not first_processed:
            return

        # 7) 应用封禁
        try:
            target_user_id = int(message_record["user_id"])
        except (TypeError, ValueError, KeyError):
            log.warning(
                f"消息归属数据异常，无法封禁（message_id={payload.message_id}, record={message_record}）"
            )
            return

        ban_result = await chat_db_manager.apply_image_generation_ban(target_user_id)

        # 8) 删除被举报图片消息（用户要求）
        try:
            await message.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            log.warning(
                f"没有权限删除被举报图片消息（message_id={payload.message_id}, channel_id={payload.channel_id}）"
            )
        except Exception as e:
            log.warning(f"删除被举报图片消息失败（message_id={payload.message_id}）: {e}")

        # 9) 在频道提示处理结果
        duration_text = ban_result.get("duration_text") or ban_result.get(
            "remaining_text", "未知时长"
        )
        offense_count = ban_result.get("offense_count", 1)
        in_repeat_window = bool(ban_result.get("in_repeat_window", False))
        repeat_window_minutes = ban_result.get(
            "repeat_window_minutes",
            feedback_config.get("REPEAT_WINDOW_MINUTES", 60),
        )
        mode_text = "窗口内升级" if in_repeat_window else "窗口外重置"

        try:
            await channel.send(
                f"用户 <@{target_user_id}> 的图片收到过多负反馈（{report_count}/{trigger_count}），"
                f"已禁用绘图权限 {duration_text}（第 {offense_count} 档，{mode_text}，窗口 {repeat_window_minutes} 分钟），"
                "并已删除该图片消息。"
            )
        except Exception as e:
            log.warning(f"发送封禁提示消息失败（channel_id={payload.channel_id}）: {e}")

        log.info(
            f"图片负反馈触发封禁：user_id={target_user_id}, message_id={payload.message_id}, "
            f"report_count={report_count}, trigger_count={trigger_count}, "
            f"offense_count={offense_count}, duration={duration_text}, "
            f"in_repeat_window={in_repeat_window}"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ImageFeedbackCog(bot))
