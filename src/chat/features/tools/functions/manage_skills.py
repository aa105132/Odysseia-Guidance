# -*- coding: utf-8 -*-
"""
月月技能管理工具 — 让月月自己创建、查看、更新、删除技能。

技能是月月的"长期记忆"：把学到的东西写成结构化的 markdown 文件，
下次遇到类似场景时自动参考，实现跨会话的自我改进。

用法示例：
  - 列出技能：manage_skills(action="list")
  - 查看技能：manage_skills(action="view", name="skill-name")
  - 创建技能：manage_skills(action="create", name="my-skill", description="描述", content="正文")
  - 更新技能：manage_skills(action="update", name="my-skill", content="新内容")
  - 删除技能：manage_skills(action="delete", name="my-skill")
"""

import logging
from typing import Dict, Any

from src.chat.features.tools.tool_metadata import tool_metadata
from src.chat.features.skills.skill_manager import (
    load_all_skills,
    get_skill_content,
    get_skill_index,
    create_skill,
    update_skill,
    delete_skill,
)

log = logging.getLogger(__name__)


@tool_metadata(
    name="技能管理",
    description="查看、创建、更新、删除月月自己的技能/记忆（跨会话的经验沉淀）",
    emoji="🧠",
    category="系统",
)
async def manage_skills(
    action: str = "list",
    name: str = "",
    description: str = "",
    content: str = "",
    category: str = "通用",
    **kwargs,
) -> Dict[str, Any]:
    """
    月月的技能管理系统。

    技能是你自己的长期记忆——当你发现某个方法、规律或经验值得记住时，
    把它写成技能，以后遇到类似场景系统会自动提醒你参考。

    支持的操作 (action):
    - list: 列出所有技能的摘要
    - view: 查看某个技能的完整内容（需提供 name）
    - create: 创建新技能（需提供 name, description, content）
    - update: 更新已有技能（需提供 name，可选 content/description/category）
    - delete: 删除技能（需提供 name）
    - index: 获取技能索引文本（用于了解当前有哪些技能可用）

    技能命名规范：
    - 用英文、小写、连字符，如 "handle-image-request" 或 "discord-emoji-trick"
    - 名字要能看出技能是干什么的

    什么时候该创建技能：
    - 用户纠正了你的错误，你学到了正确做法
    - 你发现某个工具组合特别好用
    - 你摸索出了更好的回复风格或技巧
    - 你踩了个坑，不想再踩第二次
    - 任何你觉得"下次遇到类似情况我应该记住这个"的时刻

    Args:
        action: 操作类型
        name: 技能名称（list 操作不需要）
        description: 技能的一句话描述（create 时必填）
        content: 技能正文（create/update 时提供，Markdown 格式）
        category: 技能分类（默认"通用"）
    """
    action_normalized = str(action or "list").strip().lower()

    if action_normalized == "list":
        skills = load_all_skills()
        if not skills:
            return {
                "success": True,
                "message": "目前还没有任何技能。",
                "hint": "当你学到值得记住的东西时，用 create 操作创建第一个技能吧。",
            }

        lines = [f"共有 {len(skills)} 个技能：\n"]
        for s in skills:
            lines.append(
                f"- **{s['name']}** [{s['category']}]: {s['description']}"
            )
            if s["updated"]:
                lines.append(f"  (更新于 {s['updated']})")

        return {
            "success": True,
            "skills": skills,
            "summary": "\n".join(lines),
        }

    if action_normalized == "view":
        if not name:
            return {"error": "view 操作需要提供 name 参数。"}
        skill_content = get_skill_content(name)
        if skill_content is None:
            return {"error": f"技能 '{name}' 不存在。"}
        return {
            "success": True,
            "name": name,
            "content": skill_content,
        }

    if action_normalized == "create":
        if not name:
            return {"error": "create 操作需要提供 name 参数。"}
        if not description:
            return {"error": "create 操作需要提供 description 参数。"}
        if not content:
            return {"error": "create 操作需要提供 content 参数。"}

        result = create_skill(
            name=name,
            description=description,
            content=content,
            category=category,
        )
        return result

    if action_normalized == "update":
        if not name:
            return {"error": "update 操作需要提供 name 参数。"}
        result = update_skill(
            name=name,
            content=content,
            description=description,
            category=category,
        )
        return result

    if action_normalized == "delete":
        if not name:
            return {"error": "delete 操作需要提供 name 参数。"}
        result = delete_skill(name)
        return result

    if action_normalized == "index":
        index_text = get_skill_index()
        return {
            "success": True,
            "index": index_text or "（暂无技能）",
        }

    return {
        "error": f"未知的 action: '{action}'。支持: list, view, create, update, delete, index"
    }
