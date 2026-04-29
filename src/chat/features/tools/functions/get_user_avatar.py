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
from src.chat.features.tools.utils.resolve_user import (
    MENTION_ID_PATTERN,
    resolve_username_to_id,
)

log = logging.getLogger(__name__)


@tool_metadata(
    name="查看头像",
    description="获取用户的 Discord 头像图片（支持 user_id 或用户名），让 AI 能够看到用户外观",
    emoji="📷",
    category="用户信息",
)
async def get_user_avatar(
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    **kwargs,
) -> dict:
    """
    获取指定用户（或当前对话用户）的 Discord 头像图片。
    返回图片数据供 AI 视觉分析，用于了解用户外观特征。

    [调用指南]
    - **画当前对话人**: 用户说"拿我头像画"，可以不传参数，系统会自动使用当前对话用户
    - **画某个人时**: 用户说"画小明"，必须传 get_user_avatar(username="小明")
    - **按用户名查找**: username 支持 display_name/global_name/name；若重名会返回歧义提示
    - **从上下文提取ID**: 如果上下文中有 `小明<123456>` 格式，可直接传 get_user_avatar(user_id="123456")
    - **返回图片**: 此工具返回的图片会直接传给你的视觉能力，你可以看到用户的外观

    Args:
        user_id (str, optional): 目标用户的 Discord 数字ID。
            优先级高于 username。支持格式: 纯数字字符串，如 "123456789012345678"
        username (str, optional): 目标用户名（display_name/global_name/name）。
            也支持传入 @提及格式（如 "<@123...>"），会自动解析为 user_id。

    Returns:
        包含用户头像图片数据的字典（image_data 格式），AI 可直接"看到"图片。
    """
    bot = kwargs.get("bot")

    log.info(f"get_user_avatar 被调用: user_id={user_id!r} (type={type(user_id).__name__}), username={username!r}")

    if not bot:
        return {"error": "Bot 实例不可用。"}

    resolved_user_id: str = str(user_id).strip() if user_id is not None else ""
    lookup_username: str = str(username).strip() if username is not None else ""
    log.info(f"get_user_avatar 解析后: resolved_user_id={resolved_user_id!r}, lookup_username={lookup_username!r}")

    # 1) 优先按 user_id
    # 2) 未提供 user_id 时，尝试按 username 解析
    # 3) 两者都没有时，回退为当前对话用户
    if not resolved_user_id and lookup_username:
        guild = kwargs.get("guild")
        channel = kwargs.get("channel")
        if not guild and channel and getattr(channel, "guild", None):
            guild = channel.guild

        uid, err = await resolve_username_to_id(guild, lookup_username)
        if uid:
            resolved_user_id = uid
        elif err:
            return {"error": True, "hint": err}

    if not resolved_user_id:
        # 未传参数时回退到当前对话用户（适用于"拿我头像画"场景）
        fallback_user_id = str(kwargs.get("user_id", "")).strip()
        if fallback_user_id and fallback_user_id.isdigit():
            resolved_user_id = fallback_user_id
            log.info(f"get_user_avatar 未传参数，回退到当前对话用户: {resolved_user_id}")
        else:
            return {
                "error": True,
                "hint": (
                    "未指定要查看谁的头像。请传入 user_id 或 username 参数。"
                ),
        }

    if not resolved_user_id.isdigit():
        return {
            "error": True,
            "hint": (
                "无法获取用户头像：未提供有效的 user_id/用户名。"
                "请提供数字ID、@提及或明确用户名。"
            ),
        }

    target_id = int(resolved_user_id)

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
                f"已获取 {user_name}（user_id: {target_id}）的头像。"
                f"如果还需要获取其他人的头像，继续调用 get_user_avatar；"
                f"全部头像获取完毕后立即开始画图，不要再做多余的查询。"
                f"画单人时传 edit_image(avatar_user_id=\"{target_id}\")；"
                f"画多人时把所有 user_id 放到 edit_image(avatar_user_ids=[\"{target_id}\", ...])。"
            ),
        }

    except discord.NotFound:
        return {
            "error": True,
            "hint": f"找不到 ID 为 {target_id} 的 Discord 用户。头像获取失败不影响画图，请直接根据已有信息调用绘图工具。",
        }
    except httpx.HTTPStatusError as e:
        log.error(f"下载用户 {target_id} 头像时 HTTP 错误: {e}")
        return {
            "error": True,
            "hint": f"下载头像失败 (HTTP {e.response.status_code})。头像获取失败不影响画图，请直接根据已有信息调用绘图工具。",
        }
    except Exception as e:
        log.error(f"获取用户 {target_id} 头像时出错: {e}", exc_info=True)
        return {
            "error": True,
            "hint": f"获取头像时发生错误: {str(e)}",
        }