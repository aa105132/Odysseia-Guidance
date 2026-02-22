# -*- coding: utf-8 -*-

import discord
import logging
from typing import List, Dict, Any, Optional, Tuple, Set
import re
import asyncio
import aiohttp
from urllib.parse import urlparse

from src.chat.services.regex_service import regex_service
from src import config
from src.chat.config import chat_config
from src.chat.utils.database import chat_db_manager

log = logging.getLogger(__name__)

# 定义一个正则表达式来匹配自定义表情
# <a:emoji_name:emoji_id> (动态) 或 <:emoji_name:emoji_id> (静态)
EMOJI_REGEX = re.compile(r"<a?:(\w+):(\d+)>")
MARKDOWN_LINK_URL_REGEX = re.compile(r"\[[^\]]+\]\((https?://[^\s\)]+)\)")
BARE_URL_REGEX = re.compile(r"(https?://[^\s<>\]\)]+)")

SUPPORTED_DISCORD_IMAGE_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")
IMAGE_EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}


class MessageProcessor:
    """
    负责处理和解析 discord.Message 对象，提取用于 AI 对话所需的信息。
    """

    async def _fetch_image_aio(
        self, session: aiohttp.ClientSession, url: str, proxy: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """下载图片，返回字节数据及响应中的 MIME 类型。"""
        try:
            headers = {
                "Accept": "image/gif,image/png,image/jpeg,image/webp,*/*",
                "User-Agent": "OdysseiaDiscordBot/1.0",
            }
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=5),
                proxy=proxy,
                headers=headers,
            ) as response:
                response.raise_for_status()
                image_bytes = await response.read()
                if not image_bytes:
                    return None

                content_type = (
                    (response.headers.get("Content-Type") or "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )

                return {
                    "data": image_bytes,
                    "mime_type": content_type,
                    "final_url": str(response.url),
                }
        except asyncio.TimeoutError:
            log.warning(f"下载图片超时: {url}")
            return None
        except aiohttp.ClientError as e:
            log.warning(f"下载图片失败: {url}, 错误: {e}")
            return None

    async def _extract_emojis_as_images(
        self, content: str
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """从文本中提取自定义表情，下载图片，并用占位符替换文本"""
        emoji_images = []
        tasks = []
        matches = list(EMOJI_REGEX.finditer(content))

        if not matches:
            return content, []

        proxy_url = config.PROXY_URL
        async with aiohttp.ClientSession() as session:
            for match in matches:
                emoji_name, emoji_id = match.groups()
                extension = "gif" if match.group(0).startswith("<a:") else "png"
                url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{extension}"
                tasks.append(
                    asyncio.create_task(
                        self._fetch_image_aio(session, url, proxy=proxy_url)
                    )
                )

            results = await asyncio.gather(*tasks)

        modified_content = content
        for match, fetch_result in zip(matches, results):
            if fetch_result and fetch_result.get("data"):
                emoji_name = match.group(1)
                default_mime_type = (
                    "image/gif" if match.group(0).startswith("<a:") else "image/png"
                )
                response_mime_type = (fetch_result.get("mime_type") or "").lower()
                mime_type = (
                    response_mime_type
                    if response_mime_type.startswith("image/")
                    else default_mime_type
                )

                emoji_images.append(
                    {
                        "mime_type": mime_type,
                        "data": fetch_result["data"],
                        "source": "emoji",
                        "name": emoji_name,
                    }
                )
                modified_content = modified_content.replace(
                    match.group(0), f"__EMOJI_{emoji_name}__", 1
                )

        return modified_content, emoji_images

    def _guess_mime_type_from_url(self, url: str) -> Optional[str]:
        """根据 URL 后缀推断 MIME 类型。"""
        try:
            parsed = urlparse(url.strip())
            path = (parsed.path or "").lower()
        except Exception:
            return None

        for ext, mime in IMAGE_EXT_TO_MIME.items():
            if path.endswith(ext):
                return mime
        return None

    def _extract_image_urls_from_text(self, text: str) -> List[str]:
        """从文本中提取 URL（支持 Markdown 链接和裸链接），并保持顺序去重。"""
        if not text:
            return []

        ordered_urls: List[str] = []
        seen: Set[str] = set()

        def _push(url: str):
            normalized = (url or "").strip().rstrip(".,;:!?")
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            ordered_urls.append(normalized)

        for m in MARKDOWN_LINK_URL_REGEX.finditer(text):
            _push(m.group(1))

        for m in BARE_URL_REGEX.finditer(text):
            _push(m.group(1))

        return ordered_urls

    def _is_supported_discord_image_url(self, url: str) -> bool:
        """只允许 Discord CDN/Media 的图片链接，避免抓取任意站点。"""
        try:
            parsed = urlparse(url.strip())
            host = (parsed.netloc or "").lower()
            path = (parsed.path or "").lower()
        except Exception:
            return False

        if not host or not any(host.endswith(h) for h in SUPPORTED_DISCORD_IMAGE_HOSTS):
            return False

        # 优先按后缀判断常规图片链接
        if self._guess_mime_type_from_url(url) is not None:
            return True

        # 兼容 Discord Emoji 链接在极端情况下缺少扩展名的形式
        return "/emojis/" in path

    async def _extract_images_from_text_links(
        self, content: str, source: str, seen_urls: Optional[Set[str]] = None
    ) -> List[Dict[str, Any]]:
        """从文本链接下载 Discord 图片，并转换为统一图片输入结构。"""
        if not content:
            return []

        candidate_urls = self._extract_image_urls_from_text(content)
        if not candidate_urls:
            return []

        if seen_urls is None:
            seen_urls = set()

        collected_urls: List[str] = []
        for url in candidate_urls:
            if not self._is_supported_discord_image_url(url):
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            collected_urls.append(url)

        if not collected_urls:
            return []

        proxy_url = config.PROXY_URL
        async with aiohttp.ClientSession() as session:
            tasks = [
                asyncio.create_task(self._fetch_image_aio(session, url, proxy=proxy_url))
                for url in collected_urls
            ]
            results = await asyncio.gather(*tasks)

        image_data_list: List[Dict[str, Any]] = []
        for url, fetch_result in zip(collected_urls, results):
            if not fetch_result or not fetch_result.get("data"):
                continue

            image_bytes = fetch_result["data"]
            response_mime_type = (fetch_result.get("mime_type") or "").lower()
            final_url = fetch_result.get("final_url") or url
            guessed_mime_type = self._guess_mime_type_from_url(
                final_url
            ) or self._guess_mime_type_from_url(url)

            if response_mime_type.startswith("image/"):
                mime_type = response_mime_type
            elif guessed_mime_type:
                mime_type = guessed_mime_type
            else:
                log.warning(f"文本链接返回了非图片内容，已跳过: {url}")
                continue

            image_data_list.append(
                {
                    "mime_type": mime_type,
                    "data": image_bytes,
                    "source": source,
                }
            )
            log.debug(f"成功从文本链接下载图片: {url} ({mime_type})")

        return image_data_list

    async def process_message(
        self, message: discord.Message, bot: discord.Client
    ) -> Optional[Dict[str, Any]]:
        """
        处理传入的 discord 消息对象。
        如果消息来自一个不应被触发的频道（如永久面板或置顶帖子），则返回 None。
        """
        # 检查消息是否来自置顶帖子
        # 检查频道是否被禁言
        if await chat_db_manager.is_channel_muted(message.channel.id):
            log.debug(f"消息来自被禁言的频道 {message.channel.name}，已忽略。")
            return None

        # 检查消息是否来自置顶帖子
        if isinstance(message.channel, discord.Thread) and message.channel.flags.pinned:
            log.debug(f"消息来自置顶帖子 {message.channel.name}，已忽略。")
            return None

        # 检查消息是否来自配置中禁用的频道
        if message.channel.id in chat_config.DISABLED_INTERACTION_CHANNEL_IDS:
            log.debug(f"消息来自禁用的频道 {message.channel.name}，已忽略。")
            return None

        image_data_list = []
        seen_text_image_urls: Set[str] = set()
        bot_user = message.guild.me

        if message.attachments:
            image_data_list.extend(
                await self._extract_images_from_attachments(message.attachments)
            )

        # 新增：处理文本中的 Discord 图片链接（例如 [󠄀](https://cdn.discordapp.com/emojis/...webp)）
        if message.content:
            image_data_list.extend(
                await self._extract_images_from_text_links(
                    message.content,
                    source="attachment",
                    seen_urls=seen_text_image_urls,
                )
            )

        replied_message_content = ""
        if message.reference and message.reference.message_id:
            try:
                ref_msg = await message.channel.fetch_message(
                    message.reference.message_id
                )
                if ref_msg:
                    # 核心修复：使用 'in' 和 '[]' 来访问 MessageSnapshot 的数据
                    if (
                        hasattr(ref_msg, "message_snapshots")
                        and ref_msg.message_snapshots
                    ):
                        log.debug(f"检测到消息快照，处理转发消息: {ref_msg.id}")
                        snapshot_content_parts = []

                        forwarder_name = ref_msg.author.display_name
                        original_author_name = "未知作者"

                        for snapshot in ref_msg.message_snapshots:
                            # 根据 discord.py 文档，MessageSnapshot 是一个对象，必须使用属性访问。
                            # 我们使用 hasattr() 来安全地检查属性是否存在。
                            if hasattr(snapshot, "author") and snapshot.author:
                                # snapshot.author 是一个 User/Member 对象，它有 display_name 属性
                                original_author_name = snapshot.author.display_name

                            if hasattr(snapshot, "content") and snapshot.content:
                                snapshot_content_parts.append(snapshot.content)

                                # 新增：转发快照文本中的图片链接也作为“回复图片”处理
                                snapshot_link_images = (
                                    await self._extract_images_from_text_links(
                                        snapshot.content,
                                        source="replied_attachment",
                                        seen_urls=seen_text_image_urls,
                                    )
                                )
                                image_data_list.extend(snapshot_link_images)

                            if hasattr(snapshot, "embeds") and snapshot.embeds:
                                for embed in snapshot.embeds:
                                    # embed 是一个 Embed 对象
                                    if embed.title:
                                        snapshot_content_parts.append(
                                            f"标题: {embed.title}"
                                        )
                                    if embed.description:
                                        snapshot_content_parts.append(
                                            f"描述: {embed.description}"
                                        )
                                    for field in embed.fields:
                                        snapshot_content_parts.append(
                                            f"{field.name}: {field.value}"
                                        )

                            if (
                                hasattr(snapshot, "attachments")
                                and snapshot.attachments
                            ):
                                # snapshot.attachments 是 Attachment 对象的列表
                                snapshot_images = await self._extract_images_from_attachments(
                                    snapshot.attachments
                                )
                                # 标记这些图片来自回复的转发消息
                                for img in snapshot_images:
                                    img["source"] = "replied_attachment"
                                image_data_list.extend(snapshot_images)

                        snapshot_full_text = "\n".join(
                            filter(None, snapshot_content_parts)
                        ).strip()
                        if snapshot_full_text:
                            lines = snapshot_full_text.split("\n")
                            formatted_quote = "\n> ".join(lines)
                            reply_header = f"> [{forwarder_name} 转发的来自 {original_author_name} 的消息]:"
                            replied_message_content = (
                                f"{reply_header}\n> {formatted_quote}\n\n"
                            )

                    else:
                        # 对非转发消息（包括embed命令）的常规处理
                        command_name = None
                        if ref_msg.embeds:
                            for embed in ref_msg.embeds:
                                if embed.footer and embed.footer.text:
                                    footer_text = embed.footer.text
                                    if "投喂" in footer_text:
                                        command_name = "/投喂"
                                    elif "忏悔" in footer_text:
                                        command_name = "/忏悔"
                                    break  # 找到一个就够了

                        embed_texts = []
                        if ref_msg.embeds:
                            for embed in ref_msg.embeds:
                                if embed.author and embed.author.name:
                                    author_label = (
                                        "投喂者"
                                        if command_name == "/投喂"
                                        else "忏悔者"
                                        if command_name == "/忏悔"
                                        else "作者"
                                    )
                                    embed_texts.append(
                                        f"{author_label}: {embed.author.name}"
                                    )
                                if embed.title:
                                    embed_texts.append(f"标题: {embed.title}")
                                if embed.description:
                                    embed_texts.append(f"描述: {embed.description}")
                                # 根据要求，不再将 embed 中的图片链接作为文本添加到上下文中
                                # if embed.image and embed.image.url: embed_texts.append(f"[图片]: {embed.image.url}")
                                for field in embed.fields:
                                    embed_texts.append(f"{field.name}: {field.value}")
                                if embed.footer and embed.footer.text:
                                    embed_texts.append(f"页脚: {embed.footer.text}")

                        embed_content = "\n".join(embed_texts)
                        ref_content_cleaned = self._clean_message_content(
                            ref_msg.content, ref_msg.mentions, bot_user
                        )

                        # 新增：普通回复文本中的 Discord 图片链接
                        if ref_msg.content:
                            replied_link_images = (
                                await self._extract_images_from_text_links(
                                    ref_msg.content,
                                    source="replied_attachment",
                                    seen_urls=seen_text_image_urls,
                                )
                            )
                            image_data_list.extend(replied_link_images)

                        full_ref_content = [
                            ref for ref in [ref_content_cleaned, embed_content] if ref
                        ]
                        combined_content = "\n".join(full_ref_content).strip()

                        if combined_content:
                            lines = combined_content.split("\n")
                            formatted_quote = "\n> ".join(lines)

                            reply_header = ""
                            # 修复: ref_msg.embeds 是一个列表，我们应该从列表的第一个元素获取 author
                            embed_author_name = (
                                ref_msg.embeds[0].author.name
                                if ref_msg.embeds and ref_msg.embeds[0].author
                                else None
                            )

                            if ref_msg.author.id == bot_user.id and embed_author_name:
                                command_context = (
                                    f"的 {command_name} 回应"
                                    if command_name
                                    else "的回应"
                                )
                                reply_header = f"> [月月对 {embed_author_name} {command_context}]:"
                            else:
                                reply_header = f"> [{ref_msg.author.display_name}]:"

                            replied_message_content = (
                                f"{reply_header}\n> {formatted_quote}\n\n"
                            )

                        if ref_msg.attachments:
                            replied_images = await self._extract_images_from_attachments(
                                ref_msg.attachments
                            )
                            # 标记这些图片来自回复消息，以便 prompt_service 区分
                            for img in replied_images:
                                img["source"] = "replied_attachment"
                            image_data_list.extend(replied_images)

            except (discord.NotFound, discord.Forbidden):
                log.warning(
                    f"无法找到或无权访问被回复的消息 ID: {message.reference.message_id}"
                )
            except Exception as e:
                log.error(f"处理被回复消息时出错: {e}", exc_info=True)

        content_with_placeholders, emoji_images = await self._extract_emojis_as_images(
            message.content
        )
        image_data_list.extend(emoji_images)

        clean_content = self._clean_message_content(
            content_with_placeholders, message.mentions, bot_user
        )

        return {
            "user_content": clean_content,
            "replied_content": replied_message_content,
            "image_data_list": image_data_list,
        }

    async def _extract_images_from_attachments(
        self, attachments: List[discord.Attachment]
    ) -> List[Dict[str, Any]]:
        """从附件列表中提取图片数据。"""
        image_data_list = []
        for attachment in attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                try:
                    image_bytes = await attachment.read()
                    if image_bytes:
                        image_data_list.append(
                            {
                                "mime_type": attachment.content_type,
                                "data": image_bytes,
                                "source": "attachment",
                            }
                        )
                        log.debug(
                            f"成功读取图片附件: {attachment.filename}, 大小: {len(image_bytes)} 字节"
                        )
                except Exception as e:
                    log.error(f"读取图片附件 {attachment.filename} 时出错: {e}")
        return image_data_list

    def _clean_message_content(
        self, content: str, mentions: list, bot_user: discord.ClientUser
    ) -> str:
        """
        清理消息内容，将对自身的@mention替换为名字，并移除其他@mention。
        """
        content = content.replace("\\_", "_")

        for user in mentions:
            mention_str_1 = f"<@{user.id}>"
            mention_str_2 = f"<@!{user.id}>"
            if user.id == bot_user.id:
                replacement = f"@{bot_user.display_name}"
                content = content.replace(mention_str_1, replacement).replace(
                    mention_str_2, replacement
                )
            # else:
            #     # 根据新需求，不再移除对其他用户的 @mention
            #     # 这样 AI 模型就可以接收到 <@user_id> 格式的字符串并提取 ID
            #     pass

        # content = regex_service.clean_user_input(content)
        content = content.strip()

        return content


# 创建一个单例
message_processor = MessageProcessor()
