import logging
import asyncio
import re
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import discord
from discord.http import Route
from src.chat.utils.time_utils import BEIJING_TZ
from src.chat.features.tools.tool_metadata import tool_metadata

MENTION_ID_PATTERN = re.compile(r"^<@!?(\d+)>$")


def _normalize_lookup_name(name: Optional[str]) -> str:
    if not name:
        return ""
    return str(name).strip().lstrip("@").casefold()


def _member_name_candidates(member: Any) -> List[str]:
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
    members: List[Any], username: str
) -> Tuple[Optional[Any], Optional[str]]:
    target = _normalize_lookup_name(username)
    if not target:
        return None, "未提供有效用户名。"

    exact_matches: List[Any] = []
    fuzzy_matches: List[Any] = []

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
            f"{getattr(m, 'display_name', '未知')}({getattr(m, 'id', '未知')})"
            for m in exact_matches[:5]
        )
        return (
            None,
            (
                f"用户名“{username}”匹配到多个用户：{options}。"
                "请提供更精确的用户名，或直接提供 user_id / @提及。"
            ),
        )

    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0], None

    if len(fuzzy_matches) > 1:
        options = "、".join(
            f"{getattr(m, 'display_name', '未知')}({getattr(m, 'id', '未知')})"
            for m in fuzzy_matches[:5]
        )
        return (
            None,
            (
                f"用户名“{username}”模糊匹配到多个用户：{options}。"
                "请提供更精确的用户名，或直接提供 user_id / @提及。"
            ),
        )

    return None, None


def _build_empty_result() -> Dict[str, Any]:
    return {
        "channel_results": [],
        "guild_wide_results": [],
    }


def _normalize_max_results(max_results: Optional[int]) -> Optional[int]:
    """归一化结果数量：默认 500；不设置上限；<=0 表示不限制。"""
    try:
        normalized = int(max_results)
    except (TypeError, ValueError):
        return 500
    if normalized <= 0:
        return None
    return normalized


def _build_search_params(query: str, author_id: Optional[str]) -> Dict[str, str]:
    params: Dict[str, str] = {}
    normalized_query = str(query or "").strip()
    if normalized_query:
        params["content"] = normalized_query
    if author_id:
        params["author_id"] = str(author_id)
    return params


def _collect_search_guilds(
    bot: Any, current_guild: Optional[discord.Guild] = None
) -> List[discord.Guild]:
    """收集可用于跨服搜索的服务器列表（bot 所在全部服务器 + 当前上下文服务器兜底）。"""
    guild_map: Dict[int, discord.Guild] = {}

    for guild in getattr(bot, "guilds", []) or []:
        guild_id = getattr(guild, "id", None)
        if guild_id is None:
            continue
        try:
            guild_map[int(guild_id)] = guild
        except Exception:
            continue

    if current_guild is not None and getattr(current_guild, "id", None) is not None:
        guild_map[int(current_guild.id)] = current_guild

    return list(guild_map.values())


async def _resolve_target_user_id(
    target_user: str, guilds: List[discord.Guild]
) -> Tuple[Optional[str], Optional[str]]:
    lookup_input = str(target_user).strip()
    if not lookup_input:
        return None, "未提供有效用户名或用户ID。"

    mention_match = MENTION_ID_PATTERN.match(lookup_input)
    if mention_match:
        return mention_match.group(1), None

    if lookup_input.isdigit():
        return lookup_input, None

    if not guilds:
        return (
            None,
            (
                f"当前场景无法仅通过用户名“{lookup_input}”定位用户。"
                "请提供 user_id 或 @提及，或确保机器人已加入目标服务器。"
            ),
        )

    candidate_map: Dict[int, Any] = {}

    # 第一轮：仅使用缓存数据快速匹配
    for guild in guilds:
        try:
            for member in getattr(guild, "members", []) or []:
                member_id = getattr(member, "id", None)
                if member_id is not None:
                    candidate_map[int(member_id)] = member
        except Exception:
            pass

        try:
            direct_member = guild.get_member_named(lookup_input)
            if direct_member and getattr(direct_member, "id", None) is not None:
                candidate_map[int(direct_member.id)] = direct_member
        except Exception:
            pass

    matched_member, match_error = _resolve_member_by_username(
        list(candidate_map.values()), lookup_input
    )
    if matched_member and getattr(matched_member, "id", None) is not None:
        return str(matched_member.id), None
    if match_error:
        return None, match_error

    # 第二轮：跨服 query_members 补充匹配
    for guild in guilds:
        try:
            queried_members = await guild.query_members(query=lookup_input, limit=25)
        except Exception as e:
            logging.debug(
                f"在服务器 {getattr(guild, 'id', 'unknown')} 按用户名 query_members 失败: {e}"
            )
            continue

        for member in queried_members:
            member_id = getattr(member, "id", None)
            if member_id is not None:
                candidate_map[int(member_id)] = member

    matched_member, match_error = _resolve_member_by_username(
        list(candidate_map.values()), lookup_input
    )
    if match_error:
        return None, match_error

    if matched_member and getattr(matched_member, "id", None) is not None:
        return str(matched_member.id), None

    return (
        None,
        (
            f"在当前服务器中找不到用户名“{lookup_input}”。"
            "请提供更精确的用户名，或直接给出 user_id / @提及。"
        ),
    )


def _format_search_results(messages: List[Dict]) -> List[Dict[str, Any]]:
    """Helper to format messages from the search API."""
    results = []
    for message_group in messages:
        for message_data in message_group:
            if message_data.get("hit"):
                author_data = message_data.get("author", {})

                timestamp_str = message_data.get("timestamp")
                utc_dt = datetime.fromisoformat(timestamp_str)
                beijing_dt = utc_dt.astimezone(BEIJING_TZ)

                results.append(
                    {
                        "id": message_data.get("id"),
                        "author": f"{author_data.get('username', 'N/A')}#{author_data.get('discriminator', '0000')}",
                        "author_id": author_data.get("id"),
                        "content": message_data.get("content"),
                        "timestamp": beijing_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "channel_id": message_data.get("channel_id"),
                        "guild_id": message_data.get("guild_id"),
                    }
                )
    return results


@tool_metadata(
    name="历史消息",
    description="翻翻之前的聊天记录～默认会获取500条，并可按用户名/ID检索月月所在所有服务器里的发言！",
    emoji="📜",
    category="查询",
)
async def search_channel_history(
    query: str = "",
    target_user: Optional[str] = None,
    max_results: int = 500,
    **kwargs,
) -> Dict[str, Any]:
    """
    并行搜索历史消息（默认 500 条），可按关键词与用户过滤，范围覆盖月月所在全部服务器。

    [调用指南]
    - **自主决策**: 当需要全面查找信息时调用，它会搜索当前频道 + 月月所在全部服务器。
    - **默认策略**: 若未指定 `max_results`，默认获取 500 条命中消息。
    - **数量控制**: 可按需传 `max_results` 指定返回条数；传 `max_results<=0` 表示不限制条数（会持续分页直到没有更多结果）。
    - **关键词检索**: 使用 `query` 指定消息内容关键词。
    - **按用户检索**: 使用 `target_user`（支持用户名 / @提及 / 用户ID）检索该用户在月月全部服务器的发言。
    - **组合过滤**: `query` + `target_user` 可一起使用，查找某人在月月全部服务器中包含关键词的发言。

    Args:
        query (str, optional): 要在消息内容中搜索的文本。
        target_user (str, optional): 目标用户（用户名/@提及/用户ID）。
        max_results (int, optional): 返回条数，默认 500；<=0 表示不限制条数。

    Returns:
        一个字典，包含来自频道和“全部服务器”的搜索结果（已去重）。
    """
    bot = kwargs.get("bot")
    channel = kwargs.get("channel")
    guild = kwargs.get("guild") or getattr(channel, "guild", None)

    channel_id_raw = kwargs.get("channel_id") or getattr(channel, "id", None)

    if not bot:
        logging.error("机器人实例在上下文中不可用。")
        return {
            **_build_empty_result(),
            "error": True,
            "hint": "机器人实例在上下文中不可用，无法搜索历史消息。",
        }

    search_guilds = _collect_search_guilds(bot, guild)
    search_guild_ids = [int(g.id) for g in search_guilds if getattr(g, "id", None)]

    channel_id: Optional[int] = None
    if channel_id_raw is not None:
        channel_id_text = str(channel_id_raw).strip()
        if channel_id_text.isdigit():
            channel_id = int(channel_id_text)

    normalized_max_results = _normalize_max_results(max_results)

    normalized_target_user = str(target_user).strip() if target_user is not None else ""
    resolved_target_user_id: Optional[str] = None
    if normalized_target_user:
        resolved_target_user_id, resolve_error = await _resolve_target_user_id(
            normalized_target_user, search_guilds
        )
        if resolve_error:
            return {
                **_build_empty_result(),
                "error": True,
                "hint": resolve_error,
            }

    search_params = _build_search_params(query, resolved_target_user_id)
    if not search_params:
        return {
            **_build_empty_result(),
            "error": True,
            "hint": "请至少提供 query（关键词）或 target_user（用户名/@提及/用户ID）之一。",
        }

    # --- 并行执行频道和服务器搜索 ---
    # 仅当 channel_id 可用时，才执行频道搜索
    channel_scope_guild_id = search_guild_ids[0] if search_guild_ids else 0
    if channel_id:
        channel_search_task = asyncio.create_task(
            _execute_search(
                bot,
                channel_scope_guild_id,
                search_params,
                channel_id,
                normalized_max_results,
            )
        )
    else:
        channel_search_task = asyncio.create_task(
            asyncio.sleep(0, result=[])
        )  # 返回空结果

    guild_search_tasks = [
        asyncio.create_task(
            _execute_search(bot, gid, search_params, None, normalized_max_results)
        )
        for gid in search_guild_ids
    ]

    gather_tasks = [channel_search_task, *guild_search_tasks]
    gathered_results = await asyncio.gather(*gather_tasks)
    channel_results = gathered_results[0]
    guild_results: List[Dict[str, Any]] = []
    for batch in gathered_results[1:]:
        guild_results.extend(batch or [])

    # --- 合并与去重 ---
    all_channel_ids = {msg["id"] for msg in channel_results}
    unique_guild_results = [
        msg for msg in guild_results if msg["id"] not in all_channel_ids
    ]

    return {
        "channel_results": channel_results,
        "guild_wide_results": unique_guild_results,
        "searched_guild_count": len(search_guild_ids),
        "max_results": normalized_max_results,
    }


async def _execute_search(
    bot,
    guild_id: int,
    search_params: Dict[str, str],
    channel_id: Optional[int] = None,
    max_results: Optional[int] = 500,
) -> List[Dict[str, Any]]:
    """执行单次或分页消息搜索请求。"""
    try:
        if channel_id:
            route = Route(
                "GET", "/channels/{channel_id}/messages/search", channel_id=channel_id
            )
        else:
            route = Route(
                "GET", "/guilds/{guild_id}/messages/search", guild_id=guild_id
            )

        page_size = 25
        offset = 0
        results: List[Dict[str, Any]] = []
        seen_ids = set()

        while max_results is None or len(results) < max_results:
            params = dict(search_params)
            params["offset"] = offset
            data = await bot.http.request(route, params=params)
            page_results = _format_search_results(data.get("messages", []))

            if not page_results:
                break

            new_count = 0
            for message in page_results:
                message_id = message.get("id")
                if message_id in seen_ids:
                    continue
                seen_ids.add(message_id)
                results.append(message)
                new_count += 1
                if max_results is not None and len(results) >= max_results:
                    break

            # 命中不足一页或已经没有新增，提前结束分页
            if len(page_results) < page_size or new_count == 0:
                break

            offset += page_size

        return results

    except discord.Forbidden:
        scope = f"频道 {channel_id}" if channel_id else f"服务器 {guild_id}"
        logging.error(f"没有在 {scope} 中搜索消息的权限。")
        return []
    except Exception as e:
        scope = f"频道 {channel_id}" if channel_id else f"服务器 {guild_id}"
        logging.error(f"在 {scope} 中搜索时发生未知错误: {e}")
        return []


# Metadata for the tool
SEARCH_CHANNEL_HISTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "search_channel_history",
        "description": "在当前频道与月月所在全部服务器中并行搜索消息历史。默认获取500条，可按用户名/ID过滤，max_results<=0 时不限制。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要在消息内容中搜索的文本，可为空。",
                },
                "target_user": {
                    "type": "string",
                    "description": "要筛选的目标用户（用户名/@提及/用户ID），可为空。",
                },
                "max_results": {
                    "type": "integer",
                    "description": "返回条数，默认 500；<=0 表示不限制。",
                },
            },
            "required": [],
        },
    },
}
