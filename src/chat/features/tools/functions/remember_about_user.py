# -*- coding: utf-8 -*-
"""
月月主动记忆工具 — 让月月在对话中即时记住关于用户的重要信息。

与 personal_memory_service 的自动总结不同：
  - 自动总结：每 N 轮触发一次，AI 批量总结对话历史
  - 主动记忆（本工具）：月月发现值得记住的信息时，即时追加到用户的 personal_summary

设计原则：
  - 追加，不覆盖 — 读旧摘要 → 追加新条目 → 写回
  - 不与自动总结冲突 — 写入的是同一个 personal_summary 字段
  - 月月自主决定记什么 — 不需要用户提醒
"""

import logging
from typing import Dict, Any, Optional

from src.chat.features.tools.tool_metadata import tool_metadata
from src.chat.features.personal_memory.services.personal_memory_service import (
    personal_memory_service,
)

log = logging.getLogger(__name__)


@tool_metadata(
    name="记住用户",
    description="主动记住关于某个用户的重要信息（爱好、经历、性格、重要事件等）",
    emoji="📝",
    category="用户信息",
)
async def remember_about_user(
    user_id: str = "",
    content: str = "",
    **kwargs,
) -> Dict[str, Any]:
    """
    主动记住关于某个用户的信息。

    当你在对话中发现值得跨会话记住的信息时，调用此工具把它追加到用户的长期记忆中。
    下次和这个用户聊天时，这些信息会通过 gather_context 自动出现在你的上下文里。

    **什么时候该用**:
    - 用户告诉你他的爱好、职业、经历
    - 用户提到重要的事（生日、考试、旅行、工作变动）
    - 用户纠正了你对他的误解
    - 用户表现出明显的性格特征或偏好
    - 任何你觉得"下次聊到这个人时我应该知道这个"的信息

    **什么时候不该用**:
    - 闲聊中的临时信息（"今天吃了什么"）
    - 已经记住过的信息（先读再写，别重复）
    - 对方明确说"别记"的内容

    Args:
        user_id: 要记住的用户的 Discord 数字 ID（从对话上下文获取）
        content: 要记住的内容，一句话写清楚（如"喜欢草莓大福，讨厌香菜"）
    """
    user_id_str = str(user_id or "").strip()
    if not user_id_str or not user_id_str.isdigit():
        return {"error": f"需要有效的 user_id（数字），收到: {user_id}"}

    content = str(content or "").strip()
    if not content:
        return {"error": "content 不能为空——你要记住什么？"}

    if len(content) > 500:
        content = content[:500]

    target_id = int(user_id_str)

    try:
        # 1. 读取现有摘要
        old_summary = await personal_memory_service.get_memory_summary(target_id)

        # 2. 检查是否已经记住过类似内容（简单去重）
        if content.lower() in (old_summary or "").lower():
            return {
                "success": True,
                "message": f"这条信息已经在记忆里了，不用重复记。",
                "current_summary": old_summary[:500],
            }

        # 3. 追加新条目
        timestamp_note = f"- {content}"
        if old_summary and old_summary != "该用户当前没有个人记忆摘要。":
            new_summary = f"{old_summary.rstrip()}\n{timestamp_note}"
        else:
            new_summary = timestamp_note

        # 4. 限制总长度（防止无限膨胀）
        max_chars = 2000
        if len(new_summary) > max_chars:
            # 保留最新的条目，截断旧内容
            lines = new_summary.split("\n")
            while len("\n".join(lines)) > max_chars and len(lines) > 1:
                lines.pop(0)
            new_summary = "\n".join(lines)
            log.info(f"用户 {target_id} 的记忆摘要超长，已截断保留最新条目")

        # 5. 写回
        await personal_memory_service.update_summary_manually(target_id, new_summary)

        log.info(f"月月主动为用户 {target_id} 记住了: {content[:50]}")

        return {
            "success": True,
            "message": f"已记住：{content}",
            "total_memories": len([l for l in new_summary.split("\n") if l.strip()]),
        }

    except Exception as e:
        log.error(f"remember_about_user 失败 (user_id={target_id}): {e}", exc_info=True)
        return {"error": f"记忆写入失败: {e}"}
