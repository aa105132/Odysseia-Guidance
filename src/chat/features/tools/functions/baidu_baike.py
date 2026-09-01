# -*- coding: utf-8 -*-
"""
百度百科查询工具 - 补位网络热梗/流行语（萌娘百科偏 ACG，纯网络梗覆盖不全）
用百度百科公开的 BaikeLemmaCardApi 卡片接口，返回词条摘要
"""

import asyncio
import logging
from pydantic import BaseModel, Field

import aiohttp

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)

BAIKE_CARD_API = "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
}


class BaiduBaikeParams(BaseModel):
    keyword: str = Field(..., description="要查询的词（网络热梗/流行语/人物/事件等）")
    max_length: int = Field(1500, description="摘要最大字符数 (300-3000)")


@tool_metadata(
    name="百度百科",
    description="查询百度百科。擅长网络热梗、流行语、时事人物、社会事件等泛用中文百科内容（与萌百查询互补：萌百偏二次元ACG，这个偏大众网络文化）。查梗、查热词、查新闻人物时用这个。",
    emoji="📖",
    category="搜索",
)
async def baidu_baike(
    params: BaiduBaikeParams,
    **kwargs,
) -> str:
    """查百度百科词条卡片摘要"""
    if not isinstance(params, BaiduBaikeParams):
        try:
            clean_dict = {k.strip().strip('"'): v for k, v in params.items()}
            params = BaiduBaikeParams(**clean_dict)
        except Exception as e:
            return f"参数格式错误: {e}"

    keyword = (params.keyword or "").strip()
    if not keyword:
        return "错误：keyword 不能为空"

    max_length = min(max(params.max_length, 300), 3000)

    try:
        api_params = {
            "scope": 103,
            "format": "json",
            "appid": 379020,
            "bk_key": keyword,
            "bk_length": max_length,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(
                BAIKE_CARD_API, params=api_params, headers=_HEADERS,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    return f"错误：百度百科返回 HTTP {resp.status}"
                data = await resp.json()
    except asyncio.TimeoutError:
        return "错误：百度百科请求超时，稍后再试"
    except Exception as e:
        log.warning(f"baidu_baike 异常: {e}", exc_info=True)
        return f"错误：查询百度百科失败: {e}"

    # 空结果判定：卡片接口查不到时返回的 JSON 缺 title/abstract
    title = (data.get("title") or "").strip()
    abstract = (data.get("abstract") or "").strip()
    desc = (data.get("desc") or "").strip()

    if not title and not abstract:
        return (
            f"百度百科里没有「{keyword}」的词条卡片。"
            f"如果是二次元相关内容可以试萌百查询(moegirl_lookup)，"
            f"或者用多引擎搜索(fused_search)全网搜。"
        )

    parts = [f"【百度百科 · {title}】"]
    if desc:
        parts.append(f"（{desc}）")
    parts.append("")
    parts.append(abstract if abstract else "（摘要为空）")

    url = data.get("url") or data.get("wapUrl") or ""
    if url:
        parts.append("")
        parts.append(f"词条链接: {url}")

    return "\n".join(parts)
