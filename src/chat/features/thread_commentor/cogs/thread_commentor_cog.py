# -*- coding: utf-8 -*-

import logging
import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timezone
from typing import Optional

from src.chat.features.thread_commentor.services.thread_commentor_service import (
    thread_commentor_service,
)
from src.chat.config.chat_config import THREAD_COMMENTOR_CONFIG, WARMUP_MESSAGES
from src.chat.features.thread_commentor.ui.warmup_consent_view import WarmupConsentView
from src.chat.features.odysseia_coin.service.coin_service import coin_service

log = logging.getLogger(__name__)


class ThreadCommentorCog(commands.Cog):
    """一个用于监听新帖子并进行评价的 Cog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._auto_speaker_task: Optional[asyncio.Task] = asyncio.create_task(
            self._auto_speaker_loop()
        )

    def cog_unload(self):
        if self._auto_speaker_task and not self._auto_speaker_task.done():
            self._auto_speaker_task.cancel()

    async def _fetch_recent_messages(
        self, target_channel: discord.TextChannel | discord.Thread, limit: int
    ) -> list[discord.Message]:
        """拉取目标会话最近消息（按时间升序）。"""
        messages = [message async for message in target_channel.history(limit=limit)]
        messages.reverse()
        return messages

    def _analyze_thread_activity(
        self, recent_messages: list[discord.Message]
    ) -> dict[str, Optional[datetime]]:
        """分析最近聊天活跃度，提取人类/机器人最后发言时间。"""
        last_human_message_at: Optional[datetime] = None
        last_human_user_id: Optional[int] = None
        last_human_username: Optional[str] = None
        last_bot_message_at: Optional[datetime] = None

        for message in reversed(recent_messages):
            if message.author.bot:
                if (
                    message.author.id == self.bot.user.id
                    and last_bot_message_at is None
                ):
                    last_bot_message_at = message.created_at
                continue

            if last_human_message_at is None:
                last_human_message_at = message.created_at
                last_human_user_id = message.author.id
                last_human_username = (
                    getattr(message.author, "display_name", None)
                    or getattr(message.author, "name", None)
                )

            if last_human_message_at and last_bot_message_at:
                break

        return {
            "last_human_message_at": last_human_message_at,
            "last_human_user_id": last_human_user_id,
            "last_human_username": last_human_username,
            "last_bot_message_at": last_bot_message_at,
        }

    async def _process_auto_speaker_thread(self, thread: discord.Thread):
        """处理单个帖子对象是否需要自动发言。"""
        if thread.archived or thread.locked:
            return

        if self.bot.user and thread.owner_id == self.bot.user.id:
            return

        me = thread.guild.me
        if me is not None:
            perms = thread.permissions_for(me)
            if (
                not perms.view_channel
                or not perms.read_message_history
                or not perms.send_messages_in_threads
            ):
                log.warning(
                    f"[ThreadCommentorCog] 对帖子 '{thread.name}' ({thread.id}) 缺少查看历史/在线程发言权限，跳过自动发言。"
                )
                return

        context_limit = max(
            5,
            int(THREAD_COMMENTOR_CONFIG.get("AUTO_CHAT_CONTEXT_MESSAGE_LIMIT", 20)),
        )
        history_limit = max(context_limit * 2, 40)
        recent_messages = await self._fetch_recent_messages(thread, history_limit)
        if not recent_messages:
            return

        latest_message = recent_messages[-1]
        if self.bot.user and latest_message.author.id == self.bot.user.id:
            return

        activity = self._analyze_thread_activity(recent_messages)
        last_human_message_at = activity["last_human_message_at"]
        if last_human_message_at is None:
            return

        now_utc = datetime.now(timezone.utc)
        last_bot_message_at = activity["last_bot_message_at"]

        message_interval = max(
            60,
            int(THREAD_COMMENTOR_CONFIG.get("AUTO_CHAT_MESSAGE_INTERVAL_SECONDS", 1800)),
        )
        idle_trigger = max(
            300,
            int(THREAD_COMMENTOR_CONFIG.get("AUTO_CHAT_IDLE_TRIGGER_SECONDS", 7200)),
        )
        idle_reminder = max(
            300,
            int(THREAD_COMMENTOR_CONFIG.get("AUTO_CHAT_IDLE_REMINDER_SECONDS", 3600)),
        )

        seconds_since_human = (now_utc - last_human_message_at).total_seconds()
        seconds_since_bot = (
            (now_utc - last_bot_message_at).total_seconds()
            if last_bot_message_at
            else None
        )

        regular_due = seconds_since_bot is None or seconds_since_bot >= message_interval
        idle_due = seconds_since_human >= idle_trigger and (
            seconds_since_bot is None or seconds_since_bot >= idle_reminder
        )

        if seconds_since_human >= idle_trigger:
            if not idle_due:
                return
            is_idle_call = True
        else:
            if not regular_due:
                return
            is_idle_call = False

        idle_minutes = int(seconds_since_human // 60)
        target_user_id = activity.get("last_human_user_id")
        target_username = activity.get("last_human_username")
        target_user_mention = f"<@{target_user_id}>" if target_user_id else None

        auto_message = await thread_commentor_service.generate_auto_target_message(
            target=thread,
            recent_messages=recent_messages,
            is_idle_call=is_idle_call,
            idle_minutes=idle_minutes,
            target_user_mention=target_user_mention,
            target_user_id=target_user_id,
            target_username=target_username,
        )
        if not auto_message:
            return

        await thread.send(auto_message)
        mode = "冷场召回" if is_idle_call else "常规暖聊"
        log.info(
            f"[ThreadCommentorCog] 已在帖子 '{thread.name}' 发送自动发言（{mode}）。"
        )

    async def _process_auto_speaker_text_channel(
        self, channel: discord.TextChannel
    ):
        """处理普通文本频道的自动发言。"""
        me = channel.guild.me
        if me is not None:
            perms = channel.permissions_for(me)
            if not perms.view_channel or not perms.send_messages:
                log.warning(
                    f"[ThreadCommentorCog] 对频道 '{channel.name}' ({channel.id}) 缺少查看/发言权限，跳过自动发言。"
                )
                return

        context_limit = max(
            5,
            int(THREAD_COMMENTOR_CONFIG.get("AUTO_CHAT_CONTEXT_MESSAGE_LIMIT", 20)),
        )
        history_limit = max(context_limit * 2, 40)
        recent_messages = await self._fetch_recent_messages(channel, history_limit)
        if not recent_messages:
            return

        latest_message = recent_messages[-1]
        if self.bot.user and latest_message.author.id == self.bot.user.id:
            return

        activity = self._analyze_thread_activity(recent_messages)
        last_human_message_at = activity["last_human_message_at"]
        if last_human_message_at is None:
            return

        now_utc = datetime.now(timezone.utc)
        last_bot_message_at = activity["last_bot_message_at"]

        message_interval = max(
            60,
            int(THREAD_COMMENTOR_CONFIG.get("AUTO_CHAT_MESSAGE_INTERVAL_SECONDS", 1800)),
        )
        idle_trigger = max(
            300,
            int(THREAD_COMMENTOR_CONFIG.get("AUTO_CHAT_IDLE_TRIGGER_SECONDS", 7200)),
        )
        idle_reminder = max(
            300,
            int(THREAD_COMMENTOR_CONFIG.get("AUTO_CHAT_IDLE_REMINDER_SECONDS", 3600)),
        )

        seconds_since_human = (now_utc - last_human_message_at).total_seconds()
        seconds_since_bot = (
            (now_utc - last_bot_message_at).total_seconds()
            if last_bot_message_at
            else None
        )

        regular_due = seconds_since_bot is None or seconds_since_bot >= message_interval
        idle_due = seconds_since_human >= idle_trigger and (
            seconds_since_bot is None or seconds_since_bot >= idle_reminder
        )

        if seconds_since_human >= idle_trigger:
            if not idle_due:
                return
            is_idle_call = True
        else:
            if not regular_due:
                return
            is_idle_call = False

        idle_minutes = int(seconds_since_human // 60)
        target_user_id = activity.get("last_human_user_id")
        target_username = activity.get("last_human_username")
        target_user_mention = f"<@{target_user_id}>" if target_user_id else None

        auto_message = await thread_commentor_service.generate_auto_target_message(
            target=channel,
            recent_messages=recent_messages,
            is_idle_call=is_idle_call,
            idle_minutes=idle_minutes,
            target_user_mention=target_user_mention,
            target_user_id=target_user_id,
            target_username=target_username,
        )
        if not auto_message:
            return

        await channel.send(auto_message)
        mode = "冷场召回" if is_idle_call else "常规暖聊"
        log.info(
            f"[ThreadCommentorCog] 已在频道 '{channel.name}' 发送自动发言（{mode}）。"
        )

    async def _process_auto_speaker_for_forum_channel(
        self, forum_channel: discord.ForumChannel
    ):
        """处理论坛频道，遍历其中可发言的活跃帖子。"""
        active_threads: dict[int, discord.Thread] = {}

        for forum_thread in forum_channel.threads:
            if forum_thread.archived or forum_thread.locked:
                continue
            active_threads[forum_thread.id] = forum_thread

        # 兜底：从 guild 级缓存补充该论坛下的活跃帖子，降低 forum_channel.threads 缓存不足导致的漏发。
        for guild_thread in forum_channel.guild.threads:
            if guild_thread.parent_id != forum_channel.id:
                continue
            if guild_thread.archived or guild_thread.locked:
                continue
            active_threads[guild_thread.id] = guild_thread

        if not active_threads:
            log.info(
                f"[ThreadCommentorCog] 论坛频道 '{forum_channel.name}' ({forum_channel.id}) 当前没有可发言的活跃帖子，跳过自动发言。"
            )
            return

        for forum_thread in active_threads.values():
            await self._process_auto_speaker_thread(forum_thread)

    async def _process_auto_speaker_for_category(
        self, category_channel: discord.CategoryChannel
    ):
        """处理子区（分类）频道，自动遍历其中论坛/文本频道。"""
        child_channels = list(category_channel.channels)
        if not child_channels:
            log.info(
                f"[ThreadCommentorCog] 子区 '{category_channel.name}' ({category_channel.id}) 下无可处理频道。"
            )
            return

        processed = 0
        for child in child_channels:
            if isinstance(child, discord.ForumChannel):
                processed += 1
                await self._process_auto_speaker_for_forum_channel(child)
                continue

            if isinstance(child, discord.TextChannel):
                processed += 1
                await self._process_auto_speaker_text_channel(child)

        if processed == 0:
            log.info(
                f"[ThreadCommentorCog] 子区 '{category_channel.name}' ({category_channel.id}) 下仅包含不支持的频道类型，跳过自动发言。"
            )

    async def _process_auto_speaker_for_thread(self, thread_id: int):
        """处理自动发言目标ID（支持帖子ID、论坛频道ID、文本频道ID、子区分类ID）。"""
        target = self.bot.get_channel(thread_id)
        if target is None:
            try:
                target = await self.bot.fetch_channel(thread_id)
            except discord.NotFound:
                log.warning(
                    f"[ThreadCommentorCog] 自动发言目标ID不存在或机器人不可见: {thread_id}。"
                    f"请确认这是帖子ID/论坛频道ID/文本频道ID/子区分类ID，且机器人有查看权限。"
                )
                return
            except Exception as e:
                log.error(
                    f"[ThreadCommentorCog] 拉取自动发言目标 {thread_id} 失败: {e}",
                    exc_info=True,
                )
                return

        if isinstance(target, discord.Thread):
            await self._process_auto_speaker_thread(target)
            return

        if isinstance(target, discord.ForumChannel):
            await self._process_auto_speaker_for_forum_channel(target)
            return

        if isinstance(target, discord.TextChannel):
            await self._process_auto_speaker_text_channel(target)
            return

        if isinstance(target, discord.CategoryChannel):
            await self._process_auto_speaker_for_category(target)
            return

        log.warning(
            f"[ThreadCommentorCog] 自动发言目标ID {thread_id} 不是帖子、论坛频道、普通文本频道或子区分类。"
        )

    async def _run_auto_speaker_cycle(self):
        """执行一轮自动发言检查。"""
        if not THREAD_COMMENTOR_CONFIG.get("AUTO_CHAT_ENABLED", False):
            return

        thread_ids = set(THREAD_COMMENTOR_CONFIG.get("AUTO_CHAT_THREAD_IDS", set()))
        if not thread_ids:
            return

        for thread_id in thread_ids:
            try:
                await self._process_auto_speaker_for_thread(int(thread_id))
            except Exception as e:
                log.error(
                    f"[ThreadCommentorCog] 自动发言处理帖子 {thread_id} 失败: {e}",
                    exc_info=True,
                )

    async def _auto_speaker_loop(self):
        """后台自动发言轮询任务。"""
        await self.bot.wait_until_ready()
        log.info("[ThreadCommentorCog] 自动发言后台任务已启动。")

        while not self.bot.is_closed():
            try:
                await self._run_auto_speaker_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(
                    f"[ThreadCommentorCog] 自动发言后台轮询失败: {e}",
                    exc_info=True,
                )

            check_interval = max(
                30,
                int(
                    THREAD_COMMENTOR_CONFIG.get(
                        "AUTO_CHAT_CHECK_INTERVAL_SECONDS", 300
                    )
                ),
            )
            await asyncio.sleep(check_interval)

    async def handle_new_thread_comment(self, thread: discord.Thread):
        """
        由中央事件处理器调用的公共方法，用于对新帖子进行暖贴评价。
        """
        # 检查发帖人是否为机器人本身，避免自我循环
        if thread.owner_id == self.bot.user.id:
            log.info(
                f"[ThreadCommentorCog] 帖子 '{thread.name}' 由机器人自己创建，跳过。"
            )
            return

        log.info(
            f"[ThreadCommentorCog] 接收到新帖子进行暖贴处理: '{thread.name}' (ID: {thread.id})"
        )

        # 获取发帖人信息
        user_id = thread.owner_id
        # 在 discord.py 2.0+ 中，thread.owner 可能为 None，需要处理
        if not thread.owner:
            log.warning(f"无法获取帖子 {thread.id} 的创建者信息，可能因为缓存不足。")
            # 尝试通过 fetch_members 获取
            try:
                owner = await thread.guild.fetch_member(user_id)
                user_nickname = owner.display_name
            except discord.NotFound:
                log.error(f"无法通过 fetch_member 找到 ID 为 {user_id} 的用户。")
                return
        else:
            user_nickname = thread.owner.display_name

        log.info(f"[ThreadCommentorCog] 帖子作者: {user_nickname} (ID: {user_id})")

        # 添加一个随机延迟，让回复看起来更自然
        delay = THREAD_COMMENTOR_CONFIG["INITIAL_DELAY_SECONDS"]
        log.info(f"[ThreadCommentorCog] 等待 {delay} 秒后发送评价...")
        await asyncio.sleep(delay)

        try:
            # 调用服务生成评价，并传递用户信息
            praise_text = await thread_commentor_service.praise_new_thread(
                thread, user_id, user_nickname
            )

            # 如果成功生成，则发送到帖子
            if praise_text:
                await thread.send(praise_text)
                log.info(
                    f"[ThreadCommentorCog] 成功发送对帖子 '{thread.name}' 的评价。"
                )

                # 检查用户是否已经做过选择
                if not await coin_service.has_made_warmup_choice(user_id):
                    try:
                        user = await self.bot.fetch_user(user_id)
                        if user:
                            view = WarmupConsentView(user_id)
                            message_content = WARMUP_MESSAGES["consent_dm"].format(
                                user_mention=f"<@{user_id}>"
                            )
                            await user.send(message_content, view=view)
                            log.info(
                                f"[ThreadCommentorCog] 已向用户 {user_nickname} (ID: {user_id}) 发送暖贴意见征求私信。"
                            )
                    except discord.errors.Forbidden:
                        log.warning(
                            f"[ThreadCommentorCog] 无法向用户 {user_nickname} (ID: {user_id}) 发送私信，可能已被屏蔽或关闭私信。"
                        )
                    except Exception as e:
                        log.error(
                            f"[ThreadCommentorCog] 向用户 {user_nickname} (ID: {user_id}) 发送私信时发生错误: {e}",
                            exc_info=True,
                        )
            else:
                log.warning(
                    f"[ThreadCommentorCog] 未能为帖子 '{thread.name}' 生成评价，或评价为空。"
                )

        except Exception as e:
            log.error(
                f"[ThreadCommentorCog] 处理帖子 '{thread.name}' 时发生未知错误: {e}",
                exc_info=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ThreadCommentorCog(bot))
