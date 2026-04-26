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
import re
import discord
import httpx
from typing import Optional, List, Tuple

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)

MENTION_ID_PATTERN = re.compile(r"^<@!?(\d+)>$")


def _normalize_lookup_name(name: Optional[str]) -> str:
    if not name:
        return ""
    return str(name).strip().lstrip("@").casefold()


def _member_name_candidates(member: discord.abc.User) -> List[str]:
    names: List[str] = []
    for raw_name in (
        getattr(member, "display_name", None),
        getattr(member, "global_name", None),
        getattr(member, "name", None),
    ):
        if isinstance(raw_name, str):
            clean_name = raw_name.strip()
            if clean_name and clean_name not in names:
                names.append(clean_name)
    return names


def _resolve_member_by_username(
    members: List[discord.abc.User], username: str
) -> Tuple[Optional[discord.abc.User], Optional[str]]:
    target = _normalize_lookup_name(username)
    if not target:
        return None, "未提供有效用户名。"

    exact_matches: List[discord.abc.User] = []
    fuzzy_matches: List[discord.abc.User] = []

    for member in members:
        normalized_names = [
            _normalize_lookup_name(name) for name in _member_name_candidates(member)
        ]
        if target in normalized_names:
            exact_matches.append(member)
            continue
        if any(target in name for name in normalized_names):
            fuzzy_matches.append(member)

    if len(exact_matches) == 1:
        return exact_matches[0], None

    if len(exact_matches) > 1:
        options = "、".join(
            f"{m.display_name}({m.id})" for m in exact_matches[:5]
        )
        return (
            None,
            (
                f"用户名“{username}”匹配到多个用户：{options}。"
                "请改用更精确的用户名，或直接提供 user_id / @提及。"
            ),
        )

    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0], None

    if len(fuzzy_matches) > 1:
        options = "、".join(
            f"{m.display_name}({m.id})" for m in fuzzy_matches[:5]
        )
        return (
            None,
            (
                f"用户名“{username}”模糊匹配到多个用户：{options}。"
                "请改用更精确的用户名，或直接提供 user_id / @提及。"
            ),
        )

    return None, None


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
    - **必须传参数**: 必须明确传入 user_id 或 username，不传参数会报错
    - **画某个人时**: 用户说"画小明"，你应调用 get_user_avatar(username="小明")，不要省略参数
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

    if not bot:
        return {"error": "Bot 实例不可用。"}

    resolved_user_id: str = str(user_id).strip() if user_id is not None else ""
    lookup_username: str = str(username).strip() if username is not None else ""

    # 1) 优先按 user_id
    # 2) 未提供 user_id 时，尝试按 username 解析
    # 3) 两者都没有时，回退为当前对话用户
    if not resolved_user_id and lookup_username:
        mention_match = MENTION_ID_PATTERN.match(lookup_username)
        if mention_match:
            resolved_user_id = mention_match.group(1)
        elif lookup_username.isdigit():
            resolved_user_id = lookup_username
        else:
            guild = kwargs.get("guild")
            channel = kwargs.get("channel")
            if not guild and channel and getattr(channel, "guild", None):
                guild = channel.guild

            if not guild:
                return {
                    "error": True,
                    "hint": (
                        f"当前场景无法仅通过用户名“{lookup_username}”定位用户。"
                        "请提供 user_id 或 @提及。"
                    ),
                }

            candidate_map = {}

            # 先收集缓存成员
            try:
                for member in getattr(guild, "members", []) or []:
                    candidate_map[member.id] = member
            except Exception:
                pass

            # 再尝试 Discord 的成员命中
            try:
                direct_member = guild.get_member_named(lookup_username)
                if direct_member:
                    candidate_map[direct_member.id] = direct_member
            except Exception:
                pass

            # 最后尝试 query_members（可能因权限/配置失败，失败时仅记 debug）
            try:
                queried_members = await guild.query_members(
                    query=lookup_username, limit=25
                )
                for member in queried_members:
                    candidate_map[member.id] = member
            except Exception as query_error:
                log.debug(f"按用户名 query_members 失败: {query_error}")

            matched_member, match_error = _resolve_member_by_username(
                list(candidate_map.values()), lookup_username
            )
            if match_error:
                return {"error": True, "hint": match_error}

            if matched_member:
                resolved_user_id = str(matched_member.id)
                log.info(
                    f"通过用户名 '{lookup_username}' 解析到用户 "
                    f"{matched_member.display_name} ({matched_member.id})"
                )
            else:
                return {
                    "error": True,
                    "hint": (
                        f"在当前服务器中找不到用户名“{lookup_username}”。"
                        "请提供更精确的用户名，或直接给出 user_id / @提及。"
                    ),
                }

    if not resolved_user_id:
        # 未传任何参数时不再默认回退到当前对话用户
        # 避免AI想查"某个人"但忘记传参数时误拿当前用户的头像
        return {
            "error": True,
            "hint": (
                "未指定要查看谁的头像。请明确传入 user_id 或 username 参数。"
                "例如：get_user_avatar(username=\"小明\") 或 get_user_avatar(user_id=\"123456789\")"
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
                f"调用 edit_image 时传 avatar_user_id=\"{target_id}\"。"
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