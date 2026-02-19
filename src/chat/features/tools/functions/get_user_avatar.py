# -*- coding: utf-8 -*-

"""
获取用户头像工具

让 AI 在对话中可以主动获取 Discord 用户的头像图片，
将图片数据直接传给 AI 的视觉能力进行分析。
这样 AI 在生图前可以先"看到"用户的外观，生成更匹配的图片描述。

返回格式使用 image_data 字典，tool_service 会自动将其转为
inline_data Part 传回 Gemini，实现 AI 视觉分析。
"""

import logging
import discord
import httpx
from typing import Optional

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)


@tool_metadata(
    name="查看头像",
    description="获取用户的 Discord 头像图片，让 AI 能够看到用户外观",
    emoji="📷",
    category="用户信息",
)
async def get_user_avatar(
    user_id: Optional[str] = None,
    **kwargs,
) -> dict:
    """
    获取指定用户（或当前对话用户）的 Discord 头像图片。
    返回图片数据供 AI 视觉分析，用于了解用户外观特征。

    [调用指南]
    - **主动调用**: 当你需要为用户画图、生成角色描述、或需要了解用户外观时，应主动调用此工具
    - **生图前调用**: 在调用 generate_image 或 generate_image_novelai 之前，如果需要参考用户外观，先调用此工具查看头像
    - **自动填充**: 如果不传 user_id，系统会自动使用当前对话用户的 ID
    - **指定用户**: 如果用户提到了其他人（如 @某人 或提供了用户ID），可以传入对应的 user_id 来获取该用户头像
    - **返回图片**: 此工具返回的图片会直接传给你的视觉能力，你可以分析图中人物的发色、瞳色、发型、服饰等特征

    Args:
        user_id (str, optional): 目标用户的 Discord 数字ID。
            如果不提供，系统会自动使用当前对话用户的 ID。
            支持格式: 纯数字字符串，如 "123456789012345678"

    Returns:
        包含用户头像图片数据的字典（image_data 格式），AI 可直接"看到"图片。
    """
    bot = kwargs.get("bot")

    if not bot:
        return {"error": "Bot 实例不可用。"}

    # 如果没有指定 user_id，使用当前对话用户
    if not user_id or not str(user_id).strip():
        # 系统会自动注入 user_id 到 kwargs
        user_id = kwargs.get("user_id", "")

    if not user_id or not str(user_id).isdigit():
        return {
            "error": True,
            "hint": "无法获取用户头像：未提供有效的用户 ID。请让用户提供要查看的用户 ID 或 @某人。",
        }

    target_id = int(user_id)

    try:
        # 获取用户对象
        user = await bot.fetch_user(target_id)
        if not user:
            return {
                "error": True,
                "hint": f"找不到 ID 为 {target_id} 的用户。",
            }

        # 获取头像 URL（优先使用服务器头像）
        avatar_asset = user.display_avatar
        if not avatar_asset:
            return {
                "error": True,
                "hint": f"用户 {user.display_name} 没有设置头像。",
            }

        # 使用较大尺寸的头像以便 AI 分析
        avatar_url = str(avatar_asset.replace(size=512, format="png"))
        user_name = user.display_name
        user_global_name = user.global_name or user.name

        log.info(f"正在下载用户 {user_name} ({target_id}) 的头像: {avatar_url}")

        # 下载头像图片
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(avatar_url)
            response.raise_for_status()
            image_bytes = response.content
            mime_type = response.headers.get("content-type", "image/png")

        log.info(
            f"成功获取用户 {user_name} 的头像，大小: {len(image_bytes)} bytes, "
            f"MIME: {mime_type}"
        )

        # 返回 image_data 格式，tool_service 会自动将其转为 inline_data
        # 传回 Gemini，让 AI 能够"看到"这张头像图片
        return {
            "image_data": {
                "data": image_bytes,
                "mime_type": mime_type,
            },
            "user_info": {
                "user_id": str(target_id),
                "display_name": user_name,
                "global_name": user_global_name,
                "avatar_url": avatar_url,
            },
            "hint": (
                f"这是用户 {user_name} 的 Discord 头像图片。"
                f"请仔细观察图中的外观特征（如发色、发型、瞳色、服饰、风格等），"
                f"以便在后续生成图片时参考。"
            ),
        }

    except discord.NotFound:
        return {
            "error": True,
            "hint": f"找不到 ID 为 {target_id} 的 Discord 用户。",
        }
    except httpx.HTTPStatusError as e:
        log.error(f"下载用户 {target_id} 头像时 HTTP 错误: {e}")
        return {
            "error": True,
            "hint": f"下载头像失败 (HTTP {e.response.status_code})。",
        }
    except Exception as e:
        log.error(f"获取用户 {target_id} 头像时出错: {e}", exc_info=True)
        return {
            "error": True,
            "hint": f"获取头像时发生错误: {str(e)}",
        }