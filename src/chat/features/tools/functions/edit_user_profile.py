"""
改写用户名片工具 - 让月月能修改用户的社区名片
"""
import logging
from typing import Dict, Any, Optional

from sqlalchemy import select
from src.chat.features.tools.tool_metadata import tool_metadata
from src.database.database import AsyncSessionLocal
from src.database.models import CommunityMemberProfile

log = logging.getLogger(__name__)


@tool_metadata(
    name="改写名片",
    description="改写用户的社区名片（昵称、人设、背景、偏好等）",
    emoji="✏️",
    category="用户信息",
)
async def edit_user_profile(
    user_id: str = "",
    display_name: Optional[str] = None,
    personality: Optional[str] = None,
    background: Optional[str] = None,
    preferences: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    改写用户的社区名片。可以修改昵称、人设描述、背景故事、偏好等字段。
    系统会自动获取当前对话用户的数字ID，禁止手动传递。

    [调用指南]
    - 当用户主动要求你修改他的名片时调用此工具
    - 可以只修改部分字段，未提供的字段保持不变
    - display_name: 名片标题/昵称（简短）
    - personality: 人设描述（性格特征）
    - background: 背景故事
    - preferences: 偏好设定

    [注意]
    - 仅修改用户自己要求改的内容，不要擅自修改
    - 如果该用户还没有名片档案，会自动创建一条新记录
    - 修改后应告知用户改了哪些内容

    Returns:
        包含修改状态和更新后字段的字典。
    """
    user_id_str = str(user_id or "").strip()
    if not user_id_str or not user_id_str.isdigit():
        log.warning(f"edit_user_profile: 无效的 user_id: {user_id}")
        return {"error": f"Invalid or missing user_id: {user_id}"}

    target_id = int(user_id_str)

    # 检查至少有一个字段需要修改
    changes = {}
    if display_name is not None:
        changes["display_name"] = display_name.strip()
    if personality is not None:
        changes["personality"] = personality.strip()
    if background is not None:
        changes["background"] = background.strip()
    if preferences is not None:
        changes["preferences"] = preferences.strip()

    if not changes:
        return {"error": "没有提供任何需要修改的字段。"}

    try:
        async with AsyncSessionLocal() as session:
            # 查找现有名片
            stmt = select(CommunityMemberProfile).where(
                CommunityMemberProfile.discord_id == str(target_id)
            )
            result = await session.execute(stmt)
            profile = result.scalars().first()

            import json

            if profile is None:
                # 创建新名片
                metadata = {}
                if "display_name" in changes:
                    metadata["name"] = changes["display_name"]
                if "personality" in changes:
                    metadata["personality"] = changes["personality"]
                if "background" in changes:
                    metadata["background"] = changes["background"]
                if "preferences" in changes:
                    metadata["preferences"] = changes["preferences"]

                full_text = " ".join(filter(None, [
                    changes.get("display_name", ""),
                    changes.get("personality", ""),
                    changes.get("background", ""),
                    changes.get("preferences", ""),
                ]))

                profile = CommunityMemberProfile(
                    external_id=str(target_id),
                    discord_id=str(target_id),
                    title=changes.get("display_name"),
                    full_text=full_text or "新名片",
                    source_metadata=metadata,
                )
                session.add(profile)
                await session.commit()
                log.info(f"为用户 {target_id} 创建了新名片，修改字段: {list(changes.keys())}")
                return {
                    "status": "created",
                    "user_id": str(target_id),
                    "changes": changes,
                    "message": f"已为用户创建新名片并设置: {', '.join(changes.keys())}",
                }
            else:
                # 更新现有名片
                old_metadata = profile.source_metadata or {}
                if not isinstance(old_metadata, dict):
                    try:
                        old_metadata = json.loads(old_metadata) if isinstance(old_metadata, str) else {}
                    except Exception:
                        old_metadata = {}

                if "display_name" in changes:
                    profile.title = changes["display_name"]
                    old_metadata["name"] = changes["display_name"]
                if "personality" in changes:
                    old_metadata["personality"] = changes["personality"]
                if "background" in changes:
                    old_metadata["background"] = changes["background"]
                if "preferences" in changes:
                    old_metadata["preferences"] = changes["preferences"]

                profile.source_metadata = old_metadata

                # 重建 full_text
                full_text_parts = [
                    old_metadata.get("name", ""),
                    old_metadata.get("personality", ""),
                    old_metadata.get("background", ""),
                    old_metadata.get("preferences", ""),
                ]
                profile.full_text = " ".join(filter(None, full_text_parts)) or profile.full_text

                await session.commit()
                log.info(f"已更新用户 {target_id} 的名片，修改字段: {list(changes.keys())}")
                return {
                    "status": "updated",
                    "user_id": str(target_id),
                    "changes": changes,
                    "message": f"已更新名片的以下字段: {', '.join(changes.keys())}",
                }

    except Exception as e:
        log.error(f"改写用户 {target_id} 名片时出错: {e}", exc_info=True)
        return {"error": f"改写名片时发生错误: {str(e)}"}
