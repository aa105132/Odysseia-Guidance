# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
import logging
from typing import Optional, List
import re
import io

# 导入新的 Service
from src.chat.services.chat_service import chat_service
from src.chat.services.message_processor import message_processor
from src.chat.services.gemini_service import gemini_service
from src.chat.features.tools.functions.summarize_channel import (
    text_to_newspaper_brief_image,
)

# 导入上下文服务

# 导入数据库管理器以进行黑名单检查和斜杠命令
from src.chat.utils.database import chat_db_manager
from src.chat.config.chat_config import CHAT_ENABLED, MESSAGE_SETTINGS
from src.chat.config import chat_config
from src.chat.features.odysseia_coin.service.coin_service import coin_service

log = logging.getLogger(__name__)


class AIChatCog(commands.Cog):
    """处理AI聊天功能的Cog，包括@mention回复和斜杠命令"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 服务实例的注入已由 main.py 统一处理，此处不再需要

    def _get_text_length_without_emojis(self, text: str) -> int:
        """计算移除Discord自定义表情后的文本长度。"""
        # 匹配 <a:name:id> 或 <:name:id> 格式的表情
        emoji_pattern = r"<a?:.+?:\d+>"
        text_without_emojis = re.sub(emoji_pattern, "", text)
        return len(text_without_emojis)

    def _split_text_for_discord(self, text: str, max_length: int = 2000) -> List[str]:
        if len(text) <= max_length:
            return [text]
        return [text[i:i + max_length] for i in range(0, len(text), max_length)]

    async def _reply_text_safely(
        self, message: discord.Message, text: str, mention_author: bool = True
    ) -> List[discord.Message]:
        chunks = self._split_text_for_discord(text)
        sent_messages: List[discord.Message] = []
        for index, chunk in enumerate(chunks):
            if index == 0:
                sent_msg = await message.reply(chunk, mention_author=mention_author)
            else:
                sent_msg = await message.channel.send(chunk)
            sent_messages.append(sent_msg)
        return sent_messages

    async def _send_dm_text_safely(
        self, user: discord.abc.User, intro_text: str, text: str
    ) -> None:
        chunks = self._split_text_for_discord(text)
        if not chunks:
            return

        first_with_intro = f'{intro_text}\\n\\n{chunks[0]}'
        if len(first_with_intro) <= 2000:
            await user.send(first_with_intro)
            chunks = chunks[1:]
        else:
            await user.send(intro_text)

        for chunk in chunks:
            await user.send(chunk)

    async def _suppress_link_previews(self, sent_messages: List[discord.Message]) -> None:
        for sent_msg in sent_messages:
            try:
                await sent_msg.edit(suppress=True)
            except Exception:
                pass

    @staticmethod
    def _is_newspaper_info_tool(tool_name: str) -> bool:
        normalized = str(tool_name or "").strip()
        if not normalized:
            return False
        if normalized == "render_newspaper_brief":
            return True
        if normalized == "summarize_channel":
            return True
        return (
            gemini_service._is_summary_or_search_tool(normalized)
            and normalized != "generate_voice"
        )

    def _is_newspaper_info_context(self, last_tools: List[str]) -> bool:
        return any(self._is_newspaper_info_tool(tool_name) for tool_name in last_tools)

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
        formatted_sources = self._format_source_links(source_links)
        final_source_text = formatted_sources or str(source_text or "").strip()
        if not final_source_text:
            return
        sent_messages = await self._reply_text_safely(
            message, final_source_text, mention_author=False
        )
        await self._suppress_link_previews(sent_messages)

    async def _send_newspaper_brief_reply(
        self,
        message: discord.Message,
        body_text: str,
        source_text: str,
        source_links: List[tuple],
        provided_image_data: Optional[dict] = None,
        title: str = "月月简报",
        section_name: str = "搜索 / 总结",
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
            await message.reply(
                file=discord.File(image_file, image_filename),
                mention_author=True,
            )

        await self._reply_sources_below_image(message, source_text, source_links)
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        监听所有消息，当bot被@mention时进行回复
        """
        if not CHAT_ENABLED:
            return

        # 忽略机器人自己的消息
        if message.author.bot:
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
        async with message.channel.typing():
            # 注意：这里我们将已经处理过的数据传递下去
            response_text = await self.handle_chat_message(message, processed_data)

        # 在退出 typing 状态后发送回复
        if response_text:
            try:
                # --- 响应发送逻辑 ---
                last_tools = list(getattr(gemini_service, "last_called_tools", []) or [])
                used_web_search = "web_search" in last_tools
                tool_image_data = getattr(gemini_service, "last_tool_image_data", None)
                source_links = list(getattr(gemini_service, "last_tool_source_links", []) or [])
                body_text, source_text = self._extract_source_block(response_text)
                body_length = self._get_text_length_without_emojis(body_text or response_text)
                is_info_context = self._is_newspaper_info_context(last_tools)
                newspaper_threshold = int(
                    MESSAGE_SETTINGS.get("NEWSPAPER_BRIEF_THRESHOLD", 250)
                )

                if tool_image_data and "render_newspaper_brief" in last_tools:
                    sent = await self._send_newspaper_brief_reply(
                        message,
                        body_text=body_text or response_text,
                        source_text=source_text,
                        source_links=source_links,
                        provided_image_data=tool_image_data,
                    )
                    if sent:
                        return

                if "summarize_channel" in last_tools:
                    log.info("调用了总结工具, 尝试转为报纸摘要图发送。")
                    sent = await self._send_newspaper_brief_reply(
                        message,
                        body_text=body_text or response_text,
                        source_text=source_text,
                        source_links=source_links,
                        title="月月频道简报",
                        section_name="频道总结",
                    )
                    if sent:
                        return
                    log.error("频道总结报纸摘要图片生成失败，将作为文本尝试发送。")

                if is_info_context and body_length > newspaper_threshold:
                    sent = await self._send_newspaper_brief_reply(
                        message,
                        body_text=body_text or response_text,
                        source_text=source_text,
                        source_links=source_links,
                    )
                    if sent:
                        return
                    log.error("报纸摘要图片生成失败，将回退为文本发送。")
                    response_text = body_text or response_text

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
                    return

                if (
                    self._get_text_length_without_emojis(body_text or response_text)
                    > MESSAGE_SETTINGS["DM_THRESHOLD"]
                ):
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
                            return
                        await message.author.send(
                            f"刚刚在 {channel_mention} 频道里，你想听我说的话有点多，在这里悄悄告诉你哦：\n\n{body_text or response_text}"
                        )
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
                        return
                    return

                sent_messages = await self._reply_text_safely(
                    message, body_text or response_text, mention_author=True
                )
                if used_web_search:
                    await self._suppress_link_previews(sent_messages)
                await self._reply_sources_below_image(message, source_text, source_links)

            except discord.errors.HTTPException as e:
                log.warning(f"发送回复时发生HTTP错误: {e}")
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
