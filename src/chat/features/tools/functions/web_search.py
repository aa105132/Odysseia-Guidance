# -*- coding: utf-8 -*-
"""
网络搜索工具 - 基于 Grok + Tavily 双引擎架构

参考 GrokSearch 项目 (https://github.com/GuDaStudio/GrokSearch)
- Grok: AI 驱动的智能搜索（通过 OpenAI 兼容的 chat/completions 接口）
- Tavily: 高保真网页内容抓取

配置优先级：数据库持久化配置 > 环境变量默认值
可通过 Dashboard 动态配置 API Key、URL 等参数。
"""

import asyncio
import json
import logging
import os
import re
import ipaddress
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlparse

import aiohttp

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)

# --- 搜索系统提示词 ---
SEARCH_SYSTEM_PROMPT = """You are a professional web search assistant. 
Your task is to search the web for information based on user queries and provide comprehensive, accurate, and well-structured answers.

Rules:
1. Always provide factual, up-to-date information from web sources.
2. Structure your response in clear, readable format.
3. Include relevant source URLs when available.
4. If you find conflicting information, mention different perspectives.
5. Use markdown formatting for better readability.
6. Respond in the same language as the user's query.
7. Sources are optional by default; only include them when the user explicitly asks for sources/citations or key claims need verifiable backing.

Output format:
- Provide the main answer content directly.
- When needed, list your sources in a "Sources" section using this format:
  [Source Title](URL)
- Default behavior: do not append a Sources section unless explicitly needed.
"""

FETCH_SYSTEM_PROMPT = """You are a professional web content extractor.
Your task is to fetch and extract the complete content from a given URL and return it in well-structured Markdown format.

Rules:
1. Extract ALL meaningful content from the page.
2. Preserve the original content hierarchy and formatting.
3. Convert HTML structure to proper Markdown.
4. Remove scripts, styles, ads, and non-content elements.
5. Preserve all text, links, images (as markdown), tables, and code blocks.
6. Respond in the original language of the page content.
"""


# ============================================================
# 配置管理
# ============================================================

class WebSearchConfig:
    """网络搜索配置管理器 - 支持数据库持久化和环境变量默认值"""

    # 环境变量默认值
    _ENV_DEFAULTS = {
        "grok_api_url": "GROK_API_URL",
        "grok_api_key": "GROK_API_KEY",
        "grok_model": "GROK_MODEL",
        "tavily_api_url": "TAVILY_API_URL",
        "tavily_api_key": "TAVILY_API_KEY",
    }

    def __init__(self):
        self._cache = {}

    async def _get_setting(self, key: str) -> Optional[str]:
        """从数据库获取配置，回退到环境变量"""
        try:
            from src.chat.utils.database import chat_db_manager
            db_value = await chat_db_manager.get_global_setting(f"web_search_{key}")
            if db_value:
                return db_value
        except Exception as e:
            log.warning(f"从数据库获取配置 web_search_{key} 失败: {e}")

        # 回退到环境变量
        env_var = self._ENV_DEFAULTS.get(key)
        if env_var:
            return os.getenv(env_var, "")
        return ""

    async def get_grok_api_url(self) -> str:
        return await self._get_setting("grok_api_url") or ""

    async def get_grok_api_key(self) -> str:
        return await self._get_setting("grok_api_key") or ""

    async def get_grok_model(self) -> str:
        return await self._get_setting("grok_model") or "grok-3-mini"

    async def get_tavily_api_url(self) -> str:
        return await self._get_setting("tavily_api_url") or "https://api.tavily.com"

    async def get_tavily_api_key(self) -> str:
        return await self._get_setting("tavily_api_key") or ""

    async def is_grok_configured(self) -> bool:
        url = await self.get_grok_api_url()
        key = await self.get_grok_api_key()
        return bool(url and key)

    async def is_tavily_configured(self) -> bool:
        key = await self.get_tavily_api_key()
        return bool(key)


# 全局配置实例
_config = WebSearchConfig()


def _normalize_exception_message(error: BaseException) -> str:
    """统一异常文案，避免 TimeoutError 等异常出现空字符串。"""
    text = str(error).strip()
    if text:
        return text
    return error.__class__.__name__


# ============================================================
# 时间上下文注入
# ============================================================

def _get_local_time_info() -> str:
    """获取本地时间信息，用于注入到搜索查询中"""
    try:
        local_tz = datetime.now().astimezone().tzinfo
        local_now = datetime.now(local_tz)
    except Exception:
        local_now = datetime.now(timezone.utc)

    weekdays_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays_cn[local_now.weekday()]

    return (
        f"[Current Time Context]\n"
        f"- Date: {local_now.strftime('%Y-%m-%d')} ({weekday})\n"
        f"- Time: {local_now.strftime('%H:%M:%S')}\n"
        f"- Timezone: {local_now.tzname() or 'Local'}\n"
    )


def _is_private_or_local_url(url: str) -> bool:
    """判断 URL 是否为本地/内网地址。"""
    if not url:
        return True

    try:
        parsed = urlparse(url.strip())
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        if not hostname:
            return True

        local_hosts = {"localhost", "localhost.localdomain"}
        local_suffixes = (".local", ".lan", ".internal", ".home", ".corp")
        if hostname in local_hosts or any(hostname.endswith(s) for s in local_suffixes):
            return True

        try:
            ip = ipaddress.ip_address(hostname)
            return (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            )
        except ValueError:
            return False
    except Exception:
        # 解析异常时保守处理，避免把疑似内网地址透出
        return True


# ============================================================
# Grok 搜索引擎
# ============================================================

async def _grok_search(query: str, platform: str = "", model: str = "") -> dict:
    """
    通过 Grok API（OpenAI 兼容格式）执行 AI 驱动的网络搜索。

    Returns:
        {"content": str, "sources": list[dict]}
    """
    api_url = await _config.get_grok_api_url()
    api_key = await _config.get_grok_api_key()
    effective_model = model or await _config.get_grok_model()

    if not api_url or not api_key:
        return {"content": "Grok API 未配置。请在 Dashboard 中设置 API URL 和 Key。", "sources": []}

    # 构建 platform 提示
    platform_prompt = ""
    if platform:
        platform_prompt = f"\n\nFocus your search on these platforms: {platform}\n"

    # 时间上下文
    time_context = _get_local_time_info() + "\n"

    # 构建请求
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": effective_model,
        "messages": [
            {"role": "system", "content": SEARCH_SYSTEM_PROMPT},
            {"role": "user", "content": time_context + query + platform_prompt},
        ],
        "stream": False,
    }

    endpoint = f"{api_url.rstrip('/')}/chat/completions"

    try:
        timeout = aiohttp.ClientTimeout(total=120, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, headers=headers, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    log.error(f"Grok API 返回错误 {response.status}: {error_text[:200]}")
                    return {
                        "content": f"搜索请求失败 (HTTP {response.status})。请检查 API 配置。",
                        "sources": [],
                    }

                data = await response.json()
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                    # 尝试分离内容和信源
                    answer, sources = _split_answer_and_sources(content)
                    return {"content": answer, "sources": sources}

                return {"content": "搜索未返回结果。", "sources": []}

    except aiohttp.ClientError as e:
        error_text = _normalize_exception_message(e)
        log.error(f"Grok 搜索请求失败: {error_text}")
        return {"content": f"搜索请求网络错误: {error_text}", "sources": []}
    except Exception as e:
        error_text = _normalize_exception_message(e)
        log.error(f"Grok 搜索异常: {error_text}", exc_info=True)
        return {"content": f"搜索过程中发生错误: {error_text}", "sources": []}


def _split_answer_and_sources(content: str) -> tuple:
    """
    从 Grok 回答中分离正文和信源。

    Returns:
        (answer: str, sources: list[dict])
    """
    sources = []
    answer = content

    # 尝试找到 Sources / 信源 / 参考 / References 部分
    import re
    source_patterns = [
        r'\n(?:#{1,3}\s*)?(?:Sources?|信源|参考|References?|来源)\s*(?::|\n)',
        r'\n---\n',  # 分隔线后的链接列表
    ]

    split_pos = -1
    for pattern in source_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            split_pos = match.start()
            break

    if split_pos > 0:
        answer = content[:split_pos].strip()
        source_text = content[split_pos:]

        # 提取 Markdown 链接 [title](url)
        link_pattern = r'\[([^\]]*)\]\((https?://[^\)]+)\)'
        for match in re.finditer(link_pattern, source_text):
            cleaned_url = match.group(2).strip().rstrip(".,;:!?")
            if cleaned_url and not _is_private_or_local_url(cleaned_url):
                sources.append({
                    "title": match.group(1),
                    "url": cleaned_url,
                })

        # 提取裸 URL
        if not sources:
            url_pattern = r'(https?://[^\s\)]+)'
            for match in re.finditer(url_pattern, source_text):
                cleaned_url = match.group(1).strip().rstrip(".,;:!?")
                if cleaned_url and not _is_private_or_local_url(cleaned_url):
                    sources.append({
                        "title": "",
                        "url": cleaned_url,
                    })

    return answer, sources


# ============================================================
# Tavily 搜索引擎（补充信源）
# ============================================================

def _should_enable_tavily(use_tavily: bool, detail_level: str) -> bool:
    if use_tavily:
        return True

    normalized_level = (detail_level or '').strip().lower()
    deep_levels = {
        'detail',
        'detailed',
        'deep',
        'advanced',
        'research',
        'full',
        '详细',
        '深度',
    }
    return normalized_level in deep_levels


def _build_query_list(query: str, batch_queries: str, max_queries: int = 8) -> List[str]:
    query_list: List[str] = []

    def add_query(raw_query: str):
        normalized_query = (raw_query or '').strip()
        if normalized_query and normalized_query not in query_list:
            query_list.append(normalized_query)

    add_query(query)

    batch_text = (batch_queries or '').strip()
    if not batch_text:
        return query_list[:max_queries]

    parsed_queries: List[str] = []
    if batch_text.startswith('[') and batch_text.endswith(']'):
        try:
            data = json.loads(batch_text)
            if isinstance(data, list):
                parsed_queries = [str(item).strip() for item in data]
        except Exception:
            parsed_queries = []

    if not parsed_queries:
        parsed_queries = [line.strip().lstrip('- ') for line in batch_text.splitlines()]

    for current_query in parsed_queries:
        add_query(current_query)
        if len(query_list) >= max_queries:
            break

    return query_list[:max_queries]


def _parse_query_flags(raw_query: str) -> tuple[str, bool, str]:
    text = (raw_query or '').strip()
    if not text:
        return '', False, ''

    force_tavily = False
    force_batch = False

    while text.startswith('['):
        upper_text = text.upper()
        matched = False
        for flag in ('[DEEP]', '[TAVILY]', '[BATCH]'):
            if upper_text.startswith(flag):
                matched = True
                if flag in ('[DEEP]', '[TAVILY]'):
                    force_tavily = True
                if flag == '[BATCH]':
                    force_batch = True
                text = text[len(flag) :].lstrip()
                break
        if not matched:
            break

    if '||' in text:
        segments = [segment.strip() for segment in text.split('||') if segment.strip()]
        if not segments:
            return '', force_tavily, ''
        head_query = segments[0]
        tail_queries = '\n'.join(segments[1:])
        return head_query, force_tavily, tail_queries

    if force_batch:
        lines = [line.strip().lstrip('- ') for line in text.splitlines() if line.strip()]
        if not lines:
            return '', force_tavily, ''
        head_query = lines[0]
        tail_queries = '\n'.join(lines[1:])
        return head_query, force_tavily, tail_queries

    return text, force_tavily, ''


async def _tavily_search(query: str, max_results: int = 5) -> list:
    """通过 Tavily API 执行搜索，获取补充信源"""
    api_key = await _config.get_tavily_api_key()
    api_url = await _config.get_tavily_api_url()

    if not api_key:
        return []

    endpoint = f"{api_url.rstrip('/')}/search"
    headers = {
        "Content-Type": "application/json",
    }
    body = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_raw_content": False,
        "include_answer": False,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=90, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, headers=headers, json=body) as response:
                if response.status != 200:
                    log.warning(f"Tavily 搜索失败 (HTTP {response.status})")
                    return []

                data = await response.json()
                results = data.get("results", [])
                return [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "content": r.get("content", ""),
                        "score": r.get("score", 0),
                    }
                    for r in results
                ]

    except Exception as e:
        log.warning(f"Tavily 搜索异常: {_normalize_exception_message(e)}")
        return []


async def _tavily_extract(url: str) -> Optional[str]:
    """通过 Tavily Extract API 抓取网页内容"""
    api_key = await _config.get_tavily_api_key()
    api_url = await _config.get_tavily_api_url()

    if not api_key:
        log.warning("Tavily Extract 跳过：API Key 为空，请检查数据库或环境变量配置")
        return None

    endpoint = f"{api_url.rstrip('/')}/extract"
    headers = {
        "Content-Type": "application/json",
    }
    body = {"api_key": api_key, "urls": [url]}

    log.info(f"Tavily Extract 请求: endpoint={endpoint}, url={url}")

    try:
        timeout = aiohttp.ClientTimeout(total=60, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, headers=headers, json=body) as response:
                if response.status != 200:
                    error_text = await response.text()
                    log.warning(
                        f"Tavily Extract 失败: HTTP {response.status}, "
                        f"endpoint={endpoint}, error={error_text[:500]}"
                    )
                    return None

                data = await response.json()
                results = data.get("results", [])
                if results:
                    content = results[0].get("raw_content", "")
                    if content and content.strip():
                        log.info(f"Tavily Extract 成功: 获取到 {len(content)} 字符内容")
                        return content
                    else:
                        log.warning("Tavily Extract 返回空内容 (raw_content 为空)")
                        return None
                else:
                    log.warning(f"Tavily Extract 返回无结果: {json.dumps(data, ensure_ascii=False)[:500]}")
                    return None

    except Exception as e:
        log.warning(f"Tavily Extract 异常: {_normalize_exception_message(e)}")
        return None


# ============================================================
# 结果格式化 - 返回结构化数据给主 AI 消化
# ============================================================

def _format_search_result(
    query: str,
    grok_result: dict,
    tavily_results: list = None,
    include_guidance: bool = True,
) -> str:
    """
    将搜索结果格式化为供主 AI 消化的结构化文本。
    主 AI（月月）会根据自己的人设风格来决定如何向用户呈现这些信息。

    Args:
        query: 原始搜索查询
        grok_result: Grok 搜索结果 {"content": str, "sources": list}
        tavily_results: Tavily 补充搜索结果列表
    """
    parts = []

    parts.append(f"[网络搜索结果 - 查询: {query}]")
    parts.append("")

    # 搜索内容
    content = grok_result.get("content", "")
    if content:
        parts.append("## 搜索结果摘要")
        parts.append(content)

    # 合并信源（过滤内网/本地链接，并去重）
    all_sources = []
    seen_urls = set()

    for source in grok_result.get("sources", []):
        raw_url = source.get("url", "") if isinstance(source, dict) else ""
        url = raw_url.strip().rstrip(".,;:!?")
        if not url or _is_private_or_local_url(url) or url in seen_urls:
            continue

        seen_urls.add(url)
        normalized = dict(source) if isinstance(source, dict) else {}
        normalized["url"] = url
        all_sources.append(normalized)

    # 添加 Tavily 补充信源（去重）
    if tavily_results:
        for r in tavily_results:
            url = (r.get("url", "") or "").strip().rstrip(".,;:!?")
            if url and (url not in seen_urls) and (not _is_private_or_local_url(url)):
                seen_urls.add(url)
                source_item = {"title": r.get("title", ""), "url": url}
                # 如果 Tavily 有内容摘要，也包含进来
                snippet = r.get("content", "").strip()
                if snippet:
                    source_item["snippet"] = snippet[:200]
                all_sources.append(source_item)

    # 格式化信源列表
    if all_sources:
        parts.append("")
        parts.append("## 信息来源")
        for i, source in enumerate(all_sources[:10], 1):
            title = source.get("title", "").strip()
            url = source.get("url", "").strip()
            snippet = source.get("snippet", "").strip() if "snippet" in source else ""
            if url:
                if title:
                    parts.append(f"{i}. [{title}]({url})")
                else:
                    parts.append(f"{i}. [来源链接]({url})")
                if snippet:
                    parts.append(f"   > {snippet}")

    parts.append("")
    parts.append(
        "[请基于以上搜索结果回答用户。"
        "消息源小节是可选项，默认不追加；"
        "仅当用户明确要求出处/链接，或关键结论需要可核验依据时，再附上消息源；"
        "若附加消息源，使用 Markdown 链接格式如 [标题](URL)，禁止编造链接。]"
    )

    return "\n".join(parts)


def _format_fetch_result(url: str, content: str) -> str:
    """将网页内容格式化为供主 AI 消化的结构化文本"""
    if not content:
        return f"[网页内容获取失败] 无法获取 {url} 的内容。"

    # 截断过长内容，给 AI 留足处理空间
    max_length = 6000
    truncated = False
    if len(content) > max_length:
        content = content[:max_length]
        truncated = True

    parts = [
        f"[网页内容获取结果 - URL: {url}]",
        "",
        content,
    ]

    if truncated:
        parts.append("")
        parts.append("[注意：网页内容过长，已截断。以上为部分内容。]")

    parts.append("")
    parts.append("[请根据以上网页内容，用你自己的风格来回答用户的问题或总结内容。]")

    return "\n".join(parts)


# ============================================================
# 工具函数 - Gemini Function Calling 入口
# ============================================================

@tool_metadata(
    name="网络搜索",
    description="搜索互联网获取实时信息、新闻、技术文档等",
    emoji="🔍",
    category="查询",
)
async def web_search(
    query: str,
    platform: str = "",
    **kwargs,
) -> str:
    """
    搜索互联网获取实时信息。默认仅使用 Grok；需要更详细信息时可按标记启用 Tavily。

    适用场景:
    - 用户询问**实时新闻**、**最新事件**、**当前信息**
    - 需要查找**技术文档**、**API 参考**、**开源项目信息**
    - 查询**产品价格**、**发布日期**、**版本信息**等时效性内容
    - 用户明确要求"搜索"、"查一下"、"帮我找"等
    - 你对当前问题的事实信息**不确定**，需要先检索再回答
    - 用户明确要求“先查资料/给出处/核对设定”后再绘图，需要先检索再写提示词
    - 酒馆/SillyTavern 相关教程

    不适用场景:
    - 日常闲聊、角色扮演、情感交流


    Args:
        query: 搜索查询语句，应为清晰、完整的自然语言问题。
               可选标记：
               - [DEEP] 或 [TAVILY]：启用 Tavily 深度补充
               - [BATCH]：开启多条查询并发搜索（按行列出子查询）
               - 也可使用 || 分隔多个查询实现并发
        platform: 可选的搜索聚焦平台，如 "Twitter", "GitHub", "Reddit"。
    """
    log.info(f"工具 'web_search' 被调用，查询: '{query}', 平台: '{platform}'")

    # 检查配置
    if not await _config.is_grok_configured():
        return "网络搜索功能未配置。请联系管理员在 Dashboard 中设置 Grok API。"

    normalized_query, force_tavily, inline_batch_queries = _parse_query_flags(query)
    extra_batch_queries = str(kwargs.get('batch_queries', '') or '').strip()
    if extra_batch_queries:
        inline_batch_queries = '\n'.join(
            [item for item in (inline_batch_queries, extra_batch_queries) if item]
        )

    query_list = _build_query_list(normalized_query, inline_batch_queries)
    if not query_list:
        return '请至少提供一条有效的搜索 query。'

    use_tavily_kw = bool(kwargs.get('use_tavily', False))
    detail_level = str(kwargs.get('detail_level', 'normal') or 'normal')
    wants_tavily = force_tavily or _should_enable_tavily(use_tavily_kw, detail_level)
    tavily_available = await _config.is_tavily_configured()
    enable_tavily = wants_tavily and tavily_available

    max_parallel_raw = kwargs.get('max_parallel', 3)
    try:
        max_parallel = int(max_parallel_raw)
    except Exception:
        max_parallel = 3
    max_parallel = max(1, min(max_parallel, 8, len(query_list)))

    # 防止“体感卡死”：为单查询与整轮搜索设置明确超时上限
    per_query_timeout_raw = kwargs.get('per_query_timeout_seconds', 45)
    total_timeout_raw = kwargs.get('total_timeout_seconds', 120)
    try:
        per_query_timeout = int(per_query_timeout_raw)
    except Exception:
        per_query_timeout = 45
    try:
        total_timeout = int(total_timeout_raw)
    except Exception:
        total_timeout = 120
    per_query_timeout = max(10, min(per_query_timeout, 180))
    total_timeout = max(20, min(total_timeout, 300))
    if total_timeout < per_query_timeout:
        total_timeout = per_query_timeout

    async def run_single(single_query: str) -> str:
        try:
            grok_task = _grok_search(single_query, platform)
            if enable_tavily:
                grok_result, tavily_results = await asyncio.wait_for(
                    asyncio.gather(
                        grok_task,
                        _tavily_search(single_query, max_results=5),
                    ),
                    timeout=per_query_timeout,
                )
            else:
                grok_result = await asyncio.wait_for(grok_task, timeout=per_query_timeout)
                tavily_results = []
            return _format_search_result(single_query, grok_result, tavily_results)
        except asyncio.TimeoutError:
            return (
                f"[网络搜索结果 - 查询: {single_query}]\n\n"
                f"搜索超时（>{per_query_timeout}s）。"
                "请缩小查询范围、减少批量子查询，或稍后重试。"
            )

    if len(query_list) == 1:
        try:
            formatted = await asyncio.wait_for(run_single(query_list[0]), timeout=total_timeout)
        except asyncio.TimeoutError:
            formatted = (
                f"[网络搜索结果 - 查询: {query_list[0]}]\n\n"
                f"搜索超时（>{total_timeout}s）。请稍后重试，或把问题拆得更具体。"
            )
        if wants_tavily and not tavily_available:
            formatted = (
                f'{formatted}\n\n[提示] 已请求详细模式，但 Tavily 未配置，本次仅使用 Grok。'
            )
    else:
        semaphore = asyncio.Semaphore(max_parallel)

        async def run_limited(single_query: str) -> tuple[str, str]:
            async with semaphore:
                try:
                    return single_query, await run_single(single_query)
                except Exception as exception:
                    return single_query, f'搜索失败: {exception}'

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(run_limited(single_query) for single_query in query_list)),
                timeout=total_timeout,
            )
        except asyncio.TimeoutError:
            results = [
                (
                    single_query,
                    (
                        f"搜索超时（>{total_timeout}s）。"
                        "请减少子查询数量或稍后重试。"
                    ),
                )
                for single_query in query_list
            ]

        tavily_status = '启用' if enable_tavily else '关闭'
        sections = [
            f'[网络批量搜索结果 - 共 {len(query_list)} 条查询]',
            f'- 并发上限: {max_parallel}',
            f'- Tavily 补充: {tavily_status}',
        ]

        if wants_tavily and not tavily_available:
            sections.append('- 提示: 已请求详细模式，但 Tavily 未配置，本次仅使用 Grok。')

        sections.append('')

        for index, (current_query, result_text) in enumerate(results, 1):
            sections.append(f'--- 搜索结果 {index}/{len(query_list)} ---')
            sections.append(f'查询: {current_query}')
            sections.append(result_text)
            sections.append('')

        formatted = '\n'.join(sections).strip()

    if not formatted or formatted.strip() == "":
        return "搜索未返回任何结果。请尝试换一种方式描述你的问题。"

    return formatted


@tool_metadata(
    name="网页内容获取",
    description="获取指定网页的完整内容并转为 Markdown 格式",
    emoji="🌐",
    category="查询",
)
async def web_fetch(
    url: str,
    **kwargs,
) -> str:
    """
    获取指定 URL 网页的完整内容，返回结构化的 Markdown 格式文本。

    适用场景:
    - 用户提供了一个 URL 链接并要求查看内容
    - 需要从特定网页提取信息
    - 阅读在线文档、文章、帖子

    Args:
        url: 要获取内容的网页 URL（必须是有效的 http/https 链接）。
    """
    log.info(f"工具 'web_fetch' 被调用，URL: '{url}'")

    # 验证 URL 格式
    if not url or not url.startswith(("http://", "https://")):
        return "请提供有效的 URL 地址（以 http:// 或 https:// 开头）。"

    # 优先使用 Tavily Extract（更可靠的内容提取）
    tavily_configured = await _config.is_tavily_configured()
    if tavily_configured:
        log.info("web_fetch: Tavily 已配置，尝试 Tavily Extract...")
        content = await _tavily_extract(url)
        if content:
            log.info("web_fetch: Tavily Extract 成功，返回内容")
            return _format_fetch_result(url, content)
        log.warning("web_fetch: Tavily Extract 未返回有效内容，尝试 Grok 回退")
    else:
        log.warning("web_fetch: Tavily 未配置（API Key 为空），跳过 Tavily Extract")

    # 回退到 Grok 获取（通过 AI 读取页面）
    grok_configured = await _config.is_grok_configured()
    if grok_configured:
        api_url = await _config.get_grok_api_url()
        api_key = await _config.get_grok_api_key()
        model = await _config.get_grok_model()

        log.info(f"web_fetch: 使用 Grok 回退，model={model}, endpoint={api_url}")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": FETCH_SYSTEM_PROMPT},
                {"role": "user", "content": f"{url}\n获取该网页内容并返回其结构化Markdown格式"},
            ],
            "stream": False,
        }
        endpoint = f"{api_url.rstrip('/')}/chat/completions"

        try:
            timeout = aiohttp.ClientTimeout(total=120, connect=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(endpoint, headers=headers, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        choices = data.get("choices", [])
                        if choices:
                            content = choices[0].get("message", {}).get("content", "")
                            if content:
                                log.info(f"web_fetch: Grok 回退成功，获取到 {len(content)} 字符内容")
                                return _format_fetch_result(url, content)
                        log.warning("web_fetch: Grok 返回 200 但无有效内容")
                    else:
                        error_text = await response.text()
                        log.warning(
                            f"web_fetch: Grok 回退失败: HTTP {response.status}, "
                            f"error={error_text[:500]}"
                        )
        except Exception as e:
            log.error(f"web_fetch: Grok 回退异常: {_normalize_exception_message(e)}")
    else:
        log.warning("web_fetch: Grok 也未配置，无可用后端")

    log.error(f"web_fetch: 所有后端均失败，URL: {url}")
    return f"无法获取 {url} 的内容。请检查 URL 是否可访问，或联系管理员配置 Tavily/Grok API。"
