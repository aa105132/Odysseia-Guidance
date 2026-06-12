# -*- coding: utf-8 -*-
"""
图片搜索工具

通过 OpenAI 兼容的聊天接口请求图片搜索服务，解析其 HTML 返回结果。
工具默认不把搜索结果发到频道，而是把多张候选图作为月月内部视觉参考；
当用户明确要求“把参考图发出来/给我看”时，可把解析出的图片直接发到频道。
"""

import asyncio
import base64
import html
import json
import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import aiohttp
import discord
from src.chat.config.chat_config import IMAGE_SEARCH_CONFIG
from src.chat.features.tools.tool_metadata import tool_metadata
from src.chat.features.tools.utils.discord_image_utils import fetch_image_from_url

log = logging.getLogger(__name__)


async def _get_image_search_setting(key: str) -> Any:
    """优先读取 Dashboard 数据库配置，回退到环境/启动配置。"""
    try:
        from src.chat.utils.database import chat_db_manager

        db_value = await chat_db_manager.get_global_setting(f"image_search_{key.lower()}")
        if db_value is not None and str(db_value).strip() != "":
            if key.upper() == "EXTRA_BODY":
                try:
                    parsed = json.loads(str(db_value))
                    return parsed if isinstance(parsed, dict) else {}
                except Exception:
                    return {}
            if key.upper() in {"MAX_RESULTS", "TIMEOUT_SECONDS"}:
                try:
                    return int(str(db_value).strip())
                except (TypeError, ValueError):
                    return IMAGE_SEARCH_CONFIG.get(key.upper())
            return str(db_value)
    except Exception as exc:
        log.warning("读取图片搜索配置 image_search_%s 失败: %s", key.lower(), exc)

    return IMAGE_SEARCH_CONFIG.get(key.upper())


_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif")
_IMAGE_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^\)]+)\)", re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)", re.IGNORECASE)


class _ImageHTMLParser(HTMLParser):
    """从 HTML 中提取 img/a 标签里的图片候选。"""

    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.images: List[Dict[str, str]] = []
        self._current_link: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]):
        attrs_dict = {name.lower(): (value or "") for name, value in attrs}
        tag = tag.lower()
        if tag == "a":
            href = attrs_dict.get("href", "").strip()
            self._current_link = urljoin(self.base_url, href) if href else None
            return

        if tag != "img":
            return

        raw_url = (
            attrs_dict.get("src")
            or attrs_dict.get("data-src")
            or attrs_dict.get("data-original")
            or attrs_dict.get("data-url")
            or ""
        ).strip()
        if not raw_url:
            srcset = attrs_dict.get("srcset", "").strip()
            raw_url = _pick_srcset_url(srcset)
        if not raw_url:
            return

        image_url = urljoin(self.base_url, html.unescape(raw_url))
        self.images.append(
            {
                "url": image_url,
                "title": html.unescape(attrs_dict.get("alt", "").strip()),
                "source_url": self._current_link or attrs_dict.get("data-page-url", ""),
            }
        )

    def handle_endtag(self, tag: str):
        if tag.lower() == "a":
            self._current_link = None


def _pick_srcset_url(srcset: str) -> str:
    """从 srcset 中挑选最后一个 URL，通常对应较高分辨率。"""
    if not srcset:
        return ""
    candidates = [part.strip().split(" ")[0] for part in srcset.split(",") if part.strip()]
    return candidates[-1] if candidates else ""


def _normalize_url(raw_url: str, base_url: str = "") -> str:
    text = html.unescape(str(raw_url or "").strip())
    text = text.rstrip(".,;!?)\"]'}>")
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    if base_url:
        text = urljoin(base_url, text)
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return text


def _looks_like_image_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    return path.endswith(_IMAGE_EXTENSIONS) or "/images/proxy" in path or "image" in path


def _dedupe_results(results: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for item in results:
        url = _normalize_url(str(item.get("url") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        normalized = dict(item)
        normalized["url"] = url
        deduped.append(normalized)
        if len(deduped) >= limit:
            break
    return deduped


def _extract_text_from_openai_message(message: Dict[str, Any]) -> str:
    content = message.get("content", "") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part.get("content"), str):
                parts.append(part["content"])
            elif part.get("type") in {"image_url", "input_image"}:
                image_url = part.get("image_url")
                if isinstance(image_url, dict) and image_url.get("url"):
                    parts.append(str(image_url["url"]))
                elif isinstance(image_url, str):
                    parts.append(image_url)
        return "\n".join(parts)
    return str(content or "")


def _extract_inline_image_from_message(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """兼容少数 OpenAI 网关把图片以内联 base64 放在 content parts 的情况。"""
    content = message.get("content", "") if isinstance(message, dict) else ""
    parts = content if isinstance(content, list) else []
    for part in parts:
        if not isinstance(part, dict):
            continue
        image_url = part.get("image_url")
        raw_url = ""
        if isinstance(image_url, dict):
            raw_url = str(image_url.get("url") or "")
        elif isinstance(image_url, str):
            raw_url = image_url
        if not raw_url.startswith("data:image/"):
            continue
        try:
            header, payload = raw_url.split(",", 1)
            mime = header.split(";", 1)[0].replace("data:", "") or "image/png"
            return {"data": base64.b64decode(payload), "mime_type": mime}
        except Exception:
            continue
    return None


def _parse_image_search_results(raw_text: str, *, base_url: str = "", max_results: int = 6) -> List[Dict[str, Any]]:
    """从 HTML / Markdown / JSON / 纯文本中解析图片 URL。"""
    text = str(raw_text or "")
    if not text.strip():
        return []

    candidates: List[Dict[str, Any]] = []

    # 1) JSON 兼容：服务可能直接返回数组或 {images/results/data: [...]}。
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        containers = []
        for key in ("images", "results", "data", "items"):
            value = parsed.get(key)
            if isinstance(value, list):
                containers.extend(value)
        for item in containers:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("image_url") or item.get("src")
            if isinstance(url, dict):
                url = url.get("url")
            normalized_url = _normalize_url(str(url or ""), base_url)
            if normalized_url:
                candidates.append(
                    {
                        "url": normalized_url,
                        "title": str(item.get("title") or item.get("alt") or "").strip(),
                        "source_url": str(item.get("source_url") or item.get("page_url") or "").strip(),
                    }
                )
    elif isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, str):
                normalized_url = _normalize_url(item, base_url)
                if normalized_url:
                    candidates.append({"url": normalized_url, "title": "", "source_url": ""})
            elif isinstance(item, dict):
                url = item.get("url") or item.get("image_url") or item.get("src")
                if isinstance(url, dict):
                    url = url.get("url")
                normalized_url = _normalize_url(str(url or ""), base_url)
                if normalized_url:
                    candidates.append(
                        {
                            "url": normalized_url,
                            "title": str(item.get("title") or item.get("alt") or "").strip(),
                            "source_url": str(item.get("source_url") or item.get("page_url") or "").strip(),
                        }
                    )

    # 2) HTML img 标签。
    parser = _ImageHTMLParser(base_url=base_url)
    try:
        parser.feed(text)
        candidates.extend(parser.images)
    except Exception as exc:
        log.debug("HTML 图片解析失败，继续尝试正则提取: %s", exc)

    # 3) Markdown 图片 / 链接。
    for title, url in _MARKDOWN_IMAGE_RE.findall(text):
        normalized_url = _normalize_url(url, base_url)
        if normalized_url:
            candidates.append({"url": normalized_url, "title": title.strip(), "source_url": ""})
    for title, url in _MARKDOWN_LINK_RE.findall(text):
        normalized_url = _normalize_url(url, base_url)
        if normalized_url and _looks_like_image_url(normalized_url):
            candidates.append({"url": normalized_url, "title": title.strip(), "source_url": ""})

    # 4) 纯 URL 兜底。
    for match in _IMAGE_URL_RE.finditer(text):
        normalized_url = _normalize_url(match.group(0), base_url)
        if normalized_url and _looks_like_image_url(normalized_url):
            candidates.append({"url": normalized_url, "title": "", "source_url": ""})

    return _dedupe_results(candidates, max_results)


async def _post_openai_image_search(query: str, *, max_results: int) -> Dict[str, Any]:
    api_url = str(await _get_image_search_setting("API_URL") or "").strip().rstrip("/")
    api_key = str(await _get_image_search_setting("API_KEY") or "").strip()
    model = str(await _get_image_search_setting("MODEL") or "").strip()
    timeout_seconds = max(10, int(await _get_image_search_setting("TIMEOUT_SECONDS") or 60))

    if not api_url or not api_key or not model:
        return {
            "error": True,
            "reason": "not_configured",
            "hint": "图片搜索 API 未配置。请设置 IMAGE_SEARCH_API_URL、IMAGE_SEARCH_API_KEY、IMAGE_SEARCH_MODEL。",
        }

    endpoint = f"{api_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    prompt = (
        "请搜索与下面关键词最相关的图片。"
        "必须返回 HTML 格式，优先包含 <img src=\"...\" alt=\"...\">。"
        f"最多返回 {max_results} 张图片，并保留图片原始 URL。\n\n"
        f"关键词：{query}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an image search tool. Return image results as HTML."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }

    extra_body = await _get_image_search_setting("EXTRA_BODY")
    if isinstance(extra_body, dict):
        payload.update(extra_body)

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, headers=headers, json=payload) as response:
                response_text = await response.text()
                if response.status != 200:
                    log.warning("图片搜索 API 返回错误 %s: %s", response.status, response_text[:300])
                    return {
                        "error": True,
                        "reason": "upstream_error",
                        "status": response.status,
                        "hint": f"图片搜索接口返回 HTTP {response.status}。",
                    }
                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError:
                    # 少数“兼容端口”可能直接返回 HTML。
                    return {"html": response_text, "raw_response": response_text}

                choices = data.get("choices", []) if isinstance(data, dict) else []
                message = choices[0].get("message", {}) if choices else {}
                html_text = _extract_text_from_openai_message(message)
                inline_image = _extract_inline_image_from_message(message)
                return {"html": html_text, "raw_response": data, "inline_image": inline_image}
    except asyncio.TimeoutError:
        return {"error": True, "reason": "timeout", "hint": "图片搜索接口请求超时。"}
    except Exception as exc:
        log.error("图片搜索接口请求失败: %s", exc, exc_info=True)
        return {"error": True, "reason": "request_failed", "hint": f"图片搜索接口请求失败：{exc}"}


def _build_results_html(results: List[Dict[str, Any]], query: str) -> str:
    parts = [f"<div class=\"image-search-results\" data-query=\"{html.escape(query)}\">"]
    for idx, item in enumerate(results, 1):
        url = html.escape(str(item.get("url") or ""), quote=True)
        title = html.escape(str(item.get("title") or f"图片 {idx}"))
        source = html.escape(str(item.get("source_url") or ""), quote=True)
        source_attr = f' data-source="{source}"' if source else ""
        parts.append(f'<figure{source_attr}><img src="{url}" alt="{title}"><figcaption>{title}</figcaption></figure>')
    parts.append("</div>")
    return "\n".join(parts)


@tool_metadata(
    name="图片搜索",
    description="搜索网络图片作为内部视觉参考；用户明确要求时可把参考图发到频道",
    emoji="🖼️",
    category="搜索",
)
async def image_search(
    query: str,
    max_results: int = 8,
    analyze_images: bool = True,
    max_reference_images: int = 6,
    send_to_channel: bool = False,
    max_send_images: int = 6,
    **kwargs,
) -> dict:
    """
    搜索图片并以 HTML 格式返回结果。默认只给月月内部使用；用户明确要求展示时，可把参考图直接发到频道。

    使用场景：
    - 用户要求“搜图/找图/找参考图/找某角色图片/搜索图片”。
    - 用户要求先搜索图片，再由月月分析图片内容。
    - 用户要求“把参考图发出来/给我看看/贴几张参考图”时，设置 send_to_channel=true。
    - 仅用于非频道用户的外部人物/同人角色/IP角色/现实人物/动漫小说游戏角色。
      如果用户要生成频道里的成员/群友/Discord 用户，禁止用本工具；应使用 get_user_avatar / get_user_profile。
    - 用户要求生成不熟悉的外部人物/角色/物品（例如“凡人动漫南宫婉”）时，
      可先调用本工具获取视觉参考，再调用 edit_image 或 generate_video。
    - **一次 image_search 只能搜索一个人物/角色/主体。** 多人物/多角色生成时，必须分别多次调用本工具，
      每次 query 只写一个人物名和必要作品名；禁止使用 [BATCH]、换行、|| 或把多个名字塞进同一个 query。

    Args:
        query: 图片搜索关键词。一次只能包含一个人物/角色/主体；多人物任务必须多次调用 image_search。禁止 [BATCH]、多行批量查询和 || 分隔。
        max_results: 最多解析/返回图片 URL 数量，建议 4-10，默认 8。
        analyze_images: 是否下载多张可访问图片作为多模态参考图，默认 true。
        max_reference_images: 最多下载并传给月月看的参考图数量，建议 1-6，默认 6。
        send_to_channel: 是否把解析出的参考图作为 Discord 图片消息发到当前频道。仅当用户明确要求展示参考图时设为 true。
        max_send_images: 最多发到频道的参考图数量，默认 6，最多 10。

    Returns:
        包含 HTML、图片 URL 列表、频道发送状态；如果成功下载图片，还会返回 image_data_list 给模型看图。
        后续用于图生图/图生视频时，必须由月月先分析并显式选择参考图，代码层不会自动硬传。
    """

    query = str(query or "").strip()
    if not query:
        return {"error": True, "reason": "empty_query", "hint": "缺少图片搜索关键词。"}

    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = 4
    configured_max_results = int(await _get_image_search_setting("MAX_RESULTS") or 10)
    max_results = min(max(1, max_results), configured_max_results)
    try:
        max_reference_images = int(max_reference_images)
    except (TypeError, ValueError):
        max_reference_images = 6
    max_reference_images = min(max(1, max_reference_images), max_results, 8)
    try:
        max_send_images = int(max_send_images)
    except (TypeError, ValueError):
        max_send_images = 6
    max_send_images = min(max(1, max_send_images), max_results, 10)

    upstream_result = await _post_openai_image_search(query, max_results=max_results)
    if upstream_result.get("error"):
        return upstream_result

    raw_html = str(upstream_result.get("html") or "")
    api_url = str(await _get_image_search_setting("API_URL") or "").strip()
    results = _parse_image_search_results(raw_html, base_url=api_url, max_results=max_results)

    image_data_list: List[Dict[str, Any]] = []
    inline_image = upstream_result.get("inline_image")
    if isinstance(inline_image, dict) and inline_image.get("data"):
        image_data_list.append(
            {
                "data": inline_image["data"],
                "mime_type": inline_image.get("mime_type", "image/png"),
                "source_url": "inline",
                "filename": "image_search_inline.png",
                "index": 1,
            }
        )

    if analyze_images and results:
        # 并发下载多张可访问图片，供 LLM 直接“看图”；后续是否用于生成由模型显式决定。
        candidate_results = results[:max_reference_images]

        async def _download_one(idx: int, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            image = await fetch_image_from_url(str(item.get("url") or ""))
            if not image or not image.get("data"):
                return None
            return {
                "data": image["data"],
                "mime_type": image.get("mime_type", "image/png"),
                "source_url": item.get("url"),
                "filename": image.get("filename", f"image_search_result_{idx}.png"),
                "index": idx,
            }

        downloaded = await asyncio.gather(
            *[_download_one(idx, item) for idx, item in enumerate(candidate_results, 1)],
            return_exceptions=True,
        )
        for item in downloaded:
            if isinstance(item, dict) and item.get("data"):
                image_data_list.append(item)

    image_data = image_data_list[0] if image_data_list else None

    sent_message_ids: List[int] = []
    sent_channel_id = None
    if send_to_channel and results:
        channel = kwargs.get("channel")
        message = kwargs.get("message")
        if channel:
            try:
                embeds: List[discord.Embed] = []
                for idx, item in enumerate(results[:max_send_images], 1):
                    image_url = str(item.get("url") or "").strip()
                    if not image_url:
                        continue
                    title = str(item.get("title") or f"参考图 {idx}").strip()[:200]
                    embed = discord.Embed(
                        title=f"参考图 {idx}",
                        description=title if title and title != f"参考图 {idx}" else None,
                        color=0x2B2D31,
                    )
                    embed.set_image(url=image_url)
                    source_url = str(item.get("source_url") or "").strip()
                    if source_url:
                        embed.url = source_url
                    embed.set_footer(text="图片搜索参考图 · 仅作视觉参考，生成时不要复制水印或平台文字")
                    embeds.append(embed)

                for batch_start in range(0, len(embeds), 10):
                    batch = embeds[batch_start : batch_start + 10]
                    if not batch:
                        continue
                    content = (
                        f"找到这些“{query}”的参考图，月月会先看图分析；"
                        "如果继续生成，会自动避开水印和平台文字。"
                        if batch_start == 0 else None
                    )
                    if message is not None and batch_start == 0:
                        sent = await message.reply(content=content, embeds=batch, mention_author=False)
                    else:
                        sent = await channel.send(content=content, embeds=batch)
                    sent_message_ids.append(int(sent.id))
                    sent_channel_id = int(sent.channel.id)
            except Exception as exc:
                log.warning("发送图片搜索参考图到频道失败: %s", exc, exc_info=True)

    output: Dict[str, Any] = {
        "success": True,
        "query": query,
        "html": _build_results_html(results, query) if results else raw_html,
        "results": [
            {
                "index": idx,
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "source_url": item.get("source_url", ""),
            }
            for idx, item in enumerate(results, 1)
        ],
        "image_reference_metadata": [
            {
                "index": item.get("index", idx),
                "source_url": item.get("source_url", ""),
                "filename": item.get("filename", f"image_search_result_{idx}.png"),
            }
            for idx, item in enumerate(image_data_list, 1)
        ],
        "result_count": len(results),
        "internal_only": not bool(sent_message_ids),
        "sent_to_channel": bool(sent_message_ids),
        "sent_message_ids": sent_message_ids,
        "sent_channel_id": sent_channel_id,
        "message": (
            "图片搜索结果已按用户要求作为图片发到频道；仍需由月月先分析参考图，不要原样复述 HTML。"
            if sent_message_ids else
            "图片搜索结果仅供月月内部参考，不能原样贴给用户。"
            "本次搜索结果只对应当前这一个 query 里的单个人物/主体；多人物时继续逐个调用 image_search，并记清每批编号对应的人物。"
            "请先消化 HTML/图片内容；如果原始任务是生成图片，下一步必须调用 edit_image，"
            "并显式传 image_search_reference_index 或 image_search_reference_indexes 选择参考图；"
            "edit_prompt 必须很短，只写保持参考图主体不变并修改动作/场景/构图，不要复述外观、服饰、发色、作品名或画风。"
            "禁止再调用 generate_image / generate_image_novelai / generate_image_comfyui 纯文生图。"
            "如果原始任务是图生视频，下一步必须调用 generate_video(use_reference_image=true)，并显式传搜索图编号。"
            "不要假设代码会自动传图。生成时必须去除参考图中的水印、署名、平台文字和截图 UI。"
        ),
    }

    if not results and not image_data_list:
        output.update({"success": False, "reason": "no_image_results", "hint": "搜索接口没有返回可识别的图片 URL。"})
        return output

    if image_data_list:
        output["image_data"] = image_data_list[0]
        output["image_data_list"] = image_data_list
        output["reference_image_count"] = len(image_data_list)
        output["image_reference_hint"] = (
            f"已下载 {len(image_data_list)} 张搜索图片作为内部参考图。"
            "月月需要先分析这些图的共同视觉特征，并记住这批参考图对应当前 query 的这个人物/主体；多人物/多角色时必须继续为其他外部角色逐个调用 image_search，"
            "所有搜索会在本轮累计成全局参考图编号；后续生成图片必须调用 edit_image 并显式传 "
            "image_search_reference_index 或 image_search_reference_indexes 一次性选择所有需要的参考图。edit_prompt 只写保持参考图主体不变并修改动作/场景/构图，"
            "不要复述外观、服饰、发色、作品名或画风。图生视频必须调用 generate_video 并显式传搜索图编号。"
            "不要让代码层自动硬传搜索图，也不要退回纯文生图。生成结果不要包含参考图中的水印、署名、平台文字、截图 UI 或边框。"
        )

    return output
