# -*- coding: utf-8 -*-

"""
Discord 图片提取辅助工具
从 Discord 自定义表情和用户头像中提取图片数据
供 edit_image 和 generate_video 工具使用
"""

import logging
import re
import aiohttp
import discord
from typing import Optional, Dict, Any

log = logging.getLogger(__name__)


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