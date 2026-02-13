# -*- coding: utf-8 -*-

"""
Discord 图片提取辅助工具
从 Discord 自定义表情、贴纸（Sticker）和用户头像中提取图片数据
供 edit_image、generate_image 和 generate_video 工具使用
"""

import logging
import re
import io
import os
import asyncio
from urllib.parse import urlparse

import aiohttp
import discord
from typing import Optional, Dict, Any, List, Tuple

log = logging.getLogger(__name__)

# Discord 自定义表情正则：匹配 <:name:id> 或 <a:name:id>
CUSTOM_EMOJI_PATTERN = re.compile(r'<a?:(\w+):(\d+)>')
IMAGE_URL_PATTERN = re.compile(
    r'(https?://[^\s\)\]\"\'<>]+\.(?:png|jpg|jpeg|gif|webp|bmp|svg|tiff|avif)(?:\?[^\s\)\]\"\'<>]*)?)',
    re.IGNORECASE,
)
MARKDOWN_IMAGE_PATTERN = re.compile(r'!\[[^\]]*\]\((https?://[^\)]+)\)', re.IGNORECASE)

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "tiff", "avif"}
MIME_TO_EXTENSION = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/svg+xml": "svg",
    "image/tiff": "tiff",
    "image/avif": "avif",
}


def extract_emoji_ids_from_text(text: str) -> List[Tuple[str, str]]:
    """
    从文本中提取所有 Discord 自定义表情的名称和 ID
    
    Args:
        text: 包含 Discord 自定义表情格式的文本
              例如: "帮我把 <:smile:1234567890> 画成头像"
    
    Returns:
        列表，每项为 (name, id) 元组
        例如: [("smile", "1234567890")]
    """
    if not text:
        return []
    return CUSTOM_EMOJI_PATTERN.findall(text)


def extract_image_urls_from_text(text: str) -> List[str]:
    """
    从文本中提取可能的图片 URL（支持 markdown 和纯链接）。
    """
    if not text:
        return []

    urls: List[str] = []
    seen = set()

    for match in MARKDOWN_IMAGE_PATTERN.finditer(text):
        url = match.group(1).strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    for match in IMAGE_URL_PATTERN.finditer(text):
        url = match.group(1).strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    return urls


def _guess_filename_from_url(url: str, fallback_ext: str = "png") -> str:
    parsed = urlparse(url)
    basename = os.path.basename(parsed.path) or "reference_image"
    if "." not in basename:
        basename = f"{basename}.{fallback_ext}"
    return basename


def _normalize_image_content_type(content_type: str) -> str:
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _guess_mime_from_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    path = parsed.path.lower()
    for ext in ALLOWED_IMAGE_EXTENSIONS:
        if path.endswith(f".{ext}"):
            if ext == "jpg":
                return "image/jpeg"
            if ext == "svg":
                return "image/svg+xml"
            return f"image/{ext}"
    return None


async def fetch_image_from_url(
    url: str,
    timeout_seconds: int = 20,
    max_size_bytes: int = 20 * 1024 * 1024,
) -> Optional[Dict[str, Any]]:
    """
    从 URL 下载图片，返回统一的图像数据结构。
    """
    if not url or not isinstance(url, str):
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                headers={"User-Agent": "OdysseiaDiscordBot/1.0"},
            ) as resp:
                if resp.status != 200:
                    return None

                content_length = resp.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > max_size_bytes:
                            log.warning(f"URL 图片过大，跳过: {url[:120]}")
                            return None
                    except ValueError:
                        pass

                content_type_raw = resp.headers.get("Content-Type", "")
                content_type = _normalize_image_content_type(content_type_raw)
                guessed_mime = _guess_mime_from_url(url)

                # 部分 CDN 会返回 application/octet-stream，这里允许通过扩展名推断
                if not content_type.startswith("image/"):
                    if content_type != "application/octet-stream" or not guessed_mime:
                        return None
                    content_type = guessed_mime

                data = await resp.read()
                if not data:
                    return None

                if len(data) > max_size_bytes:
                    log.warning(f"URL 图片下载后体积超限，跳过: {url[:120]}")
                    return None

                ext = MIME_TO_EXTENSION.get(content_type, "png")
                filename = _guess_filename_from_url(url, fallback_ext=ext)

                return {
                    "data": data,
                    "mime_type": content_type if content_type.startswith("image/") else guessed_mime or "image/png",
                    "filename": filename,
                }
    except asyncio.TimeoutError:
        log.warning(f"下载 URL 图片超时: {url[:120]}")
        return None
    except Exception as e:
        log.warning(f"下载 URL 图片失败: {e}, URL: {url[:120]}")
        return None


async def extract_image_from_message_url(
    message: Optional[discord.Message],
) -> Optional[Dict[str, Any]]:
    """
    从消息文本和 Embed 中提取第一张 URL 图片并下载。
    """
    if not message:
        return None

    candidate_urls: List[str] = []
    seen = set()

    def _append_url(url: Optional[str]):
        if not url:
            return
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return
        if url not in seen:
            seen.add(url)
            candidate_urls.append(url)

    if message.content:
        for url in extract_image_urls_from_text(message.content):
            _append_url(url)

    for embed in getattr(message, "embeds", []) or []:
        if getattr(embed, "image", None) and embed.image.url:
            _append_url(embed.image.url)
        if getattr(embed, "thumbnail", None) and embed.thumbnail.url:
            _append_url(embed.thumbnail.url)
        if getattr(embed, "url", None):
            for url in extract_image_urls_from_text(embed.url):
                _append_url(url)
        if getattr(embed, "description", None):
            for url in extract_image_urls_from_text(embed.description):
                _append_url(url)
        for field in getattr(embed, "fields", []) or []:
            if getattr(field, "value", None):
                for url in extract_image_urls_from_text(field.value):
                    _append_url(url)

    if not candidate_urls:
        return None

    for url in candidate_urls:
        image = await fetch_image_from_url(url)
        if image:
            log.info(f"已从消息 URL 提取图片: {url[:120]}")
            return image

    return None


async def auto_extract_emoji_from_message(
    message: Optional[discord.Message],
    explicit_emoji_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    自动从消息中提取自定义表情图片。
    优先使用显式传入的 emoji_id，否则从消息内容中自解析。
    
    Args:
        message: Discord 消息对象
        explicit_emoji_id: AI 显式传入的 emoji_id（可能为 None）
    
    Returns:
        成功返回 {"data": bytes, "mime_type": str, "filename": str}
        失败或未找到返回 None
    """
    # 1. 优先使用显式传入的 emoji_id
    if explicit_emoji_id:
        result = await fetch_emoji_image(explicit_emoji_id)
        if result:
            log.info(f"已从显式 emoji_id 提取表情图片 (ID: {explicit_emoji_id})")
            return result
        log.warning(f"显式 emoji_id 提取失败 (ID: {explicit_emoji_id})")
    
    # 2. 从消息内容中自动解析自定义表情
    if message and message.content:
        emoji_matches = extract_emoji_ids_from_text(message.content)
        if emoji_matches:
            # 取第一个匹配的表情
            emoji_name, emoji_id = emoji_matches[0]
            log.info(f"从消息内容中检测到自定义表情: :{emoji_name}: (ID: {emoji_id})")
            result = await fetch_emoji_image(emoji_id)
            if result:
                log.info(f"已从消息内容自动提取表情图片 (:{emoji_name}: ID: {emoji_id})")
                return result
            log.warning(f"从消息内容自动提取表情图片失败 (:{emoji_name}: ID: {emoji_id})")
    
    return None


async def auto_extract_sticker_from_message(
    message: Optional[discord.Message],
) -> Optional[Dict[str, Any]]:
    """
    自动从消息中提取贴纸（Sticker）图片。
    Discord 贴纸通过 message.stickers 属性访问，不在消息文本中。
    
    Args:
        message: Discord 消息对象
    
    Returns:
        成功返回 {"data": bytes, "mime_type": str, "filename": str}
        失败或未找到返回 None
    """
    if not message or not message.stickers:
        return None
    
    # 取第一个贴纸
    sticker = message.stickers[0]
    log.info(f"从消息中检测到贴纸: {sticker.name} (ID: {sticker.id})")
    
    result = await fetch_sticker_image(sticker)
    if result:
        log.info(f"已从消息中提取贴纸图片: {sticker.name} (ID: {sticker.id})")
        return result
    
    log.warning(f"从消息中提取贴纸图片失败: {sticker.name} (ID: {sticker.id})")
    return None


async def fetch_sticker_image(sticker: discord.StickerItem) -> Optional[Dict[str, Any]]:
    """
    从 Discord CDN 下载贴纸图片
    
    Discord 贴纸支持多种格式：PNG、APNG、Lottie（JSON）、GIF
    - 标准贴纸：https://media.discordapp.net/stickers/{id}.png
    - 动态贴纸：可能是 APNG 或 GIF
    - Lottie 贴纸：不适合作为图片参考，跳过
    
    Args:
        sticker: Discord StickerItem 对象
    
    Returns:
        成功返回 {"data": bytes, "mime_type": str, "filename": str}
        失败返回 None
    """
    sticker_id = sticker.id
    
    # Discord 贴纸格式类型
    # format_type: 1=PNG, 2=APNG, 3=LOTTIE, 4=GIF
    # StickerItem 可能没有 format_type，需要安全获取
    format_type = getattr(sticker, 'format', None)
    
    # Lottie 贴纸是矢量动画（JSON），不适合作为图片参考
    if format_type and hasattr(format_type, 'value') and format_type.value == 3:
        log.warning(f"贴纸 {sticker.name} 是 Lottie 格式，不支持作为图片参考")
        return None
    
    # 尝试直接使用 sticker.url（如果可用）
    sticker_url = getattr(sticker, 'url', None)
    if sticker_url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(str(sticker_url), timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        content_type = resp.headers.get('Content-Type', 'image/png')
                        ext = "png"
                        if "gif" in content_type:
                            ext = "gif"
                        elif "webp" in content_type:
                            ext = "webp"
                        elif "apng" in content_type:
                            ext = "png"
                        log.info(f"成功通过 sticker.url 下载贴纸图片: {sticker_url}, 大小: {len(data)} bytes")
                        return {
                            "data": data,
                            "mime_type": content_type.split(';')[0].strip(),
                            "filename": f"sticker_{sticker_id}.{ext}",
                        }
        except Exception as e:
            log.debug(f"通过 sticker.url 下载失败: {e}")
    
    # 回退：尝试多种 CDN URL 格式
    cdn_urls = [
        (f"https://media.discordapp.net/stickers/{sticker_id}.png?size=512", "image/png", "png"),
        (f"https://media.discordapp.net/stickers/{sticker_id}.gif?size=512", "image/gif", "gif"),
        (f"https://media.discordapp.net/stickers/{sticker_id}.webp?size=512", "image/webp", "webp"),
        (f"https://cdn.discordapp.com/stickers/{sticker_id}.png?size=512", "image/png", "png"),
        (f"https://cdn.discordapp.com/stickers/{sticker_id}.gif?size=512", "image/gif", "gif"),
    ]
    
    for url, mime, ext in cdn_urls:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        log.info(f"成功通过 CDN 下载贴纸图片: {url}, 大小: {len(data)} bytes")
                        return {
                            "data": data,
                            "mime_type": mime,
                            "filename": f"sticker_{sticker_id}.{ext}",
                        }
        except Exception as e:
            log.debug(f"尝试下载贴纸 {ext} 格式失败: {e}")
            continue
    
    log.warning(f"所有格式尝试均失败，无法下载贴纸 ID: {sticker_id}")
    return None


async def fetch_emoji_image(emoji_id: str) -> Optional[Dict[str, Any]]:
    """
    从 Discord CDN 下载自定义表情图片
    
    Args:
        emoji_id: 表情的数字ID（会自动清理非数字字符）
    
    Returns:
        成功返回 {"data": bytes, "mime_type": str, "filename": str}
        失败返回 None
    """
    # 清理ID，只保留数字
    clean_id = re.sub(r'\D', '', str(emoji_id))
    if not clean_id:
        log.warning(f"无效的表情ID: {emoji_id}")
        return None
    
    # 尝试多种格式（PNG → GIF → WebP）
    for ext, mime in [("png", "image/png"), ("gif", "image/gif"), ("webp", "image/webp")]:
        url = f"https://cdn.discordapp.com/emojis/{clean_id}.{ext}?size=512"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        log.info(f"成功下载表情图片: {url}, 大小: {len(data)} bytes")
                        return {
                            "data": data,
                            "mime_type": mime,
                            "filename": f"emoji_{clean_id}.{ext}",
                        }
        except Exception as e:
            log.debug(f"尝试下载表情 {ext} 格式失败: {e}")
            continue
    
    log.warning(f"所有格式尝试均失败，无法下载表情 ID: {clean_id}")
    return None


async def fetch_avatar_image(
    user_id: str,
    bot: Optional[discord.Client] = None,
    guild: Optional[discord.Guild] = None,
) -> Optional[Dict[str, Any]]:
    """
    获取 Discord 用户头像图片
    
    Args:
        user_id: 用户的 Discord 数字ID（会自动清理非数字字符）
        bot: Discord Bot 客户端实例（用于 API 查询）
        guild: 当前服务器（用于缓存查询）
    
    Returns:
        成功返回 {"data": bytes, "mime_type": str, "filename": str}
        失败返回 None
    """
    # 清理ID，只保留数字
    clean_id = re.sub(r'\D', '', str(user_id))
    if not clean_id:
        log.warning(f"无效的用户ID: {user_id}")
        return None
    
    user_id_int = int(clean_id)
    target_user = None
    
    # 1. 先从 guild 缓存获取
    if guild:
        try:
            target_user = guild.get_member(user_id_int)
        except Exception:
            pass
    
    # 2. 回退到 API 查询
    if not target_user and bot:
        try:
            target_user = await bot.fetch_user(user_id_int)
            log.info(f"通过API获取到用户: {target_user}")
        except discord.NotFound:
            log.warning(f"找不到用户 ID: {user_id_int}")
            return None
        except discord.HTTPException as e:
            log.error(f"获取用户信息失败: {e}")
            return None
    
    if not target_user:
        log.warning(f"无法获取用户 ID: {clean_id}")
        return None
    
    # 获取头像
    if not target_user.display_avatar:
        log.warning(f"用户 {target_user.display_name} 没有设置头像")
        return None
    
    avatar_url = str(target_user.display_avatar.with_size(512).with_format("png"))
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    log.info(f"成功下载用户头像: {avatar_url}, 大小: {len(data)} bytes")
                    return {
                        "data": data,
                        "mime_type": "image/png",
                        "filename": f"avatar_{clean_id}.png",
                    }
                else:
                    log.warning(f"下载头像失败，HTTP状态码: {resp.status}")
                    return None
    except Exception as e:
        log.error(f"下载头像出错: {e}")
        return None


def compose_avatars(avatar_images: List[bytes], max_size: int = 512) -> Optional[bytes]:
    """
    将多张头像图片横向拼接成一张合成图。
    
    Args:
        avatar_images: 头像图片字节数据列表
        max_size: 每张头像的最大尺寸（像素），用于统一缩放
    
    Returns:
        拼接后的 PNG 图片字节数据，失败返回 None
    """
    try:
        from PIL import Image as PILImage
    except ImportError:
        log.error("Pillow 未安装，无法拼接头像")
        return None
    
    if not avatar_images:
        return None
    
    if len(avatar_images) == 1:
        # 单张直接返回
        return avatar_images[0]
    
    try:
        # 加载所有图片
        pil_images = []
        for img_bytes in avatar_images:
            img = PILImage.open(io.BytesIO(img_bytes))
            img = img.convert("RGBA")
            # 统一缩放到 max_size x max_size
            img = img.resize((max_size, max_size), PILImage.Resampling.LANCZOS)
            pil_images.append(img)
        
        # 计算合成图尺寸
        padding = 20  # 头像之间的间距
        total_width = max_size * len(pil_images) + padding * (len(pil_images) - 1)
        total_height = max_size
        
        # 创建合成画布（白色背景）
        composite = PILImage.new("RGBA", (total_width, total_height), (255, 255, 255, 255))
        
        # 粘贴每张头像
        x_offset = 0
        for img in pil_images:
            composite.paste(img, (x_offset, 0), img)
            x_offset += max_size + padding
        
        # 转换为 PNG 字节
        output = io.BytesIO()
        composite.save(output, format="PNG")
        output.seek(0)
        result = output.read()
        
        log.info(f"已拼接 {len(pil_images)} 张头像为合成图，大小: {len(result)} bytes")
        return result
        
    except Exception as e:
        log.error(f"拼接头像失败: {e}")
        return None
