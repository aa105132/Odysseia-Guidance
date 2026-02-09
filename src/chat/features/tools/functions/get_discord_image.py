# -*- coding: utf-8 -*-

"""
Discord 图片提取工具
让LLM可以提取Discord自定义表情图片和用户头像，
然后将提取到的图片存入频道上下文供后续 edit_image / generate_video 使用
"""

import logging
import re
import io
import aiohttp
import discord
from typing import Optional

log = logging.getLogger(__name__)


async def get_discord_image(
    source_type: str,
    source_id: str,
    **kwargs
) -> dict:
    """
    提取Discord中的图片资源（自定义表情或用户头像），并作为图片发送到频道中。
    提取的图片可以被后续的 edit_image 或 generate_video 工具使用作为参考图。
    
    **使用场景：**
    - 用户发送了自定义表情（如 <:name:123456>）并要求以此表情生成图片/视频
    - 用户说"提取某某人的头像"、"用xxx的头像生成图片"
    - 用户说"把这个表情做成图片/视频"
    
    **重要：此工具只负责提取图片并发送到频道。如果用户还要求生成图片或视频，
    你需要在此工具执行完后，再调用 edit_image 或 generate_video 工具。**
    
    **你必须从用户消息中自动解析出表情ID或用户ID，不要询问用户。**
    
    Discord 自定义表情格式：
    - 静态表情：<:表情名:表情ID>  例如 <:smile:123456789>
    - 动态表情：<a:表情名:表情ID>  例如 <a:dance:123456789>
    - 你需要从中提取数字ID部分
    
    Args:
        source_type: 图片来源类型，只能是以下两个值之一：
                    - "emoji" 提取Discord自定义表情图片
                    - "avatar" 提取Discord用户头像
                    
        source_id: 资源ID：
                  - 当 source_type 为 "emoji" 时：填写表情的数字ID
                    例如用户发送了 <:smile:1234567890>，则填 "1234567890"
                  - 当 source_type 为 "avatar" 时：填写用户的Discord数字ID
                    例如用户说"提取ID为987654321的头像"，则填 "987654321"
    
    Returns:
        成功时返回图片信息，图片已发送到频道中供后续工具使用。
        失败时返回错误信息。
    """
    channel = kwargs.get("channel")
    bot = kwargs.get("bot")
    message: Optional[discord.Message] = kwargs.get("message")
    
    if not channel:
        return {
            "error": True,
            "hint": "无法获取当前频道，请稍后再试。"
        }
    
    # 清理 source_id，只保留数字部分
    source_id_clean = re.sub(r'\D', '', str(source_id))
    if not source_id_clean:
        return {
            "error": True,
            "hint": f"无效的ID: {source_id}。请确保提供的是纯数字ID。"
        }
    
    image_bytes = None
    image_filename = "extracted_image.png"
    image_description = ""
    
    try:
        if source_type == "emoji":
            # 提取 Discord 自定义表情图片
            emoji_id = source_id_clean
            
            # 尝试多种格式获取表情图片（先尝试 PNG，再尝试 GIF 动态表情）
            image_url = None
            for ext in ["png", "gif", "webp"]:
                test_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}?size=512"
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.head(test_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            if resp.status == 200:
                                image_url = test_url
                                image_filename = f"emoji_{emoji_id}.{ext}"
                                break
                except Exception:
                    continue
            
            if not image_url:
                # 默认使用 PNG 格式尝试下载
                image_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.png?size=512"
                image_filename = f"emoji_{emoji_id}.png"
            
            # 下载表情图片
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        image_bytes = await resp.read()
                        image_description = f"Discord 自定义表情 (ID: {emoji_id})"
                        log.info(f"成功下载表情图片: {image_url}, 大小: {len(image_bytes)} bytes")
                    else:
                        log.warning(f"下载表情图片失败，HTTP状态码: {resp.status}, URL: {image_url}")
                        return {
                            "error": True,
                            "hint": f"无法下载表情图片（ID: {emoji_id}），可能是表情ID无效或已被删除。请检查表情ID是否正确。"
                        }
        
        elif source_type == "avatar":
            # 提取 Discord 用户头像
            user_id_int = int(source_id_clean)
            
            target_user = None
            
            # 先尝试从 guild 缓存获取
            if message and message.guild:
                try:
                    target_user = message.guild.get_member(user_id_int)
                except Exception:
                    pass
            
            # 如果缓存中没有，尝试从 API 获取
            if not target_user and bot:
                try:
                    target_user = await bot.fetch_user(user_id_int)
                    log.info(f"通过API获取到用户: {target_user}")
                except discord.NotFound:
                    return {
                        "error": True,
                        "hint": f"找不到ID为 {user_id_int} 的用户。请确认用户ID是否正确。"
                    }
                except discord.HTTPException as e:
                    log.error(f"获取用户信息失败: {e}")
                    return {
                        "error": True,
                        "hint": f"获取用户信息时发生错误，请稍后再试。"
                    }
            
            if not target_user:
                return {
                    "error": True,
                    "hint": f"无法获取ID为 {source_id_clean} 的用户信息。请确认用户ID是否正确。"
                }
            
            # 获取头像URL
            avatar_url = target_user.display_avatar.url if target_user.display_avatar else None
            if not avatar_url:
                return {
                    "error": True,
                    "hint": f"用户 {target_user.display_name} 没有设置头像。"
                }
            
            # 使用高分辨率头像
            avatar_url_hd = str(target_user.display_avatar.with_size(512).with_format("png"))
            
            # 下载头像图片
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url_hd, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        image_bytes = await resp.read()
                        image_filename = f"avatar_{source_id_clean}.png"
                        image_description = f"{target_user.display_name} 的头像 (ID: {source_id_clean})"
                        log.info(f"成功下载用户头像: {avatar_url_hd}, 大小: {len(image_bytes)} bytes")
                    else:
                        log.warning(f"下载头像失败，HTTP状态码: {resp.status}")
                        return {
                            "error": True,
                            "hint": f"下载用户 {target_user.display_name} 的头像失败，请稍后再试。"
                        }
        
        else:
            return {
                "error": True,
                "hint": f"不支持的图片来源类型: {source_type}。只支持 'emoji'（表情）和 'avatar'（头像）。"
            }
        
        # 发送提取到的图片到频道（作为附件发送，以便后续 edit_image / generate_video 自动检测）
        if image_bytes and channel:
            try:
                file = discord.File(io.BytesIO(image_bytes), filename=image_filename)
                await channel.send(
                    content=f"**图片提取** | {image_description}",
                    file=file,
                )
                log.info(f"已发送提取的图片到频道: {image_description}")
                
                return {
                    "success": True,
                    "description": image_description,
                    "message": f"已成功提取并发送 {image_description} 到频道。如果用户还需要用这张图生成图片或视频，请接着调用 edit_image（图生图）或 generate_video（use_reference_image=True，图生视频）工具。图片已经在频道中了，后续工具可以自动从最近消息中获取它。"
                }
                
            except Exception as e:
                log.error(f"发送提取的图片失败: {e}", exc_info=True)
                return {
                    "error": True,
                    "hint": "图片提取成功但发送到频道失败，请稍后再试。"
                }
        
        return {
            "error": True,
            "hint": "图片提取失败，未能获取到图片数据。"
        }
        
    except Exception as e:
        log.error(f"图片提取工具执行错误: {e}", exc_info=True)
        return {
            "error": True,
            "hint": f"图片提取时发生了系统错误，请稍后再试。"
        }