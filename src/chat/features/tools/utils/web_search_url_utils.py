# -*- coding: utf-8 -*-
"""
网络搜索 URL 工具。

用于统一校验 Tavily 基础地址，避免误把 OpenAI/Grok 兼容端点
当成 Tavily Search/Extract 端点使用。
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

DEFAULT_TAVILY_API_URL = "https://api.tavily.com"

_SUSPICIOUS_TAVILY_HOST_KEYWORDS = ("openai", "grok")
_SUSPICIOUS_TAVILY_PATH_FRAGMENTS = ("/chat/completions", "/models")


def sanitize_tavily_api_url(
    api_url: Optional[str],
    *,
    logger: Optional[logging.Logger] = None,
) -> str:
    """清洗 Tavily 基础地址，遇到明显错误的 OpenAI/Grok 风格地址时回退默认值。"""
    normalized_url = str(api_url or "").strip()
    if not normalized_url:
        return DEFAULT_TAVILY_API_URL

    parsed = urlparse(normalized_url)
    host = parsed.netloc.lower()
    path = (parsed.path or "").lower().rstrip("/")
    path_contains_tavily = "tavily" in path
    host_contains_tavily = "tavily" in host

    is_invalid = parsed.scheme not in {"http", "https"} or not parsed.netloc
    is_openai_like_host = any(keyword in host for keyword in _SUSPICIOUS_TAVILY_HOST_KEYWORDS)
    is_openai_like_path = any(fragment in path for fragment in _SUSPICIOUS_TAVILY_PATH_FRAGMENTS)
    is_bare_v1_path = path.endswith("/v1") and not (host_contains_tavily or path_contains_tavily)

    if is_invalid or is_openai_like_host or is_openai_like_path or is_bare_v1_path:
        if logger:
            logger.warning(
                "检测到疑似错误的 Tavily API URL: '%s'，已自动回退到官方地址 %s",
                normalized_url,
                DEFAULT_TAVILY_API_URL,
            )
        return DEFAULT_TAVILY_API_URL

    return normalized_url.rstrip("/")


def is_suspicious_tavily_api_url(api_url: Optional[str]) -> bool:
    """判断 Tavily URL 是否明显像 OpenAI/Grok 兼容端点。"""
    normalized_url = str(api_url or "").strip()
    if not normalized_url:
        return False

    return sanitize_tavily_api_url(normalized_url) != normalized_url.rstrip("/")
