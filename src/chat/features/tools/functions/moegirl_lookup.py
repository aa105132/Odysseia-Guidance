# -*- coding: utf-8 -*-
"""
萌娘百科查询工具 - 直接调 mzh.moegirl.org.cn 的 MediaWiki API
提供 moegirl_lookup（搜索词条 + 取页面摘要）能力
让月月能实时查 ACG 词条：角色、作品、梗、声优等
"""

import asyncio
import logging
from typing import Optional
from pydantic import BaseModel, Field

import aiohttp

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)

# 萌娘百科镜像 API（mzh 镜像，主站对 bot UA 限制更严）
MOEGIRL_API_URL = "https://mzh.moegirl.org.cn/api.php"

# 请求头：萌百对无 UA 的请求可能限流
_HEADERS = {
    "User-Agent": "OdysseiaGuidanceBot/1.0 (Discord bot; moegirl-lookup tool)",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class MoegirlLookupParams(BaseModel):
    action: str = Field(
        "search",
        description=(
            "要执行的动作。search=按关键词搜索萌娘百科词条（返回标题列表）；"
            "summary=取指定词条的正文摘要（百科介绍）。默认 search。"
        ),
    )
    keyword: str = Field(..., description="搜索关键词或词条标题（如 初音未来 / 蔚蓝档案 / 某个梗名）")
    max_results: int = Field(5, description="search 时最大返回词条数 (1-10)")
    max_length: int = Field(3000, description="summary 时摘要最大字符数 (500-8000)")


@tool_metadata(
    name="萌百查询",
    description="查询萌娘百科（ACG维基）。能查动漫角色、动画漫画游戏作品、声优、网络梗、二次元用语等的百科介绍。当聊到二次元话题、用户提到不确定的 ACG 名词/梗/角色时用这个查证。",
    emoji="🎀",
    category="搜索",
)
async def moegirl_lookup(
    params: MoegirlLookupParams,
    **kwargs,
) -> str:
    """
    查询萌娘百科。

    action=search: 返回匹配的词条标题+链接，用于先看有哪些相关词条
    action=summary: 直接取词条的介绍摘要文本
    """
    if not isinstance(params, MoegirlLookupParams):
        try:
            clean_dict = {k.strip().strip('"'): v for k, v in params.items()}
            params = MoegirlLookupParams(**clean_dict)
        except Exception as e:
            return f"参数格式错误: {e}"

    keyword = (params.keyword or "").strip()
    if not keyword:
        return "错误：keyword 不能为空"

    try:
        if params.action == "search":
            return await _search(keyword, min(max(params.max_results, 1), 10))
        elif params.action == "summary":
            return await _summary(keyword, min(max(params.max_length, 500), 8000))
        else:
            return f"错误：未知 action '{params.action}'，只能是 search 或 summary"
    except asyncio.TimeoutError:
        return "错误：萌娘百科请求超时，稍后再试"
    except Exception as e:
        log.warning(f"moegirl_lookup 异常: {e}", exc_info=True)
        return f"错误：查询萌娘百科失败: {e}"


async def _search(keyword: str, limit: int) -> str:
    """opensearch 搜索词条"""
    api_params = {
        "action": "opensearch",
        "search": keyword,
        "limit": limit,
        "namespace": 0,
        "format": "json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            MOEGIRL_API_URL, params=api_params, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            if resp.status != 200:
                return f"错误：萌娘百科返回 HTTP {resp.status}"
            data = await resp.json()

    # opensearch 返回 [query, [titles], [descs], [urls]]
    titles = data[1] if len(data) > 1 else []
    descs = data[2] if len(data) > 2 else []
    urls = data[3] if len(data) > 3 else []

    if not titles:
        return f"萌娘百科里没搜到「{keyword}」相关的词条。可以换个说法再试。"

    lines = [f"萌娘百科搜索「{keyword}」找到 {len(titles)} 个词条："]
    for i, title in enumerate(titles):
        desc = (descs[i] or "").strip() if i < len(descs) else ""
        url = urls[i] if i < len(urls) else ""
        line = f"{i + 1}. **{title}**"
        if desc:
            line += f" — {desc[:100]}"
        if url:
            line += f"\n   链接: {url}"
        lines.append(line)
    lines.append("\n提示：用 action=summary + keyword=词条名 可以取某词条的百科介绍。")
    return "\n".join(lines)


async def _summary(keyword: str, max_length: int) -> str:
    """取词条正文摘要（引言部分，redirects 自动跳转重定向）"""
    api_params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": 1,
        "exintro": 1,
        "redirects": 1,
        "titles": keyword,
        "format": "json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            MOEGIRL_API_URL, params=api_params, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            if resp.status != 200:
                return f"错误：萌娘百科返回 HTTP {resp.status}"
            data = await resp.json()

    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return f"萌娘百科里没有「{keyword}」这个词条。"

    page = next(iter(pages.values()))
    if "missing" in page:
        return (
            f"萌娘百科里没有「{keyword}」这个词条。"
            f"可以先用 action=search 搜相近的词条名。"
        )

    title = page.get("title", keyword)
    extract = (page.get("extract") or "").strip()
    if not extract:
        return f"「{title}」词条存在但摘要为空，可以试试直接搜索。"

    if len(extract) > max_length:
        extract = extract[:max_length] + "…(已截断)"

    pageid = page.get("pageid", "")
    url = f"https://mzh.moegirl.org.cn/index.php?curid={pageid}" if pageid else ""

    result = f"【萌娘百科 · {title}】\n\n{extract}"
    if url:
        result += f"\n\n词条链接: {url}"
    return result

