# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
import logging
from typing import Dict, Optional, List
import re
import io
from datetime import datetime
from zoneinfo import ZoneInfo

# 导入新的 Service
from src.chat.services.chat_service import chat_service
from src.chat.services.message_processor import message_processor
from src.chat.services.gemini_service import gemini_service
from src.database.database import AsyncSessionLocal
from src.database.services.dashboard_daily_stats_service import (
    dashboard_daily_stats_service,
)
from src.chat.features.tools.functions.summarize_channel import (
    text_to_newspaper_brief_image,
)
from src.chat.features.image_generation.services.gemini_imagen_service import (
    gemini_imagen_service,
)

# 导入上下文服务

# 导入数据库管理器以进行黑名单检查和斜杠命令
from src.chat.utils.database import chat_db_manager
from src.chat.config.chat_config import CHAT_ENABLED, MESSAGE_SETTINGS
from src.chat.config import chat_config
from src.chat.features.odysseia_coin.service.coin_service import coin_service

log = logging.getLogger(__name__)
BOT_CONSECUTIVE_REPLY_LIMIT = 20  # 单个频道/帖子内连续回复 bot 的轮数上限


class AIChatCog(commands.Cog):
    """处理AI聊天功能的Cog，包括@mention回复和斜杠命令"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 每个频道/帖子的连续 bot 对话计数器 {channel_id: count}
        self._bot_consecutive_counts: Dict[int, int] = {}
        # 服务实例的注入已由 main.py 统一处理，此处不再需要

    def _get_text_length_without_emojis(self, text: str) -> int:
        """计算移除Discord自定义表情后的文本长度。"""
        # 匹配 <a:name:id> 或 <:name:id> 格式的表情
        emoji_pattern = r"<a?:.+?:\d+>"
        text_without_emojis = re.sub(emoji_pattern, "", text)
        return len(text_without_emojis)

    @staticmethod
    def _build_log_preview(text: str, limit: int = 120) -> str:
        normalized_text = str(text or "").replace("\r", "\\r").replace("\n", "\\n")
        return normalized_text[:limit]

    def _split_text_for_discord(self, text: str, max_length: int = 2000) -> List[str]:
        normalized_text = str(text or "")
        if not normalized_text.strip():
            return []
        if len(normalized_text) <= max_length:
            return [normalized_text]
        return [
            normalized_text[i:i + max_length]
            for i in range(0, len(normalized_text), max_length)
        ]

    @staticmethod
    def _normalize_bot_reply_control_text(content: str) -> str:
        normalized = re.sub(r"<@!?\d+>", "", str(content or ""))
        normalized = normalized.casefold()
        normalized = re.sub(r"\s+", "", normalized)
        return normalized

    def _get_bot_reply_control_action(self, content: str) -> Optional[str]:
        normalized = self._normalize_bot_reply_control_text(content)
        pause_keywords = (
            "停止回复bot",
            "停止bot回复",
            "暂停回复bot",
            "暂停bot回复",
            "停止本轮bot回复",
            "暂停本轮bot回复",
            "停止回复机器人",
            "停止机器人回复",
            "暂停回复机器人",
            "暂停机器人回复",
        )
        resume_keywords = (
            "恢复回复bot",
            "恢复bot回复",
            "继续回复bot",
            "继续bot回复",
            "恢复回复机器人",
            "恢复机器人回复",
            "继续回复机器人",
            "继续机器人回复",
        )

        if any(keyword in normalized for keyword in pause_keywords):
            return "pause"
        if any(keyword in normalized for keyword in resume_keywords):
            return "resume"
        return None

    async def _handle_bot_reply_control_command(self, message: discord.Message) -> bool:
        action = self._get_bot_reply_control_action(getattr(message, "content", ""))
        if not action:
            return False

        scope_id = getattr(getattr(message, "channel", None), "id", None)
        if scope_id is None:
            return False

        is_pause = action == "pause"
        await chat_db_manager.set_bot_reply_paused(scope_id, is_pause)

        reply_text = (
            "好啦，这个频道/线程的 bot 回复我先停下啦。要恢复的话，再叫我“恢复回复bot”就行。"
            if is_pause
            else "这个频道/线程的 bot 回复已经恢复啦。"
        )
        await self._reply_text_safely(message, reply_text, mention_author=False)
        return True

    async def _can_reply_to_bot_message(self, message: discord.Message) -> bool:
        scope_id = getattr(getattr(message, "channel", None), "id", None)
        if scope_id is None:
            return False

        if await chat_db_manager.is_bot_reply_paused(scope_id):
            log.info(f"频道/线程 {scope_id} 已暂停 bot 回复，忽略 bot 消息。")
            return False

        # 连续 bot 对话轮数限制
        consecutive = self._bot_consecutive_counts.get(scope_id, 0)
        if consecutive >= BOT_CONSECUTIVE_REPLY_LIMIT:
            log.info(
                f"频道/帖子 {scope_id} 的连续 bot 对话已达 {BOT_CONSECUTIVE_REPLY_LIMIT} 轮上限，"
                f"忽略来自 {message.author.id} 的 bot 消息。"
            )
            return False

        return True

    async def _record_bot_reply_usage_if_needed(self, message: discord.Message) -> None:
        if getattr(getattr(message, "author", None), "bot", False):
            scope_id = getattr(message.channel, "id", None)
            if scope_id is not None:
                self._bot_consecutive_counts[scope_id] = (
                    self._bot_consecutive_counts.get(scope_id, 0) + 1
                )
                log.debug(
                    f"频道 {scope_id} 连续 bot 对话计数: "
                    f"{self._bot_consecutive_counts[scope_id]}/{BOT_CONSECUTIVE_REPLY_LIMIT}"
                )

    async def _record_dashboard_delivery_stats(
        self,
        *,
        channel_messages: int = 0,
        dm_messages: int = 0,
        image_messages: int = 0,
    ) -> None:
        if channel_messages <= 0 and dm_messages <= 0 and image_messages <= 0:
            return

        try:
            stats_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            async with AsyncSessionLocal() as session:
                await dashboard_daily_stats_service.increment_message_stats(
                    session,
                    stats_date,
                    channel_messages=channel_messages,
                    dm_messages=dm_messages,
                    image_messages=image_messages,
                )
        except Exception as error:
            log.warning(
                "记录 Dashboard 每日发送统计失败: "
                f"channel_messages={channel_messages}, "
                f"dm_messages={dm_messages}, "
                f"image_messages={image_messages}, "
                f"error={error}"
            )

    async def _reply_text_safely(
        self, message: discord.Message, text: str, mention_author: bool = True
    ) -> List[discord.Message]:
        raw_text = str(text or "")
        chunks = self._split_text_for_discord(raw_text)
        if not chunks:
            log.warning(
                "跳过发送空频道回复: "
                f"message_id={getattr(message, 'id', 'unknown')}, "
                f"channel_id={getattr(getattr(message, 'channel', None), 'id', 'unknown')}, "
                f"author_id={getattr(getattr(message, 'author', None), 'id', 'unknown')}, "
                f"raw_len={len(raw_text)}"
            )
            return []

        sent_messages: List[discord.Message] = []
        total_chunks = len(chunks)
        for index, chunk in enumerate(chunks):
            action = "reply" if index == 0 else "channel.send"
            try:
                if index == 0:
                    sent_msg = await message.reply(chunk, mention_author=mention_author)
                else:
                    sent_msg = await message.channel.send(chunk)
            except discord.HTTPException as error:
                log.warning(
                    "发送 Discord 文本失败: "
                    f"action={action}, "
                    f"message_id={getattr(message, 'id', 'unknown')}, "
                    f"channel_id={getattr(getattr(message, 'channel', None), 'id', 'unknown')}, "
                    f"author_id={getattr(getattr(message, 'author', None), 'id', 'unknown')}, "
                    f"chunk_index={index + 1}/{total_chunks}, "
                    f"chunk_len={len(chunk)}, "
                    f"mention_author={mention_author if index == 0 else False}, "
                    f"chunk_preview={self._build_log_preview(chunk)}, "
                    f"error={error}"
                )
                raise
            sent_messages.append(sent_msg)
        await self._record_dashboard_delivery_stats(
            channel_messages=len(sent_messages)
        )
        return sent_messages

    async def _send_dm_text_safely(
        self, user: discord.abc.User, intro_text: str, text: str
    ) -> None:
        raw_text = str(text or "")
        chunks = self._split_text_for_discord(raw_text)
        if not chunks:
            log.warning(
                "跳过发送空私信回复: "
                f"user_id={getattr(user, 'id', 'unknown')}, raw_len={len(raw_text)}"
            )
            return

        first_with_intro = f'{intro_text}\\n\\n{chunks[0]}'
        if len(first_with_intro) <= 2000:
            try:
                await user.send(first_with_intro)
            except discord.HTTPException as error:
                log.warning(
                    "发送 Discord 私信失败: "
                    f"action=dm.send_intro_and_chunk, "
                    f"user_id={getattr(user, 'id', 'unknown')}, "
                    f"chunk_len={len(first_with_intro)}, "
                    f"chunk_preview={self._build_log_preview(first_with_intro)}, "
                    f"error={error}"
                )
                raise
            chunks = chunks[1:]
            sent_count = 1
        else:
            try:
                await user.send(intro_text)
            except discord.HTTPException as error:
                log.warning(
                    "发送 Discord 私信失败: "
                    f"action=dm.send_intro_only, "
                    f"user_id={getattr(user, 'id', 'unknown')}, "
                    f"chunk_len={len(intro_text)}, "
                    f"chunk_preview={self._build_log_preview(intro_text)}, "
                    f"error={error}"
                )
                raise
            sent_count = 1

        total_chunks = len(chunks)
        for index, chunk in enumerate(chunks):
            try:
                await user.send(chunk)
            except discord.HTTPException as error:
                log.warning(
                    "发送 Discord 私信失败: "
                    f"action=dm.send_chunk, "
                    f"user_id={getattr(user, 'id', 'unknown')}, "
                    f"chunk_index={index + 1}/{total_chunks}, "
                    f"chunk_len={len(chunk)}, "
                    f"chunk_preview={self._build_log_preview(chunk)}, "
                    f"error={error}"
                )
                raise
            sent_count += 1
        await self._record_dashboard_delivery_stats(dm_messages=sent_count)

    async def _suppress_link_previews(self, sent_messages: List[discord.Message]) -> None:
        for sent_msg in sent_messages:
            try:
                await sent_msg.edit(suppress=True)
            except Exception:
                pass

    @staticmethod
    def _should_send_summary_image(last_tools: List[str]) -> bool:
        normalized_tools = {
            str(tool_name or "").strip()
            for tool_name in last_tools
            if str(tool_name or "").strip()
        }
        return bool(
            {"summarize_channel", "render_newspaper_brief"} & normalized_tools
        )

    def _should_send_long_reply_via_dm(self, text: str) -> bool:
        if not bool(MESSAGE_SETTINGS.get("LONG_REPLY_IN_DM_ENABLED", False)):
            return False
        return self._get_text_length_without_emojis(text) > int(
            MESSAGE_SETTINGS.get("DM_THRESHOLD", 300)
        )

    @staticmethod
    def _extract_source_block(response_text: str) -> tuple[str, str]:
        text = str(response_text or "").strip()
        if not text:
            return "", ""

        patterns = [r"\n##\s*信息来源\s*", r"\n消息源：\s*"]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            body = text[: match.start()].rstrip()
            source_text = text[match.end() :].strip()
            return body, source_text

        return text, ""

    @staticmethod
    def _format_source_links(source_links: List[tuple]) -> str:
        lines = []
        seen_urls = set()
        for title, url in source_links or []:
            clean_title = str(title or "来源链接").strip() or "来源链接"
            clean_url = str(url or "").strip()
            if not clean_url or clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)
            lines.append(f"- {clean_title}: {clean_url}")
        if not lines:
            return ""
        return "信息来源：\n" + "\n".join(lines)

    async def _reply_sources_below_image(
        self, message: discord.Message, source_text: str, source_links: List[tuple]
    ) -> None:
        if not await self._should_show_sources():
            return
        formatted_sources = self._format_source_links(source_links)
        final_source_text = formatted_sources or str(source_text or "").strip()
        if not final_source_text:
            return
        sent_messages = await self._reply_text_safely(
            message, final_source_text, mention_author=False
        )
        await self._suppress_link_previews(sent_messages)

    async def _should_show_sources(self) -> bool:
        """Dashboard可配置的消息源展示开关，默认开启"""
        try:
            from src.chat.utils.database import chat_db_manager
            val = await chat_db_manager.get_global_setting("web_search_show_sources")
            if val is not None:
                return val.lower() not in ("false", "0", "no", "off")
        except Exception:
            pass
        return True

    async def _send_newspaper_brief_reply(
        self,
        message: discord.Message,
        body_text: str,
        source_text: str,
        source_links: List[tuple],
        provided_image_data: Optional[dict] = None,
        title: str = "月月简报",
        section_name: str = "搜索 / 总结",
        send_sources: bool = True,
    ) -> bool:
        image_data = provided_image_data or getattr(gemini_service, "last_tool_image_data", None)
        image_filename = "newspaper-brief.png"
        image_bytes = None

        if image_data and image_data.get("data"):
            image_bytes = image_data.get("data")
        else:
            image_bytes = text_to_newspaper_brief_image(
                body=body_text,
                title=title,
                section_name=section_name,
            )

        if not image_bytes:
            return False

        with io.BytesIO(image_bytes) as image_file:
            try:
                await message.reply(
                    file=discord.File(image_file, image_filename),
                    mention_author=True,
                )
            except discord.HTTPException as error:
                log.warning(
                    "发送 Discord 简报图片失败: "
                    f"message_id={getattr(message, 'id', 'unknown')}, "
                    f"channel_id={getattr(getattr(message, 'channel', None), 'id', 'unknown')}, "
                    f"author_id={getattr(getattr(message, 'author', None), 'id', 'unknown')}, "
                    f"image_filename={image_filename}, "
                    f"body_len={len(str(body_text or ''))}, "
                    f"source_len={len(str(source_text or ''))}, "
                    f"error={error}"
                )
                raise

        await self._record_dashboard_delivery_stats(
            channel_messages=1,
            image_messages=1,
        )
        if send_sources:
            await self._reply_sources_below_image(message, source_text, source_links)
        return True

    async def _send_summary_image_reply(
        self,
        message: discord.Message,
        body_text: str,
        source_text: str,
        source_links: List[tuple],
        *,
        used_web_search: bool,
        provided_image_data: Optional[dict] = None,
        title: str = "月月简报",
        section_name: str = "搜索 / 总结",
    ) -> bool:
        image_bytes = None
        image_filename = "summary-image.png"

        if provided_image_data and provided_image_data.get("data"):
            image_bytes = provided_image_data.get("data")
        else:
            # 优先尝试 Imagen 生图
            summary_imagen_enabled = chat_config.FEEDING_CONFIG.get("SUMMARY_IMAGEN_ENABLED", False)
            if summary_imagen_enabled and gemini_imagen_service.is_available():
                outfit_desc = chat_config.DAILY_OUTFIT_CONFIG.get("CURRENT_OUTFIT_DESCRIPTION", "")
                outfit_hint = (
                    f"还穿着以下服装：{outfit_desc}。请在画面中体现月月的今日穿着。"
                    if outfit_desc else ""
                )
                imagen_prompt = (
                    f"为以下频道总结生成一张有趣的插图。用中文描述画面：\n"
                    f"总结内容：{body_text[:800]}\n"
                    f"要求：画面上要自然地融入总结中的关键信息和数据，"
                    f"风格可爱温馨，适合Discord社交平台展示。"
                    f"画面中可以出现月月作为讲解者。月月的完整外貌特征："
                    f"银白色长发扎成高马尾,左眼淡绿色右眼淡蓝色异色瞳,白皙肤色,"
                    f"毛茸茸的白色狐耳内侧粉色,银白色蓬松大尾巴,"
                    f"高马尾处插着银色月牙发簪,两侧戴着细微尖三角形耳坠。"
                    f"{outfit_hint}"
                )
                try:
                    log.info("尝试用 Imagen 生成总结插图")
                    summary_resolution = chat_config.FEEDING_CONFIG.get("SUMMARY_IMAGEN_RESOLUTION", "default")
                    summary_model = chat_config.FEEDING_CONFIG.get("SUMMARY_IMAGEN_MODEL", "") or None
                    img = await gemini_imagen_service.generate_single_image(
                        prompt=imagen_prompt,
                        aspect_ratio="16:9",
                        resolution=summary_resolution,
                        model_name_override=summary_model,
                    )
                    if img:
                        image_bytes = img
                        image_filename = "summary-imagen.png"
                except Exception as img_err:
                    log.warning(f"Imagen 总结生图失败，回退到报纸图: {img_err}")

            # Imagen 失败或不可用时回退到报纸图
            if not image_bytes:
                image_bytes = text_to_newspaper_brief_image(
                    body=body_text,
                    title=title,
                    section_name=section_name,
                )

        if not image_bytes:
            return False

        with io.BytesIO(image_bytes) as image_file:
            try:
                await message.reply(
                    file=discord.File(image_file, image_filename),
                    mention_author=True,
                )
            except discord.HTTPException as error:
                log.error(
                    f"发送总结图片失败，"
                    f"image_filename={image_filename}, "
                    f"body_len={len(str(body_text or ''))}, "
                    f"source_len={len(str(source_text or ''))}, "
                    f"error={error}"
                )
                raise

        await self._record_dashboard_delivery_stats(
            channel_messages=1,
            image_messages=1,
        )
        return True

    async def _send_summary_with_full_text(
        self,
        message: discord.Message,
        response_text: str,
        source_text: str,
        source_links: List[tuple],
        *,
        used_web_search: bool,
        provided_image_data: Optional[dict] = None,
        title: str = "月月简报",
        section_name: str = "搜索 / 总结",
    ) -> bool:
        sent = await self._send_summary_image_reply(
            message=message,
            body_text=response_text,
            source_text=source_text,
            source_links=source_links,
            used_web_search=used_web_search,
            provided_image_data=provided_image_data,
            title=title,
            section_name=section_name,
        )
        if not sent:
            return False

        sent_messages = await self._reply_text_safely(
            message, response_text, mention_author=False
        )
        if used_web_search:
            await self._suppress_link_previews(sent_messages)
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        监听所有消息，当bot被@mention时进行回复
        """
        if not CHAT_ENABLED:
            return

        # 忽略机器人自己的消息
        if self.bot.user and message.author.id == self.bot.user.id:
            return

        # --- 核心前置检查 ---
        # 在处理任何逻辑之前，首先检查消息是否应该被 message_processor 忽略
        # 这会处理置顶帖和禁用频道的情况
        processed_data = await message_processor.process_message(message, self.bot)
        if processed_data is None:
            # 如果返回 None，说明消息来自一个应被忽略的源（如置顶帖），直接退出
            return

        # 检查消息是否符合处理条件：私聊 或 在服务器中被@
        is_dm = message.guild is None
        is_mentioned = self.bot.user in message.mentions

        if not is_dm and not is_mentioned:
            return

        is_bot_author = getattr(message.author, "bot", False)
        scope_id = getattr(message.channel, "id", None)

        if not is_bot_author:
            # 真人消息：重置该频道的连续 bot 对话计数
            if scope_id and scope_id in self._bot_consecutive_counts:
                self._bot_consecutive_counts.pop(scope_id, None)
            if await self._handle_bot_reply_control_command(message):
                return
        else:
            if not await self._can_reply_to_bot_message(message):
                return

        # 新增：检查是否在帖子中，以及帖子创建者是否禁用了回复
        if isinstance(message.channel, discord.Thread):
            # 检查帖子的创建者
            thread_owner = message.channel.owner
            if thread_owner and await coin_service.blocks_thread_replies(
                thread_owner.id
            ):
                log.info(
                    f"帖子 '{message.channel.name}' 的创建者 {thread_owner.id} 已禁用回复，跳过消息处理。"
                )
                return

        # 黑名单检查
        if await chat_db_manager.is_user_globally_blacklisted(message.author.id):
            log.info(f"用户 {message.author.id} 在全局黑名单中，已跳过。")
            return

        # 在显示“输入中”之前执行所有前置检查
        if not await chat_service.should_process_message(message):
            return

        # 显示"正在输入"状态，直到AI响应生成完毕
        response_text = None
        try:
            async with message.channel.typing():
                response_text = await self.handle_chat_message(message, processed_data)
        except discord.HTTPException:
            # typing 被限流时静默降级，仍然正常处理消息
            response_text = await self.handle_chat_message(message, processed_data)

        # 即使 AI 选择沉默（stay_silent），也要递增 bot 连续对话计数
        if not response_text and is_bot_author and scope_id is not None:
            self._bot_consecutive_counts[scope_id] = (
                self._bot_consecutive_counts.get(scope_id, 0) + 1
            )

        # 在退出 typing 状态后发送回复
        if response_text:
            try:
                # --- 响应发送逻辑 ---
                last_tools = list(getattr(gemini_service, "last_called_tools", []) or [])
                used_web_search = "web_search" in last_tools
                tool_image_data = getattr(gemini_service, "last_tool_image_data", None)
                source_links = list(getattr(gemini_service, "last_tool_source_links", []) or [])
                body_text, source_text = self._extract_source_block(response_text)
                should_send_summary = self._should_send_summary_image(
                    last_tools
                )

                if should_send_summary:
                    # 若 Imagen 开启可用，忽略 render_newspaper_brief 工具产出，优先生成 AI 配图
                    log.info("调用了总结工具, 尝试优先生成 AI 配图。")
                    sent = await self._send_summary_with_full_text(
                        message=message,
                        response_text=response_text,
                        source_text=source_text,
                        source_links=source_links,
                        used_web_search=used_web_search,
                        provided_image_data=tool_image_data,
                        title="月月频道简报",
                        section_name="频道总结",
                    )
                    if sent:
                        await self._record_bot_reply_usage_if_needed(message)
                        return
                    log.error("频道总结图片生成失败，将作为文本尝试发送。")

                is_unrestricted = (
                    message.channel.id in chat_config.UNRESTRICTED_CHANNEL_IDS
                    or isinstance(message.channel, discord.Thread)
                )
                if is_unrestricted:
                    sent_messages = await self._reply_text_safely(
                        message, body_text or response_text, mention_author=True
                    )
                    if used_web_search:
                        await self._suppress_link_previews(sent_messages)
                    await self._reply_sources_below_image(message, source_text, source_links)
                    await self._record_bot_reply_usage_if_needed(message)
                    return

                if self._should_send_long_reply_via_dm(body_text or response_text):
                    try:
                        channel_mention = (
                            message.channel.mention
                            if isinstance(
                                message.channel, (discord.TextChannel, discord.Thread)
                            )
                            else "你们的私信"
                        )

                        if len(body_text or response_text) > 1800:
                            await self._send_dm_text_safely(
                                message.author, 'Long reply split in DM:', body_text or response_text
                            )
                            await self._record_bot_reply_usage_if_needed(message)
                            return
                        await message.author.send(
                            f"刚刚在 {channel_mention} 频道里，你想听我说的话有点多，在这里悄悄告诉你哦：\n\n{body_text or response_text}"
                        )
                        await self._record_dashboard_delivery_stats(dm_messages=1)
                        await self._record_bot_reply_usage_if_needed(message)
                        log.info(
                            f"回复因过长已通过私信发送给 {message.author.display_name}"
                        )
                    except discord.Forbidden:
                        log.warning(
                            f"无法通过私信发送给 {message.author.display_name}，将在原频道回复提示信息。"
                        )
                        sent_messages = await self._reply_text_safely(
                            message, body_text or response_text, mention_author=True
                        )
                        if used_web_search:
                            await self._suppress_link_previews(sent_messages)
                        await self._reply_sources_below_image(message, source_text, source_links)
                        await self._record_bot_reply_usage_if_needed(message)
                        return
                    return

                sent_messages = await self._reply_text_safely(
                    message, body_text or response_text, mention_author=True
                )
                if used_web_search:
                    await self._suppress_link_previews(sent_messages)
                await self._reply_sources_below_image(message, source_text, source_links)
                await self._record_bot_reply_usage_if_needed(message)

            except discord.errors.HTTPException as e:
                final_body_text = str(body_text or response_text or "")
                final_source_text = str(source_text or "")
                log.warning(
                    "发送回复时发生HTTP错误: "
                    f"message_id={getattr(message, 'id', 'unknown')}, "
                    f"channel_id={getattr(getattr(message, 'channel', None), 'id', 'unknown')}, "
                    f"author_id={getattr(getattr(message, 'author', None), 'id', 'unknown')}, "
                    f"body_len={len(final_body_text)}, "
                    f"source_len={len(final_source_text)}, "
                    f"used_web_search={used_web_search}, "
                    f"last_tools={last_tools}, "
                    f"body_preview={self._build_log_preview(final_body_text)}, "
                    f"source_preview={self._build_log_preview(final_source_text)}, "
                    f"error={e}"
                )
            except Exception as e:
                log.error(f"发送回复时发生未知错误: {e}", exc_info=True)

    async def handle_chat_message(
        self, message: discord.Message, processed_data: dict
    ) -> Optional[str]:
        """
        处理聊天消息（包括私聊和@mention），协调各个服务生成AI回复并返回其内容
        """
        try:
            # 1. MessageProcessor 的处理已前移到 on_message 中

            # 2. 使用 ChatService 获取AI回复
            # --- 新增：获取并传递位置信息 ---
            guild_name = message.guild.name if message.guild else "私信"
            location_name = ""
            if isinstance(message.channel, discord.Thread):
                # 如果是帖子（子区），显示“父频道 -> 帖子名”
                parent_channel_name = (
                    message.channel.parent.name
                    if message.channel.parent
                    else "未知频道"
                )
                location_name = f"{parent_channel_name} -> {message.channel.name}"
            elif isinstance(message.channel, discord.abc.GuildChannel):
                # 确保是服务器频道再获取名字
                location_name = message.channel.name
            else:
                # 否则（如私信），提供一个默认值
                location_name = "私信中"

            final_response = await chat_service.handle_chat_message(
                message, processed_data, guild_name, location_name
            )

            # 3. 返回回复内容
            return final_response

        except Exception as e:
            log.error(f"[AIChatCog] 处理@mention消息时发生顶层错误: {e}", exc_info=True)
            # 确保即使发生意外错误也有反馈
            return "抱歉，处理你的请求时遇到了一个未知错误。"


async def setup(bot: commands.Bot):
    """将这个Cog添加到机器人中"""
    await bot.add_cog(AIChatCog(bot))
