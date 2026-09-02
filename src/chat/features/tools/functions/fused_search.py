# -*- coding: utf-8 -*-
"""
多引擎并行搜索工具 - 基于 search-boost MCP HTTP bridge
提供 fused_search（多引擎并行搜索）能力
"""

import logging
import json
import os
from typing import Optional, List
from pydantic import BaseModel, Field

import aiohttp

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)

# search-boost HTTP bridge 地址
BRIDGE_URL = os.environ.get("SEARCH_BOOST_BRIDGE_URL", "http://172.17.0.1:8788")


class FusedSearchParams(BaseModel):
    query: str = Field(..., description="搜索关键词。支持 site: -site: \"phrase\" A OR B 等高级语法。")
    max_results: int = Field(6, description="最大返回结果数 (1-10)")
    recency: Optional[str] = Field(None, description="时间范围: day/week/month/year")
    layer: Optional[str] = Field(None, description="搜索引擎层: free(免费引擎) 或 api(含付费引擎)")


@tool_metadata(
    name="多引擎搜索",
    description="多引擎并行网络搜索（Bing+DuckDuckGo+Tavily等），比单引擎搜索覆盖面更广。用于实时信息、技术问题、冷门知识等。",
    emoji="🔍",
    category="搜索",
)
async def fused_search(
    params: FusedSearchParams,
    **kwargs,
) -> str:
    """
    多引擎并行搜索。
    
    使用场景：
    - 用户问实时信息/新闻
    - 技术问题需要多源验证
    - 单引擎搜索找不到的信息
    - 需要交叉验证的事实
    
    返回格式：
    - 每条结果包含标题、URL、摘要、来源引擎
    - 按综合相关性排序
    """
    if not isinstance(params, FusedSearchParams):
        try:
            clean_dict = {k.strip().strip('"'): v for k, v in params.items()}
            params = FusedSearchParams(**clean_dict)
        except Exception as e:
            return f"参数格式错误: {e}"

    payload = {
        "query": params.query,
        "max_results": min(max(params.max_results, 1), 10),
    }
    if params.recency:
        payload["recency"] = params.recency
    if params.layer:
        payload["layer"] = params.layer

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{BRIDGE_URL}/fused_search",
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    log.error(f"多引擎搜索 HTTP {resp.status}: {error_text[:200]}")
                    return f"搜索服务返回错误 (HTTP {resp.status})，请稍后重试。"

                data = await resp.json()

        # 解析 MCP 返回格式
        content = data.get("content", [])
        if not content:
            return "搜索未返回结果。"

        text_content = ""
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_content = item.get("text", "")
                break

        if not text_content:
            return "搜索未返回有效内容。"

        log.info(f"多引擎搜索完成: query='{params.query}', results={len(text_content)} chars")
        return text_content

    except aiohttp.ClientError as e:
        log.error(f"多引擎搜索连接失败: {e}")
        return f"搜索服务连接失败: {e}"
    except Exception as e:
        log.error(f"多引擎搜索异常: {e}", exc_info=True)
        return f"搜索异常: {e}"


class XSearchParams(BaseModel):
    query: str = Field(..., description="搜索关键词")
    type: str = Field("keyword", description="搜索类型: keyword/semantic/user/thread")
    max_results: int = Field(5, description="最大返回结果数 (1-10)")
    username: Optional[str] = Field(None, description="用户搜索时的用户名")
    post_id: Optional[str] = Field(None, description="帖子搜索时的帖子ID")


@tool_metadata(
    name="X搜索",
    description="实时 X/Twitter 搜索：关键词、语义、用户主页、帖子线程。用于查找推特上的实时讨论和观点。",
    emoji="🐦",
    category="搜索",
)
async def x_search(
    params: XSearchParams,
    **kwargs,
) -> str:
    """
    X/Twitter 实时搜索。
    
    使用场景：
    - 查找推特上的实时讨论
    - 搜索特定用户的推文
    - 查看某个话题的热门推文
    
    搜索类型：
    - keyword: 关键词搜索
    - semantic: 语义搜索
    - user: 用户主页搜索
    - thread: 帖子线程搜索
    """
    if not isinstance(params, XSearchParams):
        try:
            clean_dict = {k.strip().strip('"'): v for k, v in params.items()}
            params = XSearchParams(**clean_dict)
        except Exception as e:
            return f"参数格式错误: {e}"

    payload = {
        "query": params.query,
        "type": params.type,
        "max_results": min(max(params.max_results, 1), 10),
    }
    if params.username:
        payload["username"] = params.username
    if params.post_id:
        payload["post_id"] = params.post_id

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{BRIDGE_URL}/x_search",
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as resp:
                if resp.status != 200:
                    return f"X搜索服务返回错误 (HTTP {resp.status})。"

                data = await resp.json()

        content = data.get("content", [])
        if not content:
            return "X搜索未返回结果。"

        text_content = ""
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_content = item.get("text", "")
                break

        if not text_content:
            return "X搜索未返回有效内容。"

        log.info(f"X搜索完成: query='{params.query}', type={params.type}")
        return text_content

    except Exception as e:
        log.error(f"X搜索异常: {e}", exc_info=True)
        return f"X搜索异常: {e}"


__all__ = ["FusedSearchParams", "fused_search", "XSearchParams", "x_search"]
