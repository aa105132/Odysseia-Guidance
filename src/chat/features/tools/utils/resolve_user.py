# -*- coding: utf-8 -*-

import logging
import re
from typing import List, Optional, Tuple

import discord

log = logging.getLogger(__name__)

MENTION_ID_PATTERN = re.compile(r"^<@!?(\d+)>$")


def normalize_lookup_name(name: Optional[str]) -> str:
    if not name:
        return ""
    return str(name).strip().lstrip("@").casefold()


def member_name_candidates(member: discord.abc.User) -> List[str]:
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


def resolve_member_by_username(
    members: List[discord.abc.User], username: str
) -> Tuple[Optional[discord.abc.User], Optional[str]]:
    target = normalize_lookup_name(username)
    if not target:
        return None, "未提供有效用户名。"

    exact_matches: List[discord.abc.User] = []
    fuzzy_matches: List[discord.abc.User] = []

    for member in members:
        normalized_names = [
            normalize_lookup_name(name) for name in member_name_candidates(member)
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
            f"用户名\"{username}\"匹配到多个用户：{options}。请改用更精确的用户名，或直接提供 user_id / @提及。",
        )

    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0], None

    if len(fuzzy_matches) > 1:
        options = "、".join(
            f"{m.display_name}({m.id})" for m in fuzzy_matches[:5]
        )
        return (
            None,
            f"用户名\"{username}\"模糊匹配到多个用户：{options}。请改用更精确的用户名，或直接提供 user_id / @提及。",
        )

    return None, None


async def resolve_username_to_id(
    guild: Optional[discord.Guild],
    username: str,
    **kwargs,
) -> Tuple[Optional[str], Optional[str]]:
    """
    将用户名解析为 Discord user_id 字符串。

    Returns:
        (user_id_str, error_msg) — 成功时 error_msg 为 None，失败时 user_id_str 为 None。
    """
    lookup = str(username or "").strip()
    if not lookup:
        return None, "未提供用户名。"

    mention_match = MENTION_ID_PATTERN.match(lookup)
    if mention_match:
        return mention_match.group(1), None

    if lookup.isdigit():
        return lookup, None

    if not guild:
        return None, f"当前场景无法仅通过用户名\"{lookup}\"定位用户。请提供 user_id 或 @提及。"

    candidate_map = {}

    try:
        for member in getattr(guild, "members", []) or []:
            candidate_map[member.id] = member
    except Exception:
        pass

    try:
        direct_member = guild.get_member_named(lookup)
        if direct_member:
            candidate_map[direct_member.id] = direct_member
    except Exception:
        pass

    try:
        queried_members = await guild.query_members(query=lookup, limit=25)
        for member in queried_members:
            candidate_map[member.id] = member
    except Exception as query_error:
        log.debug(f"按用户名 query_members 失败: {query_error}")

    matched_member, match_error = resolve_member_by_username(
        list(candidate_map.values()), lookup
    )
    if match_error:
        return None, match_error

    if matched_member:
        log.info(
            f"通过用户名 '{lookup}' 解析到用户 "
            f"{matched_member.display_name} ({matched_member.id})"
        )
        return str(matched_member.id), None

    return None, f"在当前服务器中找不到用户名\"{lookup}\"。请提供更精确的用户名，或直接给出 user_id / @提及。"
