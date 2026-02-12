# -*- coding: utf-8 -*-

"""
Discord 图片提取工具
让 LLM 可以在对话中提取多个 Discord 用户头像，
返回图片数据供后续图生图/视频生成等工具使用。
不发送到频道，仅在内部传递数据。
"""

import logging
import asyncio
import discord
from typing import Optional, List, Dict, Any

log = logging.getLogger(__name__)


async def get_discord_image(
    user_ids: List[str],
    **kwargs,
) -> dict:
    """
    提取一个或多个 Discord 用户的头像图片数据。
    此工具不会发送任何内容到频道，仅返回提取结果供后续工具使用。

    **使用场景：**
    - 需要获取用户头像数据来传给 edit_image 或 generate_video 使用
    - 用户说"提取xxx和yyy的头像来画图" → 先用此工具提取，再用 edit_image
    - 用户在消息中 @了多个人并说"用他们的头像画" → 提取后传给图生图工具

    **注意：大多数场景下你不需要单独调用此工具！**
    edit_image 和 generate_video 已经内置了 avatar_user_ids 参数，
    可以直接传入用户ID列表来提取头像。只有当你需要单独预览或检查头像时才需要此工具。

    **如何获取用户ID：**
    - 用户消息中 @ 了某人时，格式为 <@用户ID>，直接提取数字ID
    - 用户直接说了数字ID
    - 用户说"我的头像"时，使用当前用户的ID（从 kwargs 中获取 user_id）

    Args:
        user_ids: Discord 用户的数字 ID 列表。
                  支持 1~10 个用户 ID。
                  例如: ["123456789", "987654321"]

    Returns:
        包含提取结果的字典，供后续工具或 AI 使用。
    """
    from src.chat.features.tools.utils.discord_image_utils import fetch_avatar_image

    message: Optional[discord.Message] = kwargs.get("message")
    bot = kwargs.get("bot")
    guild = message.guild if message else None

    # 参数校验
    if not user_ids or not isinstance(user_ids, list):
        return {
            "error": True,
            "hint": "未提供有效的用户ID列表。请用自己的语气告诉用户需要指定要提取头像的用户。"
        }

    # 限制最多 10 个
    MAX_USERS = 10
    if len(user_ids) > MAX_USERS:
        user_ids = user_ids[:MAX_USERS]
        log.warning(f"用户ID数量超过上限，已截断为 {MAX_USERS} 个")

    # 并发提取所有用户头像
    async def fetch_one(uid: str) -> Dict[str, Any]:
        """提取单个用户的头像"""
        try:
            result = await fetch_avatar_image(
                user_id=uid,
                bot=bot,
                guild=guild,
            )
            if result:
                # 获取用户名
                user_name = uid
                if bot:
                    try:
                        user_obj = bot.get_user(int(uid))
                        if not user_obj:
                            user_obj = await bot.fetch_user(int(uid))
                        if user_obj:
                            user_name = user_obj.display_name
                    except Exception:
                        pass
                return {
                    "user_id": uid,
                    "user_name": user_name,
                    "success": True,
                }
            else:
                return {"user_id": uid, "success": False, "reason": "无法获取头像"}
        except Exception as e:
            log.error(f"提取用户 {uid} 头像时出错: {e}")
            return {"user_id": uid, "success": False, "reason": str(e)}

    tasks = [fetch_one(uid) for uid in user_ids]
    results = await asyncio.gather(*tasks)

    # 分离成功和失败
    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    if not successful:
        failed_ids = ", ".join([r["user_id"] for r in failed])
        return {
            "error": True,
            "hint": f"所有用户的头像都提取失败了（ID: {failed_ids}）。请用自己的语气告诉用户无法获取这些头像，可能是用户ID不正确。"
        }

    # 返回结果给 AI（不发送到频道）
    result_info = {
        "success": True,
        "extracted_count": len(successful),
        "total_requested": len(user_ids),
        "extracted_users": [
            {"user_id": r["user_id"], "user_name": r["user_name"]}
            for r in successful
        ],
        "message": f"已成功提取 {len(successful)} 个用户头像。"
                   f"你可以直接调用 edit_image 工具并传入 avatar_user_ids={[r['user_id'] for r in successful]} 来使用这些头像进行图生图。"
    }
    if failed:
        result_info["failed_user_ids"] = [r["user_id"] for r in failed]
        result_info["message"] += f" {len(failed)} 个提取失败。"

    return result_info