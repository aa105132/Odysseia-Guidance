# -*- coding: utf-8 -*-
"""
月月技能系统 — 自我记忆与经验沉淀

类似 Hermes 的 Skills 机制：月月可以把自己学到的东西写成技能文件，
下次遇到类似场景时自动加载，实现跨会话的自我改进。

技能格式：每个技能是一个目录，内含 SKILL.md（YAML frontmatter + Markdown 正文）。
技能目录：/app/data/skills/（持久化，容器重建不丢失）
"""

import os
import re
import logging
import shutil
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# 技能根目录（容器内路径，对应宿主机 /opt/Odysseia-Guidance/data/skills/）
SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "skills")
SKILLS_DIR = os.path.normpath(SKILLS_DIR)

# 北京时区
_BEIJING_TZ = timezone(timedelta(hours=8))

# YAML frontmatter 正则
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def _ensure_skills_dir():
    """确保技能目录存在。"""
    os.makedirs(SKILLS_DIR, exist_ok=True)


def _parse_frontmatter(content: str) -> tuple[Dict[str, str], str]:
    """解析 YAML frontmatter，返回 (metadata, body)。"""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    raw_meta = match.group(1)
    body = match.group(2).strip()

    # 简单的 YAML key: value 解析（不引入 yaml 依赖）
    # 支持 | 和 > block scalar 语法
    meta = {}
    lines_list = raw_meta.split("\n")
    i = 0
    while i < len(lines_list):
        line = lines_list[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            # 处理 YAML block scalar: | 或 >
            if value in ("|", "|-", "|+", ">", ">-", ">+"):
                block_lines = []
                i += 1
                while i < len(lines_list):
                    bl = lines_list[i]
                    if bl.strip() == "":
                        block_lines.append("")
                        i += 1
                        continue
                    if bl.startswith(" ") or bl.startswith("\t"):
                        block_lines.append(bl.strip())
                        i += 1
                        continue
                    break
                meta[key] = " ".join(bl for bl in block_lines if bl).strip()
                continue
            else:
                meta[key] = value
        i += 1

    return meta, body


def _build_frontmatter(meta: Dict[str, str]) -> str:
    """从字典构建 YAML frontmatter 字符串。"""
    lines = ["---"]
    for key in ["name", "description", "category", "created", "updated"]:
        if key in meta:
            lines.append(f"{key}: {meta[key]}")
    lines.append("---")
    return "\n".join(lines)


def _now_str() -> str:
    return datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d")


def load_all_skills() -> List[Dict[str, Any]]:
    """
    扫描技能目录，返回所有技能的元数据列表。

    Returns:
        [{"name": ..., "description": ..., "category": ..., "path": ..., "size": ...}, ...]
    """
    _ensure_skills_dir()
    skills = []

    for entry in os.listdir(SKILLS_DIR):
        skill_path = os.path.join(SKILLS_DIR, entry)
        skill_md = os.path.join(skill_path, "SKILL.md")

        if not os.path.isfile(skill_md):
            continue

        try:
            with open(skill_md, "r", encoding="utf-8") as f:
                content = f.read()
            meta, body = _parse_frontmatter(content)

            skills.append({
                "name": meta.get("name", entry),
                "description": meta.get("description", ""),
                "category": meta.get("category", "未分类"),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
                "path": entry,
                "size": len(content),
                "body_preview": body[:200] if body else "",
            })
        except Exception as e:
            log.error(f"加载技能 {entry} 失败: {e}")
            continue

    # 按名称排序
    skills.sort(key=lambda x: x["name"])
    return skills


def get_skill_index() -> str:
    """
    生成精简的技能索引，用于注入 system prompt。

    格式：
        ## 月月技能库（N 个）
        - **skill-name**: 描述 [分类]
        - ...
    """
    skills = load_all_skills()
    if not skills:
        return ""

    lines = [f"## 月月技能库（共 {len(skills)} 个）"]
    lines.append("以下是你自己总结的经验技能，遇到相关场景时应参考：")
    lines.append("")

    for s in skills:
        desc = s["description"] or "（无描述）"
        cat = s["category"]
        lines.append(f"- **{s['name']}**: {desc} [{cat}]")

    lines.append("")
    lines.append("使用 manage_skills(action='view', name='技能名') 查看完整内容。")

    return "\n".join(lines)


def get_skill_content(name: str) -> Optional[str]:
    """获取技能的完整内容。"""
    _ensure_skills_dir()

    # 尝试直接匹配目录名
    skill_path = os.path.join(SKILLS_DIR, name, "SKILL.md")
    if os.path.isfile(skill_path):
        with open(skill_path, "r", encoding="utf-8") as f:
            return f.read()

    # 尝试匹配 name 字段（frontmatter 里的 name 可能和目录名不同）
    for entry in os.listdir(SKILLS_DIR):
        entry_path = os.path.join(SKILLS_DIR, entry, "SKILL.md")
        if not os.path.isfile(entry_path):
            continue
        try:
            with open(entry_path, "r", encoding="utf-8") as f:
                content = f.read()
            meta, _ = _parse_frontmatter(content)
            if meta.get("name", "").lower() == name.lower():
                return content
        except Exception:
            continue

    return None


def create_skill(
    name: str,
    description: str,
    content: str,
    category: str = "通用",
) -> Dict[str, Any]:
    """
    创建一个新技能。

    Args:
        name: 技能名称（英文、小写、连字符，用作目录名）
        description: 一句话描述
        content: Markdown 正文（不含 frontmatter）
        category: 分类

    Returns:
        {"success": True/False, "message": "..."}
    """
    _ensure_skills_dir()

    # 清理名称
    safe_name = re.sub(r"[^a-zA-Z0-9\-_]", "-", name.strip().lower())
    safe_name = re.sub(r"-+", "-", safe_name).strip("-")

    if not safe_name:
        return {"success": False, "message": "技能名称无效，请用英文+连字符。"}

    if len(safe_name) > 64:
        return {"success": False, "message": "技能名称太长（最多64字符）。"}

    skill_dir = os.path.join(SKILLS_DIR, safe_name)
    if os.path.exists(skill_dir):
        return {"success": False, "message": f"技能 '{safe_name}' 已存在，请用 update 操作修改。"}

    os.makedirs(skill_dir, exist_ok=True)

    today = _now_str()
    meta = {
        "name": safe_name,
        "description": description,
        "category": category,
        "created": today,
        "updated": today,
    }

    full_content = f"{_build_frontmatter(meta)}\n\n{content.strip()}\n"

    skill_md = os.path.join(skill_dir, "SKILL.md")
    with open(skill_md, "w", encoding="utf-8") as f:
        f.write(full_content)

    log.info(f"技能 '{safe_name}' 已创建")
    return {
        "success": True,
        "message": f"技能 '{safe_name}' 创建成功。",
        "path": safe_name,
    }


def update_skill(
    name: str,
    content: str = "",
    description: str = "",
    category: str = "",
) -> Dict[str, Any]:
    """
    更新已有技能的内容。

    Args:
        name: 技能名称
        content: 新的正文内容（可选，不传则不改）
        description: 新的描述（可选）
        category: 新的分类（可选）

    Returns:
        {"success": True/False, "message": "..."}
    """
    _ensure_skills_dir()

    # 找到技能目录
    skill_dir = None
    for entry in os.listdir(SKILLS_DIR):
        entry_path = os.path.join(SKILLS_DIR, entry, "SKILL.md")
        if not os.path.isfile(entry_path):
            continue
        try:
            with open(entry_path, "r", encoding="utf-8") as f:
                old_content = f.read()
            meta, _ = _parse_frontmatter(old_content)
            if meta.get("name", "").lower() == name.lower() or entry.lower() == name.lower():
                skill_dir = entry
                old_meta = meta
                break
        except Exception:
            continue

    if skill_dir is None:
        return {"success": False, "message": f"技能 '{name}' 不存在。"}

    skill_md = os.path.join(SKILLS_DIR, skill_dir, "SKILL.md")

    # 更新元数据
    new_meta = dict(old_meta)
    if description:
        new_meta["description"] = description
    if category:
        new_meta["category"] = category
    new_meta["updated"] = _now_str()

    # 更新正文
    if content:
        new_body = content.strip()
    else:
        # 保留原正文
        _, old_body = _parse_frontmatter(open(skill_md, "r", encoding="utf-8").read())
        new_body = old_body

    full_content = f"{_build_frontmatter(new_meta)}\n\n{new_body}\n"

    with open(skill_md, "w", encoding="utf-8") as f:
        f.write(full_content)

    log.info(f"技能 '{name}' 已更新")
    return {
        "success": True,
        "message": f"技能 '{name}' 更新成功。",
    }


def delete_skill(name: str) -> Dict[str, Any]:
    """删除一个技能。"""
    _ensure_skills_dir()

    # 找到技能目录
    for entry in os.listdir(SKILLS_DIR):
        entry_path = os.path.join(SKILLS_DIR, entry, "SKILL.md")
        if not os.path.isfile(entry_path):
            continue
        try:
            with open(entry_path, "r", encoding="utf-8") as f:
                content = f.read()
            meta, _ = _parse_frontmatter(content)
            if meta.get("name", "").lower() == name.lower() or entry.lower() == name.lower():
                shutil.rmtree(os.path.join(SKILLS_DIR, entry))
                log.info(f"技能 '{name}' 已删除")
                return {"success": True, "message": f"技能 '{name}' 已删除。"}
        except Exception as e:
            return {"success": False, "message": f"删除失败: {e}"}

    return {"success": False, "message": f"技能 '{name}' 不存在。"}
