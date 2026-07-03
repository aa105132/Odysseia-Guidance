# -*- coding: utf-8 -*-

import discord
import logging
from typing import List, Dict, Any, Optional, Tuple, Set
import re
import asyncio
import aiohttp
from pathlib import Path
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
SUPPORTED_EXTENSIONLESS_VIDEO_HOSTS = ("artifact.anycap.cloud",)
IMAGE_EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}
VIDEO_EXT_TO_MIME = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/mp4",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
}
AUDIO_EXT_TO_MIME = {
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".m4a": "audio/m4a",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/opus",
    ".flac": "audio/flac",
    ".aiff": "audio/aiff",
    ".aif": "audio/aiff",
    ".mpeg": "audio/mpeg",
}
TEXT_ATTACHMENT_MAX_BYTES = 3 * 1024 * 1024
TEXT_ATTACHMENT_EXTENSIONS = {
    ".txt",
    ".md",
    ".log",
    ".json",
    ".jsonl",
    ".ndjson",
    ".jsonc",
    ".yml",
    ".yaml",
}
TEXT_ATTACHMENT_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "application/json",
    "application/ld+json",
    "application/vnd.api+json",
    "application/x-ndjson",
    "application/jsonlines",
    "application/jsonl",
    "text/json",
    "text/x-json",
    "application/yaml",
    "application/x-yaml",
    "text/yaml",
    "text/x-yaml",
}
TEXT_ATTACHMENT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")


class MessageProcessor:
    """
    负责处理和解析 discord.Message 对象，提取用于 AI 对话所需的信息。
    """

    async def _fetch_image_aio(
        self, session: aiohttp.ClientSession, url: str, proxy: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """下载图片/视频/音频，返回字节数据及响应中的 MIME 类型。"""
        try:
            max_video_bytes = int(
                chat_config.IMAGE_PROCESSING_CONFIG.get(
                    "VIDEO_MAX_BYTES", 64 * 1024 * 1024
                )
                or 64 * 1024 * 1024
            )
            max_audio_bytes = int(
                chat_config.IMAGE_PROCESSING_CONFIG.get(
                    "AUDIO_MAX_BYTES", 64 * 1024 * 1024
                )
                or 64 * 1024 * 1024
            )
            headers = {
                "Accept": "image/gif,image/png,image/jpeg,image/webp,video/mp4,video/webm,video/*,audio/*,*/*",
                "User-Agent": "OdysseiaDiscordBot/1.0",
            }
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
                proxy=proxy,
                headers=headers,
            ) as response:
                response.raise_for_status()
                content_type = (
                    (response.headers.get("Content-Type") or "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                content_length = response.headers.get("Content-Length")
                likely_video_by_url = self._is_supported_video_url(str(response.url)) or self._is_supported_video_url(url)
                likely_audio_by_url = self._is_supported_audio_url(str(response.url)) or self._is_supported_audio_url(url)
                if (content_type.startswith("video/") or likely_video_by_url) and content_length:
                    try:
                        if int(content_length) > max_video_bytes:
                            log.info(
                                f"视频链接超过 {max_video_bytes / 1024 / 1024:.0f}MB 限制，已跳过: {url[:120]}"
                            )
                            return None
                    except ValueError:
                        pass
                if (content_type.startswith("audio/") or likely_audio_by_url) and content_length:
                    try:
                        if int(content_length) > max_audio_bytes:
                            log.info(
                                f"音频链接超过 {max_audio_bytes / 1024 / 1024:.0f}MB 限制，已跳过: {url[:120]}"
                            )
                            return None
                    except ValueError:
                        pass

                media_bytes = await response.read()
                if not media_bytes:
                    return None
                if (content_type.startswith("video/") or likely_video_by_url) and len(media_bytes) > max_video_bytes:
                    log.info(
                        f"视频链接读取后超过 {max_video_bytes / 1024 / 1024:.0f}MB 限制，已跳过: {url[:120]}"
                    )
                    return None
                if (content_type.startswith("audio/") or likely_audio_by_url) and len(media_bytes) > max_audio_bytes:
                    log.info(
                        f"音频链接读取后超过 {max_audio_bytes / 1024 / 1024:.0f}MB 限制，已跳过: {url[:120]}"
                    )
                    return None

                return {
                    "data": media_bytes,
                    "mime_type": content_type,
                    "final_url": str(response.url),
                }
        except asyncio.TimeoutError:
            log.warning(f"下载媒体超时: {url}")
            return None
        except aiohttp.ClientError as e:
            log.warning(f"下载媒体失败: {url}, 错误: {e}")
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
        """根据 URL 后缀推断图片 MIME 类型。"""
        try:
            parsed = urlparse(url.strip())
            path = (parsed.path or "").lower()
        except Exception:
            return None

        for ext, mime in IMAGE_EXT_TO_MIME.items():
            if path.endswith(ext):
                return mime
        return None

    def _guess_video_mime_type_from_filename(self, filename: str) -> Optional[str]:
        """根据附件文件名推断视频 MIME 类型。"""
        ext = Path(filename or "").suffix.lower()
        return VIDEO_EXT_TO_MIME.get(ext)

    def _guess_video_mime_type_from_url(self, url: str) -> Optional[str]:
        """根据 URL 后缀推断视频 MIME 类型。"""
        try:
            parsed = urlparse(url.strip())
            path = (parsed.path or "").lower()
        except Exception:
            return None

        for ext, mime in VIDEO_EXT_TO_MIME.items():
            if path.endswith(ext):
                return mime
        return None

    def _guess_audio_mime_type_from_filename(self, filename: str) -> Optional[str]:
        """根据附件文件名推断音频 MIME 类型。"""
        ext = Path(filename or "").suffix.lower()
        return AUDIO_EXT_TO_MIME.get(ext)

    def _guess_audio_mime_type_from_url(self, url: str) -> Optional[str]:
        """根据 URL 后缀推断音频 MIME 类型。"""
        try:
            parsed = urlparse(url.strip())
            path = (parsed.path or "").lower()
        except Exception:
            return None

        for ext, mime in AUDIO_EXT_TO_MIME.items():
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

    def _is_image_url_by_extension(self, url: str) -> bool:
        """判断 URL 是否以图片扩展名结尾（不限制域名）。"""
        return self._guess_mime_type_from_url(url) is not None

    def _is_supported_video_url(self, url: str) -> bool:
        """判断是否允许尝试下载为视频的 URL。"""
        try:
            parsed = urlparse(url.strip())
            host = (parsed.netloc or "").lower()
        except Exception:
            return False

        if self._guess_video_mime_type_from_url(url) is not None:
            return True
        # Discord CDN 同时承载图片和视频，不能只凭域名判成视频；
        # 无扩展名视频 artifact 目前只对已知 artifact 域名放行。
        return bool(
            host
            and any(host.endswith(video_host) for video_host in SUPPORTED_EXTENSIONLESS_VIDEO_HOSTS)
        )

    def _is_supported_audio_url(self, url: str) -> bool:
        """判断是否允许尝试下载为音频的 URL。"""
        return self._guess_audio_mime_type_from_url(url) is not None

    async def _extract_images_from_text_links(
        self, content: str, source: str, seen_urls: Optional[Set[str]] = None
    ) -> List[Dict[str, Any]]:
        """从文本链接下载图片/视频/音频，支持常见媒体后缀。"""
        if not content:
            return []

        candidate_urls = self._extract_image_urls_from_text(content)
        if not candidate_urls:
            return []

        if seen_urls is None:
            seen_urls = set()

        collected_urls: List[str] = []
        for url in candidate_urls:
            if url in seen_urls:
                continue
            # Discord CDN 图片、常见图片/视频/音频后缀、已知视频 artifact 链接都允许。
            if (
                self._is_supported_discord_image_url(url)
                or self._is_image_url_by_extension(url)
                or self._is_supported_video_url(url)
                or self._is_supported_audio_url(url)
            ):
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
            guessed_mime_type = (
                self._guess_mime_type_from_url(final_url)
                or self._guess_mime_type_from_url(url)
                or self._guess_video_mime_type_from_url(final_url)
                or self._guess_video_mime_type_from_url(url)
                or self._guess_audio_mime_type_from_url(final_url)
                or self._guess_audio_mime_type_from_url(url)
            )

            if response_mime_type.startswith(("image/", "video/", "audio/")):
                mime_type = response_mime_type
            elif guessed_mime_type:
                mime_type = guessed_mime_type
            elif response_mime_type in {"application/octet-stream", "binary/octet-stream"} and self._is_supported_video_url(url):
                mime_type = "video/mp4"
            elif response_mime_type in {"application/octet-stream", "binary/octet-stream"} and self._is_supported_audio_url(url):
                mime_type = self._guess_audio_mime_type_from_url(url) or "audio/mp3"
            else:
                log.warning(f"文本链接返回了非图片/视频/音频内容，已跳过: {url}")
                continue

            image_data_list.append(
                {
                    "mime_type": mime_type,
                    "data": image_bytes,
                    "source": source,
                    "filename": Path(urlparse(final_url or url).path or "").name or None,
                    "url": final_url or url,
                }
            )
            media_kind = "视频" if mime_type.startswith("video/") else ("音频" if mime_type.startswith("audio/") else "图片")
            log.debug(f"成功从文本链接下载{media_kind}: {url} ({mime_type})")

        return image_data_list

    def _is_supported_text_attachment(self, attachment: discord.Attachment) -> bool:
        """判断附件是否属于可直接全文注入上下文的纯文本附件。"""
        filename = getattr(attachment, "filename", "") or ""
        ext = Path(filename).suffix.lower()
        content_type = (
            (getattr(attachment, "content_type", "") or "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )

        if ext in TEXT_ATTACHMENT_EXTENSIONS:
            return True
        if content_type in TEXT_ATTACHMENT_MIME_TYPES:
            return True
        if content_type.endswith("+json"):
            return True
        return bool(content_type and content_type.startswith("text/"))

    def _decode_text_attachment_bytes(self, file_bytes: bytes) -> Optional[str]:
        """按约定编码顺序解码文本附件；疑似二进制内容返回 None。"""
        if b"\x00" in file_bytes:
            return None

        for encoding in TEXT_ATTACHMENT_ENCODINGS:
            try:
                return file_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return None

    async def _extract_text_blocks_from_attachments(
        self, attachments: List[discord.Attachment], label: str
    ) -> List[str]:
        """读取可支持的纯文本附件，并转成可直接拼接进 prompt 的全文块。"""
        text_blocks: List[str] = []

        for attachment in attachments:
            if not self._is_supported_text_attachment(attachment):
                continue

            filename = getattr(attachment, "filename", "未命名附件")
            attachment_size = getattr(attachment, "size", None)
            if isinstance(attachment_size, int) and attachment_size > TEXT_ATTACHMENT_MAX_BYTES:
                log.info(
                    f"文本附件超过 {TEXT_ATTACHMENT_MAX_BYTES} 字节限制，已跳过: {filename}"
                )
                continue

            try:
                file_bytes = await attachment.read()
            except Exception as e:
                log.error(f"读取文本附件 {filename} 时出错: {e}")
                continue

            if not file_bytes:
                continue
            if len(file_bytes) > TEXT_ATTACHMENT_MAX_BYTES:
                log.info(
                    f"文本附件读取后确认超过 {TEXT_ATTACHMENT_MAX_BYTES} 字节限制，已跳过: {filename}"
                )
                continue

            decoded_text = self._decode_text_attachment_bytes(file_bytes)
            if decoded_text is None:
                log.warning(f"文本附件无法按支持编码解码或疑似二进制，已跳过: {filename}")
                continue

            text_blocks.append(
                f"[{label}: {filename}]\n[附件全文开始]\n{decoded_text}\n[附件全文结束]"
            )
            log.debug(f"成功读取文本附件: {filename}, 大小: {len(file_bytes)} 字节")

        return text_blocks

    def _append_text_blocks(self, base_content: str, text_blocks: List[str]) -> str:
        """将文本附件块追加到已有内容末尾，保持简单换行边界。"""
        if not text_blocks:
            return base_content

        extra_content = "\n\n".join(block for block in text_blocks if block)
        if not extra_content:
            return base_content
        if not base_content:
            return extra_content
        return f"{base_content}\n\n{extra_content}"

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
        bot_user = message.guild.me if message.guild else self.bot.user
        current_text_attachment_blocks: List[str] = []

        if message.attachments:
            image_data_list.extend(
                await self._extract_images_from_attachments(message.attachments)
            )
            current_text_attachment_blocks = (
                await self._extract_text_blocks_from_attachments(
                    message.attachments, label="用户上传的文本附件"
                )
            )

        # 处理文本中的 Discord 图片链接（例如 [󠄀](https://cdn.discordapp.com/emojis/...webp)）
        if message.content:
            image_data_list.extend(
                await self._extract_images_from_text_links(
                    message.content,
                    source="attachment",
                    seen_urls=seen_text_image_urls,
                )
            )

        # 从 embed 中提取图片（用户贴的链接会被 Discord 自动嵌入为 embed，
        # embed 的 proxy_url 经过 Discord 代理，比原始 URL 更可靠）
        if message.embeds:
            embed_images = await self._extract_images_from_embeds(
                message.embeds, seen_urls=seen_text_image_urls
            )
            image_data_list.extend(embed_images)

        # 提取贴纸/动图图片（Discord 贴纸不在 attachments 中）。
        # 不再要求 image_data_list 为空：用户可能同时发图和贴纸，二者都应进入视觉上下文。
        image_data_list.extend(
            await self._extract_sticker_images_from_message(message, source="sticker")
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
                                snapshot_content_parts.extend(
                                    await self._extract_text_blocks_from_attachments(
                                        snapshot.attachments,
                                        label="转发消息包含文本附件",
                                    )
                                )

                            snapshot_sticker_images = (
                                await self._extract_sticker_images_from_message(
                                    snapshot,
                                    source="replied_attachment",
                                )
                            )
                            if snapshot_sticker_images:
                                image_data_list.extend(snapshot_sticker_images)
                                sticker_names = [
                                    img.get("sticker_name") or "未命名贴纸"
                                    for img in snapshot_sticker_images[:3]
                                ]
                                snapshot_content_parts.append(
                                    f"[转发消息包含贴纸: {'、'.join(sticker_names)}]"
                                )

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
                        replied_text_attachment_blocks: List[str] = []

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
                            ref
                            for ref in [
                                ref_content_cleaned,
                                embed_content,
                                "\n\n".join(replied_text_attachment_blocks),
                            ]
                            if ref
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
                            replied_text_attachment_blocks = (
                                await self._extract_text_blocks_from_attachments(
                                    ref_msg.attachments,
                                    label="回复消息包含文本附件",
                                )
                            )
                            full_ref_content = [
                                ref
                                for ref in [
                                    ref_content_cleaned,
                                    embed_content,
                                    "\n\n".join(replied_text_attachment_blocks),
                                ]
                                if ref
                            ]
                            combined_content = "\n".join(full_ref_content).strip()
                            if combined_content:
                                lines = combined_content.split("\n")
                                formatted_quote = "\n> ".join(lines)

                                reply_header = ""
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

                        # 从回复消息的 embed 中提取图片（proxy_url 回退）
                        if ref_msg.embeds and not any(
                            img.get("source") == "replied_attachment"
                            for img in image_data_list
                        ):
                            replied_embed_images = await self._extract_images_from_embeds(
                                ref_msg.embeds, seen_urls=seen_text_image_urls
                            )
                            for img in replied_embed_images:
                                img["source"] = "replied_attachment"
                            image_data_list.extend(replied_embed_images)

                        # 从回复消息中提取贴纸图片。贴纸不是 attachments，必须单独读取；
                        # 即使回复消息已有图片附件，也保留贴纸，避免“贴纸看不到”。
                        replied_sticker_images = (
                            await self._extract_sticker_images_from_message(
                                ref_msg,
                                source="replied_attachment",
                            )
                        )
                        if replied_sticker_images:
                            image_data_list.extend(replied_sticker_images)
                            if not replied_message_content:
                                sticker_names = [
                                    img.get("sticker_name") or "未命名贴纸"
                                    for img in replied_sticker_images[:3]
                                ]
                                replied_message_content = (
                                    f"> [{ref_msg.author.display_name}]:\n"
                                    f"> [发送了贴纸: {'、'.join(sticker_names)}]\n\n"
                                )

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
        clean_content = self._append_text_blocks(
            clean_content, current_text_attachment_blocks
        )

        return {
            "user_content": clean_content,
            "replied_content": replied_message_content,
            "image_data_list": image_data_list,
        }

    async def _extract_images_from_embeds(
        self,
        embeds: List[discord.Embed],
        seen_urls: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        从消息 embed 中提取图片数据。

        当用户贴了 Discord CDN 图片链接时，Discord 会自动生成 embed，
        其中 proxy_url 是经过 Discord 代理的 URL，不受 CDN 签名参数限制，
        比原始 url 更可靠。
        """
        if not embeds:
            return []

        if seen_urls is None:
            seen_urls = set()

        candidate_urls: List[str] = []

        for embed in embeds:
            # 优先使用 proxy_url（经过 Discord 代理，更可靠）
            if getattr(embed, "image", None):
                proxy = getattr(embed.image, "proxy_url", None)
                url = getattr(embed.image, "url", None)
                chosen = proxy or url
                if chosen and chosen not in seen_urls:
                    seen_urls.add(chosen)
                    candidate_urls.append(chosen)

            if getattr(embed, "thumbnail", None):
                proxy = getattr(embed.thumbnail, "proxy_url", None)
                url = getattr(embed.thumbnail, "url", None)
                chosen = proxy or url
                if chosen and chosen not in seen_urls:
                    seen_urls.add(chosen)
                    candidate_urls.append(chosen)

            if getattr(embed, "video", None):
                proxy = getattr(embed.video, "proxy_url", None)
                url = getattr(embed.video, "url", None)
                chosen = proxy or url
                if chosen and chosen not in seen_urls:
                    seen_urls.add(chosen)
                    candidate_urls.append(chosen)

            embed_texts: List[str] = []
            for attr_name in ("title", "description", "url"):
                value = getattr(embed, attr_name, None)
                if value:
                    embed_texts.append(str(value))
            for field in getattr(embed, "fields", []) or []:
                field_name = getattr(field, "name", None)
                field_value = getattr(field, "value", None)
                if field_name:
                    embed_texts.append(str(field_name))
                if field_value:
                    embed_texts.append(str(field_value))
            for url in self._extract_image_urls_from_text("\n".join(embed_texts)):
                if not url or url in seen_urls:
                    continue
                if (
                    self._is_supported_discord_image_url(url)
                    or self._is_image_url_by_extension(url)
                    or self._is_supported_video_url(url)
                    or self._is_supported_audio_url(url)
                ):
                    seen_urls.add(url)
                    candidate_urls.append(url)

        if not candidate_urls:
            return []

        proxy_url = config.PROXY_URL
        image_data_list: List[Dict[str, Any]] = []

        async with aiohttp.ClientSession() as session:
            tasks = [
                asyncio.create_task(self._fetch_image_aio(session, url, proxy=proxy_url))
                for url in candidate_urls
            ]
            results = await asyncio.gather(*tasks)

        for url, fetch_result in zip(candidate_urls, results):
            if not fetch_result or not fetch_result.get("data"):
                log.warning(f"从 embed 下载图片失败: {url[:120]}")
                continue

            image_bytes = fetch_result["data"]
            response_mime_type = (fetch_result.get("mime_type") or "").lower()
            final_url = fetch_result.get("final_url") or url
            guessed_mime_type = (
                self._guess_mime_type_from_url(final_url)
                or self._guess_mime_type_from_url(url)
                or self._guess_video_mime_type_from_url(final_url)
                or self._guess_video_mime_type_from_url(url)
                or self._guess_audio_mime_type_from_url(final_url)
                or self._guess_audio_mime_type_from_url(url)
            )

            if response_mime_type.startswith(("image/", "video/", "audio/")):
                mime_type = response_mime_type
            elif guessed_mime_type:
                mime_type = guessed_mime_type
            elif response_mime_type in {"application/octet-stream", "binary/octet-stream"} and self._is_supported_video_url(url):
                mime_type = "video/mp4"
            elif response_mime_type in {"application/octet-stream", "binary/octet-stream"} and self._is_supported_audio_url(url):
                mime_type = self._guess_audio_mime_type_from_url(url) or "audio/mp3"
            else:
                log.warning(f"embed 链接返回了非图片/视频/音频内容，已跳过: {url[:120]}")
                continue

            image_data_list.append(
                {
                    "mime_type": mime_type,
                    "data": image_bytes,
                    "source": "embed",
                    "filename": Path(urlparse(final_url or url).path or "").name or None,
                    "url": final_url or url,
                }
            )
            media_kind = "视频" if mime_type.startswith("video/") else ("音频" if mime_type.startswith("audio/") else "图片")
            log.info(f"成功从 embed proxy_url 提取{media_kind}: {url[:120]} ({mime_type})")

        return image_data_list

    @staticmethod
    def _get_message_stickers(message: Any) -> List[Any]:
        """兼容不同 discord.py 版本/对象形态，读取消息中的贴纸列表。"""
        stickers = getattr(message, "stickers", None)
        if stickers is None:
            stickers = getattr(message, "sticker_items", None)
        if not stickers:
            return []
        return list(stickers)

    async def _extract_sticker_images_from_message(
        self,
        message: Any,
        source: str,
    ) -> List[Dict[str, Any]]:
        """从 Discord 消息贴纸中提取可供视觉模型识别的图片数据。"""
        sticker_images: List[Dict[str, Any]] = []
        stickers = self._get_message_stickers(message)
        if not stickers:
            return sticker_images

        try:
            from src.chat.features.tools.utils.discord_image_utils import (
                fetch_sticker_image,
            )

            for sticker in stickers:
                sticker_image = await fetch_sticker_image(sticker)
                if not sticker_image or not sticker_image.get("data"):
                    log.info(
                        f"贴纸暂时无法转为图片输入，已跳过: "
                        f"{getattr(sticker, 'name', '未知贴纸')} "
                        f"(ID: {getattr(sticker, 'id', 'unknown')})"
                    )
                    continue

                sticker_images.append(
                    {
                        "mime_type": sticker_image.get("mime_type", "image/png"),
                        "data": sticker_image["data"],
                        "source": source,
                        "filename": sticker_image.get("filename"),
                        "sticker_id": str(getattr(sticker, "id", "")),
                        "sticker_name": getattr(sticker, "name", ""),
                    }
                )
                log.info(
                    f"已提取贴纸图片: {getattr(sticker, 'name', '未知贴纸')} "
                    f"(ID: {getattr(sticker, 'id', 'unknown')}), "
                    f"MIME: {sticker_image.get('mime_type')}, "
                    f"大小: {len(sticker_image['data'])} bytes"
                )
        except Exception as e:
            log.warning(f"提取贴纸图片失败: {e}")

        return sticker_images

    async def _extract_images_from_attachments(
        self, attachments: List[discord.Attachment]
    ) -> List[Dict[str, Any]]:
        """从附件列表中提取图片/视频/音频数据。视频在旧路径中会抽帧成拼图。"""
        image_data_list = []
        max_video_bytes = int(
            chat_config.IMAGE_PROCESSING_CONFIG.get(
                "VIDEO_MAX_BYTES", 64 * 1024 * 1024
            )
            or 64 * 1024 * 1024
        )
        max_audio_bytes = int(
            chat_config.IMAGE_PROCESSING_CONFIG.get(
                "AUDIO_MAX_BYTES", 64 * 1024 * 1024
            )
            or 64 * 1024 * 1024
        )
        for attachment in attachments:
            content_type = (
                (getattr(attachment, "content_type", "") or "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            filename = getattr(attachment, "filename", "未命名附件")
            attachment_url = (
                getattr(attachment, "url", None)
                or getattr(attachment, "proxy_url", None)
                or ""
            )
            guessed_image_mime = (
                self._guess_mime_type_from_url(filename)
                or self._guess_mime_type_from_url(attachment_url)
            )
            guessed_video_mime = (
                self._guess_video_mime_type_from_filename(filename)
                or self._guess_video_mime_type_from_url(attachment_url)
            )
            guessed_audio_mime = (
                self._guess_audio_mime_type_from_filename(filename)
                or self._guess_audio_mime_type_from_url(attachment_url)
            )
            is_image_attachment = bool(
                (content_type and content_type.startswith("image/"))
                or guessed_image_mime
            )
            is_video_attachment = bool(
                (content_type and content_type.startswith("video/"))
                or guessed_video_mime
                or (attachment_url and self._is_supported_video_url(attachment_url))
            )
            is_audio_attachment = bool(
                (content_type and content_type.startswith("audio/"))
                or guessed_audio_mime
                or (attachment_url and self._is_supported_audio_url(attachment_url))
            )

            if is_image_attachment or is_video_attachment or is_audio_attachment:
                if is_video_attachment:
                    attachment_size = getattr(attachment, "size", None)
                    if isinstance(attachment_size, int) and attachment_size > max_video_bytes:
                        log.info(
                            f"视频附件超过 {max_video_bytes / 1024 / 1024:.0f}MB 限制，已跳过: {filename}"
                        )
                        continue
                if is_audio_attachment:
                    attachment_size = getattr(attachment, "size", None)
                    if isinstance(attachment_size, int) and attachment_size > max_audio_bytes:
                        log.info(
                            f"音频附件超过 {max_audio_bytes / 1024 / 1024:.0f}MB 限制，已跳过: {filename}"
                        )
                        continue

                media_bytes = b""
                try:
                    media_bytes = await attachment.read()
                except Exception as e:
                    log.warning(f"直接读取媒体附件 {filename} 失败，尝试通过 URL 下载: {e}")

                fetched_media: Optional[Dict[str, Any]] = None
                if not media_bytes and attachment_url:
                    try:
                        async with aiohttp.ClientSession() as session:
                            fetched_media = await self._fetch_image_aio(
                                session,
                                attachment_url,
                                proxy=config.PROXY_URL,
                            )
                        if fetched_media and fetched_media.get("data"):
                            media_bytes = fetched_media["data"]
                            content_type = (
                                str(fetched_media.get("mime_type") or content_type or "")
                                .split(";", 1)[0]
                                .strip()
                                .lower()
                            )
                            final_url = str(fetched_media.get("final_url") or attachment_url)
                            guessed_image_mime = guessed_image_mime or self._guess_mime_type_from_url(final_url)
                            guessed_video_mime = guessed_video_mime or self._guess_video_mime_type_from_url(final_url)
                            guessed_audio_mime = guessed_audio_mime or self._guess_audio_mime_type_from_url(final_url)
                            is_video_attachment = bool(
                                is_video_attachment
                                or content_type.startswith("video/")
                                or guessed_video_mime
                                or self._is_supported_video_url(final_url)
                            )
                            is_audio_attachment = bool(
                                is_audio_attachment
                                or content_type.startswith("audio/")
                                or guessed_audio_mime
                                or self._is_supported_audio_url(final_url)
                            )
                            log.info(f"已通过 URL 下载媒体附件: {filename}")
                    except Exception as e:
                        log.warning(f"通过 URL 下载媒体附件 {filename} 失败: {e}")

                if not media_bytes:
                    log.warning(f"媒体附件为空，已跳过: {filename}")
                    continue

                if is_video_attachment and len(media_bytes) > max_video_bytes:
                    log.info(
                        f"视频附件读取后超过 {max_video_bytes / 1024 / 1024:.0f}MB 限制，已跳过: {filename}"
                    )
                    continue
                if is_audio_attachment and len(media_bytes) > max_audio_bytes:
                    log.info(
                        f"音频附件读取后超过 {max_audio_bytes / 1024 / 1024:.0f}MB 限制，已跳过: {filename}"
                    )
                    continue

                if is_video_attachment:
                    resolved_mime_type = (
                        content_type
                        if content_type.startswith("video/")
                        else guessed_video_mime
                        or "video/mp4"
                    )
                elif is_audio_attachment:
                    resolved_mime_type = (
                        content_type
                        if content_type.startswith("audio/")
                        else guessed_audio_mime
                        or "audio/mp3"
                    )
                else:
                    resolved_mime_type = (
                        content_type
                        if content_type.startswith("image/")
                        else guessed_image_mime
                        or "image/png"
                    )
                image_data_list.append(
                    {
                        "mime_type": resolved_mime_type,
                        "data": media_bytes,
                        "source": "attachment",
                        "filename": filename,
                        "url": attachment_url or None,
                    }
                )
                media_kind = "视频" if is_video_attachment else ("音频" if is_audio_attachment else "图片")
                log.info(
                    f"成功读取{media_kind}附件: {filename}, MIME: {resolved_mime_type}, 大小: {len(media_bytes)} 字节"
                )
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
