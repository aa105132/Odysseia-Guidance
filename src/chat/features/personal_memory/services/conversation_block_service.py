# -*- coding: utf-8 -*-

"""最近对话块读取服务。

该服务只读取当前用户尚未总结进长期记忆的短期对话历史，供
gather_context 工具按需加载，避免每次聊天都把大段动态记忆注入 prompt。
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.future import select

from src.database.database import AsyncSessionLocal
from src.database.models import CommunityMemberProfile

log = logging.getLogger(__name__)


class ConversationBlockService:
    """读取用户最近未总结对话块。"""

    @staticmethod
    def _stringify_parts(parts: Any) -> str:
        if not isinstance(parts, list):
            return str(parts or "").strip()
        return " ".join(
            str(part).strip()
            for part in parts
            if isinstance(part, (str, int, float, bool)) and str(part).strip()
        ).strip()

    @classmethod
    def _format_history(cls, history: List[Dict[str, Any]], user_name: str) -> str:
        lines: List[str] = []
        for turn in history:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role") or "").strip().lower()
            role_label = user_name if role == "user" else "月月"
            content = cls._stringify_parts(turn.get("parts", []))
            if content:
                lines.append(f"{role_label}: {content}")
        return "\n".join(lines).strip()

    async def get_latest_block_content(
        self,
        user_id: int,
        max_entries: int = 20,
        user_name: Optional[str] = None,
    ) -> str:
        """返回当前用户最近未总结进长期记忆的对话块文本。"""
        async with AsyncSessionLocal() as session:
            stmt = select(CommunityMemberProfile).where(
                CommunityMemberProfile.discord_id == str(user_id)
            )
            result = await session.execute(stmt)
            profile = result.scalars().first()

        if not profile:
            return ""

        history = list(getattr(profile, "history", []) or [])
        if not history:
            return ""

        safe_max_entries = max(2, min(int(max_entries or 20), 40))
        display_name = str(user_name or getattr(profile, "title", None) or "用户").strip()
        formatted_history = self._format_history(history[-safe_max_entries:], display_name)
        if not formatted_history:
            return ""

        return (
            "这是你和该用户最近尚未总结进长期记忆的对话块，仅供理解当前对话连续性：\n"
            f"<latest_conversation>\n{formatted_history}\n</latest_conversation>"
        )


conversation_block_service = ConversationBlockService()
